"""
autosetter.pipeline
===================
Validation pipeline orchestrator for generated problem artifacts.

Verifies:
1. Compilation: Checks that solution, validator, generator, and checker compile cleanly.
2. Sample Verification (Ground Truth): Runs the validator on the problem's official samples.
   If samples pass, the validator is deemed trusted.
3. Test Generation: Runs the generator N times with random seeds.
4. Input Validation: Validates generated inputs against the validator.
5. Solution Execution: Executes the reference solution to produce jury outputs.
6. Checker Verification & Probing:
   - Verifies the checker accepts the jury solution output.
   - Probes the checker with deliberately flawed outputs (empty, truncated, trailing garbage,
     perturbed values) to ensure it can reject incorrect submissions.
7. Diagnostics & Reporting: Produces `validation_report.json` and cleanly separates shippable
   tests from rejected inputs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from autosetter.config import DEFAULT_EXECUTION_TIMEOUT, DEFAULT_NUM_TESTS
from autosetter.sandbox import ExecutionResult, SandboxError, SandboxLocalClient


class PipelineError(Exception):
    """Raised when an unrecoverable failure occurs during validation."""


# Backwards compatibility alias
TestPipelineError = PipelineError

CHECKER_VERDICTS = {0: "ok", 1: "wa", 2: "pe", 3: "fail"}


def _verdict(exit_code: int) -> str:
    """Map exit code to testlib verdict label."""
    return CHECKER_VERDICTS.get(exit_code, f"exit{exit_code}")


def _looks_like_int(token: str) -> bool:
    """Check if token is an integer suitable for numeric perturbation."""
    return bool(token) and (token.lstrip("-+").isdigit())


@dataclass
class TestCase:
    """A single generated test case and its lifecycle status."""

    index: int
    seed: str
    input_data: str = ""
    expected_output: str = ""
    generator_ok: bool = False
    validator_ok: bool = False
    solution_ok: bool = False
    checker_ok: bool = False
    checker_message: str = ""
    error: str = ""

    @property
    def usable(self) -> bool:
        """Whether this test is valid, solved, verified, and shippable."""
        return (
            self.generator_ok
            and self.validator_ok
            and self.solution_ok
            and self.checker_ok
            and not self.error
            and bool(self.input_data)
            and bool(self.expected_output)
        )


@dataclass
class SampleCheck:
    """Result of running validator on an official problem statement sample."""

    index: int
    accepted: bool
    message: str = ""


@dataclass
class CheckerProbe:
    """Result of testing the checker against a deliberately faulty output."""

    name: str
    what: str
    fatal: bool
    verdict: str = ""
    rejected: bool = False
    message: str = ""


@dataclass
class CompilationReport:
    """Compilation status for all four C++ artifacts."""

    solution: bool = False
    validator: bool = False
    generator: bool = False
    checker: bool = False
    solution_wa: bool = False
    solution_brute: bool = False
    solution_tle: bool = False
    errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class TestReport:
    """Comprehensive validation summary for the problem package."""

    compilation: CompilationReport = field(default_factory=CompilationReport)
    test_cases: List[TestCase] = field(default_factory=list)
    samples: List[SampleCheck] = field(default_factory=list)
    checker_probes: List[CheckerProbe] = field(default_factory=list)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    all_passed: bool = False
    validator_trusted: bool = False
    checker_trusted: bool = False
    diagnosis: str = ""
    duration_ms: int = 0

    @property
    def usable_tests(self) -> List[TestCase]:
        """List of test cases fit to ship."""
        return [tc for tc in self.test_cases if tc.usable]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize report to a dictionary."""
        return {
            "compilation": {
                "solution": self.compilation.solution,
                "validator": self.compilation.validator,
                "generator": self.compilation.generator,
                "checker": self.compilation.checker,
                "solution_wa": self.compilation.solution_wa,
                "solution_brute": self.compilation.solution_brute,
                "solution_tle": self.compilation.solution_tle,
                "errors": self.compilation.errors,
            },
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "all_passed": self.all_passed,
            "validator_trusted": self.validator_trusted,
            "checker_trusted": self.checker_trusted,
            "diagnosis": self.diagnosis,
            "duration_ms": self.duration_ms,
            "samples": [
                {
                    "index": s.index,
                    "accepted": s.accepted,
                    "message": s.message,
                }
                for s in self.samples
            ],
            "checker_probes": [
                {
                    "name": p.name,
                    "what": p.what,
                    "fatal": p.fatal,
                    "verdict": p.verdict,
                    "rejected": p.rejected,
                    "message": p.message,
                }
                for p in self.checker_probes
            ],
            "test_cases": [
                {
                    "index": tc.index,
                    "seed": tc.seed,
                    "generator_ok": tc.generator_ok,
                    "validator_ok": tc.validator_ok,
                    "solution_ok": tc.solution_ok,
                    "checker_ok": tc.checker_ok,
                    "checker_message": tc.checker_message,
                    "usable": tc.usable,
                    "error": tc.error,
                }
                for tc in self.test_cases
            ],
        }


