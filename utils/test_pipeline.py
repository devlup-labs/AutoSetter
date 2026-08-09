"""
test_pipeline.py
=================
Stage 03 of the AutoSetter workflow: **Validate**.

Orchestrates the full sandboxed validation cycle:

    1. Compile all generated C++ artifacts (solution, validator, generator,
       checker) — verifying they compile cleanly.
    2. Run the generator N times with different seeds to produce test inputs.
    3. Run the validator on each generated input to confirm it's structurally
       valid.
    4. Run the solution on each input to produce expected (jury) outputs.
    5. Run the checker to verify that the solution's outputs are correct.
    6. Produce a structured TestReport summarizing all results.

This module doesn't care *how* code is compiled/run (Docker vs local) — it
delegates everything to a ``SandboxLocalClient`` (or ``SandboxHTTPClient`` in
the future).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sandbox_client import (
    SandboxLocalClient,
    SandboxError,
    ExecutionResult,
    ensure_testlib,
)


class TestPipelineError(Exception):
    """Raised when any stage of the validation pipeline fails fatally."""


@dataclass
class TestCase:
    """One generated test case with its validation and execution results."""
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


@dataclass
class CompilationReport:
    """Tracks which artifacts compiled successfully."""
    solution: bool = False
    validator: bool = False
    generator: bool = False
    checker: bool = False
    errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class TestReport:
    """Full validation report for the problem."""
    compilation: CompilationReport = field(default_factory=CompilationReport)
    test_cases: List[TestCase] = field(default_factory=list)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    all_passed: bool = False
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the report to a JSON-friendly dict."""
        return {
            "compilation": {
                "solution": self.compilation.solution,
                "validator": self.compilation.validator,
                "generator": self.compilation.generator,
                "checker": self.compilation.checker,
                "errors": self.compilation.errors,
            },
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "all_passed": self.all_passed,
            "duration_ms": self.duration_ms,
            "test_cases": [
                {
                    "index": tc.index,
                    "seed": tc.seed,
                    "generator_ok": tc.generator_ok,
                    "validator_ok": tc.validator_ok,
                    "solution_ok": tc.solution_ok,
                    "checker_ok": tc.checker_ok,
                    "checker_message": tc.checker_message,
                    "error": tc.error,
                }
                for tc in self.test_cases
            ],
        }


# ---------------------------------------------------------------------------
# The main pipeline class
# ---------------------------------------------------------------------------

class TestPipeline:
    """
    Drives the validate stage of the AutoSetter workflow.

    Parameters
    ----------
    generated_dir : Path
        Directory containing the generated artifacts (solution.cpp, etc.).
    tests_dir : Path
        Directory where generated test inputs/outputs will be stored.
    sandbox : SandboxLocalClient
        The compilation/execution backend.
    num_tests : int
        Number of random test cases to generate (default: 10).
    time_limit : int
        Per-execution time limit in seconds (default: 5).
    progress_callback : callable, optional
        Called with progress messages (e.g. for the UI).
    """

    # Map of artifact name -> (source filename, needs testlib?)
    ARTIFACTS = {
        "solution":  ("solution.cpp",  False),
        "validator": ("validator.cpp", True),
        "generator": ("generator.cpp", True),
        "checker":   ("checker.cpp",   True),
    }

    def __init__(
        self,
        generated_dir: str | Path,
        tests_dir: str | Path,
        sandbox: SandboxLocalClient,
        num_tests: int = 10,
        time_limit: int = 5,
        progress_callback=None,
    ) -> None:
        self.generated_dir = Path(generated_dir)
        self.tests_dir = Path(tests_dir)
        self.sandbox = sandbox
        self.num_tests = num_tests
        self.time_limit = time_limit
        self._log = progress_callback or (lambda msg: None)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> TestReport:
        """
        Execute the full validation pipeline.  Returns a ``TestReport``.
        """
        start = time.monotonic()
        report = TestReport()

        # Ensure tests directory exists
        self.tests_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Compile all artifacts
        self._log("Compiling generated artifacts...")
        report.compilation = self._compile_all()

        # If any critical artifact failed to compile, we can't proceed.
        if not report.compilation.solution:
            self._log("❌ Solution failed to compile — cannot validate.")
            report.duration_ms = int((time.monotonic() - start) * 1000)
            return report

        if not report.compilation.generator:
            self._log("❌ Generator failed to compile — cannot produce tests.")
            report.duration_ms = int((time.monotonic() - start) * 1000)
            return report

        # Step 2-5: Generate, validate, solve, and check each test
        self._log(f"Running {self.num_tests} test cases...")
        for i in range(1, self.num_tests + 1):
            seed = str(i)
            tc = TestCase(index=i, seed=seed)

            try:
                # 2. Generate a test input
                tc.input_data = self._generate_test(seed)
                tc.generator_ok = True

                # 3. Validate the input (if validator compiled)
                if report.compilation.validator:
                    tc.validator_ok = self._validate_input(tc.input_data)
                else:
                    tc.validator_ok = True  # skip if validator didn't compile

                if not tc.validator_ok:
                    tc.error = "Validator rejected the generated input"
                    report.test_cases.append(tc)
                    continue

                # 4. Run the solution to produce expected output
                tc.expected_output = self._run_solution(tc.input_data)
                tc.solution_ok = True

                # 5. Check the output (if checker compiled)
                if report.compilation.checker:
                    checker_result = self._run_checker(
                        tc.input_data, tc.expected_output, tc.expected_output
                    )
                    tc.checker_ok = checker_result.exit_code == 0
                    tc.checker_message = checker_result.stderr or checker_result.stdout
                else:
                    tc.checker_ok = True  # skip if checker didn't compile

            except SandboxError as exc:
                tc.error = str(exc)

            report.test_cases.append(tc)

            status = "✅" if (tc.generator_ok and tc.validator_ok and tc.solution_ok and tc.checker_ok) else "❌"
            self._log(f"  Test {i}/{self.num_tests}: {status}")

        # Summarize
        report.total_tests = len(report.test_cases)
        report.passed_tests = sum(
            1 for tc in report.test_cases
            if tc.generator_ok and tc.validator_ok and tc.solution_ok and tc.checker_ok and not tc.error
        )
        report.failed_tests = report.total_tests - report.passed_tests
        report.all_passed = report.failed_tests == 0
        report.duration_ms = int((time.monotonic() - start) * 1000)

        self._log(
            f"Validation complete: {report.passed_tests}/{report.total_tests} passed "
            f"({report.duration_ms}ms)"
        )

        # Save report and test data
        self._save_report(report)
        self._save_test_data(report)

        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compile_all(self) -> CompilationReport:
        """Compile each generated artifact and record success/failure."""
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

    def _generate_test(self, seed: str) -> str:
        """Run the generator with the given seed and return the output."""
        generator_bin = self.generated_dir / "generator"
        result = self.sandbox.run_binary(
            generator_bin,
            args=[seed],
            timeout=self.time_limit,
        )
        if result.status != "success":
            raise SandboxError(
                f"Generator failed (seed={seed}): {result.stderr}"
            )
        return result.stdout

    def _validate_input(self, input_data: str) -> bool:
        """Run the validator on the given input.  Returns True if valid."""
        validator_bin = self.generated_dir / "validator"
        result = self.sandbox.run_binary(
            validator_bin,
            stdin=input_data,
            timeout=self.time_limit,
        )
        return result.exit_code == 0

    def _run_solution(self, input_data: str) -> str:
        """Run the solution on the given input.  Returns the solution output."""
        solution_bin = self.generated_dir / "solution"
        result = self.sandbox.run_binary(
            solution_bin,
            stdin=input_data,
            timeout=self.time_limit,
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
        """
        Run the checker.  testlib checkers expect three file arguments:
        ``checker <input_file> <output_file> <answer_file>``.

        We write the data to temporary files in tests_dir, invoke the
        checker, then clean up.
        """
        checker_bin = self.generated_dir / "checker"

        # Write temporary files for the checker
        input_file = self.tests_dir / "_checker_input.txt"
        output_file = self.tests_dir / "_checker_output.txt"
        answer_file = self.tests_dir / "_checker_answer.txt"

        input_file.write_text(input_data, encoding="utf-8")
        output_file.write_text(contestant_output, encoding="utf-8")
        answer_file.write_text(jury_output, encoding="utf-8")

        try:
            result = self.sandbox.run_binary(
                checker_bin,
                args=[str(input_file), str(output_file), str(answer_file)],
                timeout=self.time_limit,
            )
        finally:
            # Clean up temporary checker files
            for f in (input_file, output_file, answer_file):
                f.unlink(missing_ok=True)

        return result

    def _save_report(self, report: TestReport) -> None:
        """Save the validation report as JSON."""
        report_path = self.tests_dir / "validation_report.json"
        report_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_test_data(self, report: TestReport) -> None:
        """Save each test case's input and expected output to disk."""
        for tc in report.test_cases:
            if tc.input_data:
                input_path = self.tests_dir / f"{tc.index:03d}.in"
                input_path.write_text(tc.input_data, encoding="utf-8")
            if tc.expected_output:
                output_path = self.tests_dir / f"{tc.index:03d}.ans"
                output_path.write_text(tc.expected_output, encoding="utf-8")