class TestPipeline:
    """
    Drives the validation phase of AutoSetter.

    Parameters
    ----------
    generated_dir : Path | str
        Directory containing generated source files (`solution.cpp`, etc.).
    tests_dir : Path | str
        Directory to write generated `.in` and `.ans` test cases and reports.
    sandbox : SandboxLocalClient
        Compilation and execution client.
    num_tests : int
        Number of test cases to generate.
    time_limit : int
        Per-execution timeout in seconds.
    progress_callback : Optional[Callable[[str], None]]
        Progress reporting callback.
    samples : Optional[List[Dict[str, Any]]]
        Official problem samples extracted from `problem.json`.
    """

    ARTIFACTS = {
        "solution": ("solution.cpp", False),
        "validator": ("validator.cpp", True),
        "generator": ("generator.cpp", True),
        "checker": ("checker.cpp", True),
        "solution_wa": ("solution.wa.cpp", False),
        "solution_brute": ("solution.brute.cpp", False),
        "solution_tle": ("solution.tle.cpp", False),
    }

    def __init__(
        self,
        generated_dir: str | Path,
        tests_dir: str | Path,
        sandbox: SandboxLocalClient,
        num_tests: int = DEFAULT_NUM_TESTS,
        time_limit: int = DEFAULT_EXECUTION_TIMEOUT,
        progress_callback: Optional[Callable[[str], None]] = None,
        samples: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.generated_dir = Path(generated_dir)
        self.tests_dir = Path(tests_dir)
        self.sandbox = sandbox
        self.num_tests = num_tests
        self.time_limit = time_limit
        self._log = progress_callback or (lambda msg: None)
        self.samples = samples or []

    def run(self) -> TestReport:
        """Execute the full validation pipeline."""
        start = time.monotonic()
        report = TestReport()
        self.tests_dir.mkdir(parents=True, exist_ok=True)

        # 1. Compile all artifacts
        self._log("Compiling generated artifacts...")
        report.compilation = self._compile_all()

        if not report.compilation.solution:
            self._log("❌ Solution failed to compile — cannot validate.")
            report.duration_ms = int((time.monotonic() - start) * 1000)
            return report

        if not report.compilation.generator:
            self._log("❌ Generator failed to compile — cannot produce tests.")
            report.duration_ms = int((time.monotonic() - start) * 1000)
            return report

        # 2. Verify validator against official samples (ground truth)
        if report.compilation.validator:
            report.samples = self._check_samples()
            report.validator_trusted = bool(report.samples) and all(
                s.accepted for s in report.samples
            )
            if not report.samples:
                self._log(
                    "  ⚠️  No usable samples in problem.json — validator cannot be corroborated"
                )
            elif report.validator_trusted:
                self._log(
                    f"  ✅ Validator accepts all {len(report.samples)} official sample(s)"
                )
            else:
                bad = [s for s in report.samples if not s.accepted]
                self._log(
                    f"  ❌ Validator rejects {len(bad)} official sample(s) — "
                    "validator or extracted constraints are flawed"
                )

        # 3-6. Generate, validate, solve, and check test cases
        self._log(f"Running {self.num_tests} test cases...")
        for i in range(1, self.num_tests + 1):
            seed = str(i)
            tc = TestCase(index=i, seed=seed)

            try:
                # Generate test input
                tc.input_data = self._generate_test(seed)
                tc.generator_ok = True

                # Validate test input
                if report.compilation.validator:
                    tc.validator_ok = self._validate_input(tc.input_data)
                else:
                    tc.validator_ok = True

                if not tc.validator_ok:
                    tc.error = self._blame_for_rejected_input(report)
                    report.test_cases.append(tc)
                    self._log(f"  Test {i}/{self.num_tests}: ❌ {tc.error}")
                    continue

                # Run reference solution
                tc.expected_output = self._run_solution(tc.input_data)
                tc.solution_ok = True

                # Run checker on jury answer
                if report.compilation.checker:
                    checker_result = self._run_checker(
                        tc.input_data, tc.expected_output, tc.expected_output
                    )
                    tc.checker_ok = checker_result.exit_code == 0
                    tc.checker_message = (
                        checker_result.stderr or checker_result.stdout
                    )
                else:
                    tc.checker_ok = True

            except SandboxError as exc:
                tc.error = str(exc)

            report.test_cases.append(tc)

            if not tc.error:
                status = "✅" if tc.usable else "❌"
                self._log(f"  Test {i}/{self.num_tests}: {status}")

        # 6b. Probe checker with deliberately flawed outputs
        if report.compilation.checker:
            reference = next((tc for tc in report.test_cases if tc.usable), None)
            if reference is None:
                self._log("  ⚠️  No usable test available to probe the checker")
            else:
                report.checker_probes = self._probe_checker(
                    reference.input_data, reference.expected_output
                )
                fatal_probes = [p for p in report.checker_probes if p.fatal]
                report.checker_trusted = all(p.rejected for p in fatal_probes)
                for probe in report.checker_probes:
                    mark = (
                        "✅"
                        if probe.rejected
                        else ("❌" if probe.fatal else "⚠️ ")
                    )
                    verb = (
                        "rejected"
                        if probe.rejected
                        else f"ACCEPTED ({probe.verdict})"
                    )
                    self._log(f"  {mark} checker {verb}: {probe.what}")
        else:
            report.checker_trusted = False

        # Summary calculations
        report.total_tests = len(report.test_cases)
        report.passed_tests = sum(1 for tc in report.test_cases if tc.usable)
        report.failed_tests = report.total_tests - report.passed_tests
        report.all_passed = (
            report.failed_tests == 0
            and report.total_tests > 0
            and report.validator_trusted
            and report.checker_trusted
        )
        report.diagnosis = self._diagnose(report)
        report.duration_ms = int((time.monotonic() - start) * 1000)

        self._log(
            f"Validation complete: {report.passed_tests}/{report.total_tests} passed "
            f"({report.duration_ms}ms)"
        )
        if report.diagnosis:
            self._log(f"  → {report.diagnosis}")

        self._save_report(report)
        self._save_test_data(report)
        return report

    def _compile_all(self) -> CompilationReport:
        """Compile each generated C++ artifact."""
        comp = CompilationReport()
        for name, (filename, needs_testlib) in self.ARTIFACTS.items():
            source = self.generated_dir / filename
            if not source.exists():
                comp.errors[name] = f"Source file not found: {filename}"
                self._log(f"  ⚠️  {name}: source file missing ({filename})")
                continue

            try:
                self.sandbox.compile_file(
                    source,
                    needs_testlib=needs_testlib,
                    timeout=60,
                )
                setattr(comp, name, True)
                self._log(f"  ✅ {name}: compiled successfully")
            except SandboxError as exc:
                comp.errors[name] = str(exc)
                self._log(f"  ❌ {name}: compilation failed")

        return comp

    def _check_samples(self) -> List[SampleCheck]:
        """Validate official problem samples using the validator."""
        checks: List[SampleCheck] = []
        for index, sample in enumerate(self.samples, start=1):
            text = (sample or {}).get("input") or ""
            if not text.strip():
                continue

            if not text.endswith("\n"):
                text += "\n"

            try:
                accepted = self._validate_input(text)
                message = "" if accepted else "validator rejected this sample"
            except SandboxError as exc:
                accepted, message = False, str(exc)

            checks.append(
                SampleCheck(index=index, accepted=accepted, message=message)
            )

        return checks

    def _blame_for_rejected_input(self, report: TestReport) -> str:
        """Attribute failure between generator vs validator using sample ground truth."""
        if report.validator_trusted:
            return (
                "Validator rejected the generated input; it accepts official "
                "samples, so the generator is the file at fault"
            )
        if report.samples:
            return (
                "Validator rejected the generated input, and also rejects "
                "official samples, so the validator is the file at fault"
            )
        return (
            "Validator rejected the generated input; no samples available "
            "to establish which file is wrong"
        )

    def _build_probes(self, answer: str) -> List[Tuple[str, str, bool, str]]:
        """Construct test probes: (name, description, fatal, broken_output)."""
        tokens = answer.split()
        probes: List[Tuple[str, str, bool, str]] = [
            ("empty", "an empty output file", True, ""),
        ]

        if len(tokens) >= 2:
            probes.append((
                "truncated",
                "a correct answer with its last token missing",
                True,
                " ".join(tokens[:-1]) + "\n",
            ))

        if tokens:
            probes.append((
                "trailing_garbage",
                "a correct answer with an extra token appended",
                False,
                answer.rstrip("\n") + " 999999999\n",
            ))

            if all(_looks_like_int(t) for t in tokens):
                bumped = " ".join(str(int(t) + 1) for t in tokens)
                probes.append((
                    "perturbed",
                    "a correct answer with every number increased by one",
                    True,
                    bumped + "\n",
                ))

        return probes

    def _probe_checker(self, input_data: str, answer: str) -> List[CheckerProbe]:
        """Test the checker against corrupted outputs."""
        results: List[CheckerProbe] = []
        for name, what, fatal, broken in self._build_probes(answer):
            probe = CheckerProbe(name=name, what=what, fatal=fatal)
            try:
                result = self._run_checker(input_data, broken, answer)
                probe.verdict = _verdict(result.exit_code)
                first_line = (
                    (result.stderr or result.stdout).strip().splitlines()[:1]
                )
                probe.message = first_line[0] if first_line else ""
                probe.rejected = probe.verdict in ("wa", "pe")
            except SandboxError as exc:
                probe.verdict = "error"
                probe.message = str(exc)
                probe.rejected = False

            results.append(probe)
        return results

    def _diagnose(self, report: TestReport) -> str:
        """Produce a one-line concise diagnostic summary."""
        if not report.compilation.solution:
            return "The solution does not compile; nothing downstream can be trusted."
        if not report.compilation.generator:
            return "The generator does not compile, so there are no tests."
        if not report.compilation.validator:
            return "The validator does not compile, so inputs were never validated."
        if report.samples and not report.validator_trusted:
            return (
                "The validator rejects official samples. Fix validator/constraints "
                "before trusting test cases."
            )
        if not report.samples:
            return (
                "No samples extracted to corroborate validator. Treat verdicts as unconfirmed."
            )
        if not report.compilation.checker:
            return "The checker does not compile, so outputs were never judged."
        if not report.checker_trusted:
            accepted = [
                p.name
                for p in report.checker_probes
                if p.fatal and not p.rejected
            ]
            return (
                f"The checker accepts definitely wrong outputs ({', '.join(accepted)}), "
                "so it would accept wrong contestant submissions."
            )
        if report.failed_tests:
            return (
                f"{report.failed_tests} of {report.total_tests} generated tests are "
                "unusable and have been excluded from the package."
            )
        return ""

    def _generate_test(self, seed: str) -> str:
        """Run the generator with a seed to produce test input."""
        generator_bin = self.generated_dir / "generator"
        result = self.sandbox.run_binary(
            generator_bin, args=[seed], timeout=self.time_limit
        )
        if result.status != "success":
            raise SandboxError(f"Generator failed (seed={seed}): {result.stderr}")
        return result.stdout

    def _validate_input(self, input_data: str) -> bool:
        """Run validator on given input."""
        validator_bin = self.generated_dir / "validator"
        result = self.sandbox.run_binary(
            validator_bin, stdin=input_data, timeout=self.time_limit
        )
        return result.exit_code == 0

    def _run_solution(self, input_data: str) -> str:
        """Run reference solution on input."""
        solution_bin = self.generated_dir / "solution"
        result = self.sandbox.run_binary(
            solution_bin, stdin=input_data, timeout=self.time_limit
        )
        if result.status == "timeout":
            raise SandboxError("Solution exceeded time limit")
        if result.status != "success":
            raise SandboxError(f"Solution runtime error: {result.stderr}")
        return result.stdout

    def _run_checker(
        self,
        input_data: str,
        contestant_output: str,
        jury_output: str,
    ) -> ExecutionResult:
        """Run checker comparing contestant output against jury answer."""
        checker_bin = self.generated_dir / "checker"

        input_file = self.tests_dir / "_checker_input.txt"
        output_file = self.tests_dir / "_checker_output.txt"
        answer_file = self.tests_dir / "_checker_answer.txt"

        input_file.write_text(input_data, encoding="utf-8")
        output_file.write_text(contestant_output, encoding="utf-8")
        answer_file.write_text(jury_output, encoding="utf-8")

        try:
            return self.sandbox.run_binary(
                checker_bin,
                args=[str(input_file), str(output_file), str(answer_file)],
                timeout=self.time_limit,
            )
        finally:
            for f in (input_file, output_file, answer_file):
                f.unlink(missing_ok=True)

    def _save_report(self, report: TestReport) -> None:
        """Write validation_report.json to tests directory."""
        report_path = self.tests_dir / "validation_report.json"
        report_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_test_data(self, report: TestReport) -> None:
        """Write shippable test pairs (001.in / 001.ans) and isolate rejected inputs."""
        for path in list(self.tests_dir.glob("*.in")) + list(
            self.tests_dir.glob("*.ans")
        ):
            path.unlink()

        for position, tc in enumerate(report.usable_tests, start=1):
            (self.tests_dir / f"{position:03d}.in").write_text(
                tc.input_data, encoding="utf-8"
            )
            (self.tests_dir / f"{position:03d}.ans").write_text(
                tc.expected_output, encoding="utf-8"
            )

        rejected = [tc for tc in report.test_cases if not tc.usable]
        if not rejected:
            return

        rejected_dir = self.tests_dir / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        for tc in rejected:
            if tc.input_data:
                (rejected_dir / f"{tc.index:03d}.in").write_text(
                    tc.input_data, encoding="utf-8"
                )
            if tc.expected_output:
                (rejected_dir / f"{tc.index:03d}.ans").write_text(
                    tc.expected_output, encoding="utf-8"
                )
            (rejected_dir / f"{tc.index:03d}.why").write_text(
                (
                    tc.error
                    or "did not complete generate/validate/solve/check chain"
                )
                + "\n",
                encoding="utf-8",
            )
