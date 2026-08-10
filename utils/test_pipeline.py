"""
test_pipeline.py
=================
Stage 03 of the AutoSetter workflow: **Validate**.

Orchestrates the full sandboxed validation cycle:

    1. Compile all generated C++ artifacts (solution, validator, generator,
       checker) — verifying they compile cleanly.
    2. Run the validator on the statement's official samples, which are free
       ground truth: a correct validator must accept every one of them.
    3. Run the generator N times with different seeds to produce test inputs.
    4. Run the validator on each generated input to confirm it's structurally
       valid.
    5. Run the solution on each input to produce expected (jury) outputs.
    6. Run the checker on the solution's own output (it must accept), and on
       deliberately broken outputs (it must reject).
    7. Produce a structured TestReport summarizing all results.

Two of those steps exist because of how this pipeline can lie to you.

**Steps 2 and 4 together.** When the validator rejects a generated input, one
of the two files is wrong, and on its own the failure doesn't say which. The
samples settle it: they came with the problem, so a validator that rejects them
is the broken party, and a validator that accepts them is trustworthy enough to
convict the generator. Without step 2 a rejected test is an unattributable
shrug.

**Step 6's second half.** Running the checker on the solution's own output only
ever asks whether ``x == x``. A checker whose body is ``quitf(_ok, ...)`` passes
that on every test. A checker is only tested by handing it something wrong and
requiring it to say so.

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
    VENDORED_TESTLIB,
)
from .test_plan import (
    TestShape,
    default_plan,
    looks_multitest,
    seed_invariant_modes,
    shaped_count,
)


class TestPipelineError(Exception):
    """Raised when any stage of the validation pipeline fails fatally."""


# testlib reports a checker's verdict through its exit code.
CHECKER_VERDICTS = {0: "ok", 1: "wa", 2: "pe", 3: "fail"}


def _verdict(exit_code: int) -> str:
    """Name the verdict behind a checker's exit code."""
    return CHECKER_VERDICTS.get(exit_code, f"exit{exit_code}")


def _looks_like_int(token: str) -> bool:
    """Is this token an integer, so that arithmetic on it makes sense?"""
    return bool(token) and (token.lstrip("-+").isdigit())


def _leading_count(text: str) -> Optional[int]:
    """The first integer in a test file -- `t`, on a multitest problem.

    None when the file does not begin with a bare integer, in which case this
    is not the multitest shape assumed here and nothing follows from it.
    """
    first = text.strip().split("\n", 1)[0].strip()
    return int(first) if first.isdigit() else None


def _first_lines(text: str, limit: int = 8) -> List[str]:
    """The opening lines of a compiler's complaint.

    The first error is the one that matters; everything after it is usually
    fallout. Showing a bounded number keeps a wall of template noise from
    burying the rest of the run's output.
    """
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    shown = lines[:limit]
    if len(lines) > limit:
        shown.append(f"... and {len(lines) - limit} more line(s)")
    return shown


@dataclass
class TestCase:
    """One generated test case with its validation and execution results."""
    index: int
    seed: str
    mode: str = "random"
    name: str = ""
    purpose: str = ""
    input_data: str = ""
    expected_output: str = ""
    generator_ok: bool = False
    validator_ok: bool = False
    solution_ok: bool = False
    checker_ok: bool = False
    checker_message: str = ""
    error: str = ""
    # Brute-force cross-verification. brute_ok records whether brute.cpp ran
    # to completion at all; matches_brute is the actual verdict and is a
    # three-state field on purpose: True (agrees), False (a confirmed
    # mismatch -- solution.cpp is almost certainly wrong), or None
    # (inconclusive -- brute.cpp wasn't available, or timed out on this
    # test's size, which is expected for a deliberately slow reference).
    brute_ok: bool = False
    brute_output: str = ""
    brute_error: str = ""
    matches_brute: Optional[bool] = None

    @property
    def usable(self) -> bool:
        """Is this test fit to ship?

        Anything less than the whole chain succeeding leaves a test that is
        incomplete (an input with no answer), wrong (an input the validator
        refuses), or unverified (solution.cpp disagrees with the independent
        brute-force oracle), and shipping any of those produces a broken
        package.
        """
        return (
            self.generator_ok
            and self.validator_ok
            and self.solution_ok
            and self.checker_ok
            and self.matches_brute is not False
            and not self.error
            and bool(self.input_data)
            and bool(self.expected_output)
        )


@dataclass
class SampleCheck:
    """The validator's verdict on one official sample from the statement."""
    index: int
    accepted: bool
    message: str = ""


@dataclass
class CheckerProbe:
    """One deliberately wrong output, and what the checker made of it.

    ``fatal`` marks the probes whose expected outcome is beyond argument. An
    empty file is wrong for every problem there is, so a checker that accepts
    one is broken, full stop. The advisory probes are ones where a pass is
    strongly expected but a rare problem could legitimately disagree, so they
    are reported without condemning the checker.
    """
    name: str
    what: str
    fatal: bool
    verdict: str = ""
    rejected: bool = False
    message: str = ""


@dataclass
class CompilationReport:
    """Tracks which artifacts compiled successfully."""
    solution: bool = False
    validator: bool = False
    generator: bool = False
    checker: bool = False
    brute: bool = False
    errors: Dict[str, str] = field(default_factory=dict)


@dataclass
class TestReport:
    """Full validation report for the problem."""
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
    # True only if brute.cpp compiled AND at least one test actually
    # compared solution.cpp against it AND none of those comparisons
    # disagreed. Mirrors validator_trusted/checker_trusted: a package isn't
    # fit to release just because brute.cpp exists, only if it was actually
    # used to confirm solution.cpp.
    brute_verified: bool = False
    modes_respected: bool = False
    mode_evidence: str = ""
    diagnosis: str = ""
    duration_ms: int = 0

    @property
    def usable_tests(self) -> List[TestCase]:
        """The tests fit to ship. Only these belong in a package."""
        return [tc for tc in self.test_cases if tc.usable]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the report to a JSON-friendly dict."""
        return {
            "compilation": {
                "solution": self.compilation.solution,
                "validator": self.compilation.validator,
                "generator": self.compilation.generator,
                "checker": self.compilation.checker,
                "brute": self.compilation.brute,
                "errors": self.compilation.errors,
            },
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "all_passed": self.all_passed,
            "validator_trusted": self.validator_trusted,
            "checker_trusted": self.checker_trusted,
            "brute_verified": self.brute_verified,
            "modes_respected": self.modes_respected,
            "mode_evidence": self.mode_evidence,
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
                    "name": tc.name,
                    "mode": tc.mode,
                    "purpose": tc.purpose,
                    "seed": tc.seed,
                    "generator_ok": tc.generator_ok,
                    "validator_ok": tc.validator_ok,
                    "solution_ok": tc.solution_ok,
                    "checker_ok": tc.checker_ok,
                    "checker_message": tc.checker_message,
                    "brute_ok": tc.brute_ok,
                    "matches_brute": tc.matches_brute,
                    "brute_error": tc.brute_error,
                    "usable": tc.usable,
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
    # brute.cpp is plain stdin/stdout C++, like solution.cpp -- it doesn't
    # touch testlib itself, only the generator/validator/checker do.
    ARTIFACTS = {
        "solution":  ("solution.cpp",  False),
        "validator": ("validator.cpp", True),
        "generator": ("generator.cpp", True),
        "checker":   ("checker.cpp",   True),
        "brute":     ("brute.cpp",     False),
    }

    def __init__(
        self,
        generated_dir: str | Path,
        tests_dir: str | Path,
        sandbox: SandboxLocalClient,
        num_tests: int = 10,
        time_limit: int = 5,
        brute_time_limit: int = 15,
        progress_callback=None,
        samples: Optional[List[Dict[str, Any]]] = None,
        plan: Optional[List[TestShape]] = None,
        problem: Optional[Dict[str, Any]] = None,
        multitest: Optional[bool] = None,
    ) -> None:
        self.generated_dir = Path(generated_dir)
        self.tests_dir = Path(tests_dir)
        self.sandbox = sandbox
        self.num_tests = num_tests
        self.time_limit = time_limit
        # brute.cpp is deliberately unoptimized (O(n^3) etc. is expected), so
        # it gets a longer timeout than the reference solution. A genuine
        # timeout here is treated as inconclusive, not as a failure -- see
        # _run_brute.
        self.brute_time_limit = brute_time_limit
        self._log = progress_callback or (lambda msg: None)

        # Whether one file holds several test cases decides both which shapes
        # are worth asking for and which checks can be run on them. A caller
        # may state it outright; otherwise it is read off the statement.
        self.multitest = (
            multitest if multitest is not None else looks_multitest(problem)
        )

        # Shapes, not seeds. Ten different seeds produce ten tests from the
        # middle of the input space, so nothing ever reaches a stated bound.
        # `num_tests` now controls how many uniformly random tests follow the
        # shaped ones, which are fixed by the constraints rather than a count.
        self.plan = plan if plan is not None else default_plan(
            random_tests=max(1, num_tests - shaped_count(self.multitest)),
            multitest=self.multitest,
        )
        # The "samples" list straight out of problem.json. Each entry is a dict
        # with "input" and "output" keys.
        self.samples = samples if samples is not None else (
            (problem or {}).get("samples") or []
        )

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

        # Step 2: Check the validator against the statement's own samples.
        # Free ground truth, and the only thing that can tell a broken
        # validator apart from a broken generator later on.
        if report.compilation.validator:
            report.samples = self._check_samples()
            report.validator_trusted = bool(report.samples) and all(
                s.accepted for s in report.samples
            )
            if not report.samples:
                self._log("  ⚠️  No usable samples in problem.json — the validator "
                          "cannot be corroborated")
            elif report.validator_trusted:
                self._log(f"  ✅ Validator accepts all {len(report.samples)} official sample(s)")
            else:
                bad = [s for s in report.samples if not s.accepted]
                self._log(f"  ❌ Validator rejects {len(bad)} official sample(s) — "
                          "the validator or the extracted constraints are wrong")

        # Step 3-6: Generate, validate, solve, and check each test
        # Does the generator honour its mode argument at all? Asked before the
        # tests are generated, because if the answer is no then every "shaped"
        # test below is really just another random one.
        report.modes_respected, report.mode_evidence = self._check_modes()
        if report.modes_respected:
            self._log("  ✅ Generator honours its mode argument")
        else:
            self._log(f"  ❌ {report.mode_evidence}")

        plan = self.plan
        self._log(f"Running {len(plan)} test cases...")
        for i, shape in enumerate(plan, start=1):
            tc = TestCase(
                index=i,
                seed=str(shape.seed),
                mode=shape.mode,
                name=shape.name,
                purpose=shape.purpose,
            )

            try:
                # 3. Generate a test input of this shape
                tc.input_data = self._generate_test(shape.args)
                tc.generator_ok = True

                # 4. Validate the input (if validator compiled)
                if report.compilation.validator:
                    tc.validator_ok = self._validate_input(tc.input_data)
                else:
                    tc.validator_ok = True  # skip if validator didn't compile

                if not tc.validator_ok:
                    tc.error = self._blame_for_rejected_input(report)
                    report.test_cases.append(tc)
                    self._log(f"  {tc.name:<10} ❌ {tc.error}")
                    continue

                # 5. Run the solution to produce expected output
                tc.expected_output = self._run_solution(tc.input_data)
                tc.solution_ok = True

                # 6. The checker must accept the reference solution's own
                # output. This is a real check — a special checker that
                # misreads the input format rejects the jury answer itself —
                # but it is only half of one, since it can never fail for a
                # checker that accepts everything. The other half runs once,
                # after the loop, in _probe_checker.
                if report.compilation.checker:
                    checker_result = self._run_checker(
                        tc.input_data, tc.expected_output, tc.expected_output
                    )
                    tc.checker_ok = checker_result.exit_code == 0
                    tc.checker_message = checker_result.stderr or checker_result.stdout
                else:
                    tc.checker_ok = True  # skip if checker didn't compile

                # 7. Cross-verify solution.cpp against brute.cpp, when
                # available. Every step above only ever asks solution.cpp to
                # agree with itself; this is the only one that can catch a
                # reference solution that runs cleanly and is simply wrong.
                if report.compilation.brute:
                    try:
                        tc.brute_output = self._run_brute(tc.input_data)
                        tc.brute_ok = True

                        if report.compilation.checker:
                            cmp_result = self._run_checker(
                                tc.input_data, tc.expected_output, tc.brute_output
                            )
                            tc.matches_brute = cmp_result.exit_code == 0
                        else:
                            # No checker to consult: fall back to a
                            # whitespace-tolerant literal comparison. Cruder
                            # than testlib's own comparator (no partial-credit
                            # or multiple-answer awareness), but still catches
                            # an outright wrong answer.
                            tc.matches_brute = (
                                tc.expected_output.split() == tc.brute_output.split()
                            )

                        if not tc.matches_brute:
                            tc.error = (
                                "solution.cpp disagrees with brute.cpp on this "
                                "input -- solution.cpp is most likely wrong"
                            )
                    except SandboxError as exc:
                        # A brute-force timeout at larger n is expected, not a
                        # failure of solution.cpp. Leave matches_brute as None
                        # (inconclusive) instead of penalizing the test.
                        tc.brute_error = str(exc)

            except SandboxError as exc:
                tc.error = str(exc)

            report.test_cases.append(tc)

            if tc.error:
                self._log(f"  {tc.name:<10} ❌ {tc.error}")
            else:
                status = "✅" if tc.usable else "❌"
                self._log(f"  {tc.name:<10} {status}  {tc.purpose}")

        # Step 6b: prove the checker can say no.
        # Deliberately NOT gated on tc.usable: usable also requires
        # matches_brute is not False, but probing the checker's own
        # soundness only needs a valid input and a solution output the
        # checker already accepted -- it has nothing to do with whether
        # brute.cpp agreed. A run where solution.cpp disagrees with
        # brute.cpp on every test must still be able to probe the checker,
        # or that failure gets masked behind a misleading "no usable test"
        # / checker_trusted=False default instead of the real diagnosis.
        if report.compilation.checker:
            reference = next(
                (
                    tc
                    for tc in report.test_cases
                    if tc.validator_ok
                    and tc.solution_ok
                    and tc.checker_ok
                    and bool(tc.input_data)
                    and bool(tc.expected_output)
                ),
                None,
            )
            if reference is None:
                self._log("  ⚠️  No usable test to probe the checker with")
            else:
                report.checker_probes = self._probe_checker(
                    reference.input_data, reference.expected_output
                )
                fatal = [p for p in report.checker_probes if p.fatal]
                report.checker_trusted = all(p.rejected for p in fatal)
                for probe in report.checker_probes:
                    mark = "✅" if probe.rejected else ("❌" if probe.fatal else "⚠️ ")
                    verb = "rejected" if probe.rejected else f"ACCEPTED ({probe.verdict})"
                    self._log(f"  {mark} checker {verb}: {probe.what}")
        else:
            report.checker_trusted = False

        # Summarize
        report.total_tests = len(report.test_cases)
        report.passed_tests = sum(1 for tc in report.test_cases if tc.usable)
        report.failed_tests = report.total_tests - report.passed_tests

        # brute_verified requires brute.cpp to have actually compiled *and*
        # actually been used to confirm at least one test, with zero
        # confirmed disagreements. A run where every brute-force attempt
        # timed out (matches_brute stays None throughout) is inconclusive,
        # not verified -- it must not be reported as confirmed correctness.
        report.brute_verified = bool(
            report.compilation.brute
            and any(tc.matches_brute is True for tc in report.test_cases)
            and not any(tc.matches_brute is False for tc in report.test_cases)
        )

        # `all_passed` is what the packager and the exit code key off, so it
        # has to mean "this problem is fit to ship", not merely "the tests we
        # kept happened to pass". A trustworthy validator, a checker that can
        # say no, and a solution independently confirmed against brute.cpp
        # are all part of that.
        report.all_passed = (
            report.failed_tests == 0
            and report.total_tests > 0
            and report.validator_trusted
            and report.checker_trusted
            and report.brute_verified
            # A test set that never reaches the stated bounds is not fit to
            # ship however many of its tests pass: a solution too slow at the
            # maximum sails through, and the bound may as well not exist.
            and report.modes_respected
        )
        report.diagnosis = self._diagnose(report)
        report.duration_ms = int((time.monotonic() - start) * 1000)

        self._log(
            f"Validation complete: {report.passed_tests}/{report.total_tests} passed "
            f"({report.duration_ms}ms)"
        )
        if report.diagnosis:
            self._log(f"  → {report.diagnosis}")

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
                # Print the compiler's own words. They were always captured and
                # never shown, which left the one question a reader has -- why
                # did it fail -- answered only inside a JSON file they had no
                # reason to open.
                self._log(f"  ❌ {name}: compilation failed")
                for line in _first_lines(str(exc), limit=8):
                    self._log(f"       {line}")

        self._explain_testlib_failures(comp)
        return comp

    def _explain_testlib_failures(self, comp: CompilationReport) -> None:
        """Separate "these files are broken" from "testlib does not build here".

        The validator, generator and checker are the three artifacts that
        include testlib.h. When all three fail and the ones that do not include
        it succeed, they are not three broken files -- the toolchain cannot
        compile testlib at all, and regenerating the artifacts will not help.

        A five-line probe settles which it is, so the report says so outright
        instead of leaving three identical failures to be interpreted.
        """
        testlib_users = [
            name for name, (_, needs) in self.ARTIFACTS.items() if needs
        ]
        failed = [n for n in testlib_users if not getattr(comp, n, False)]
        if len(failed) < len(testlib_users):
            return  # at least one compiled, so testlib itself is fine

        ok, message = self.sandbox.check_testlib(self.generated_dir)
        if ok:
            return

        comp.errors["testlib"] = message
        self._log("")
        self._log("  ⚠️  Every artifact that includes testlib.h failed, and a")
        self._log("      minimal testlib probe fails too — so this is the")
        self._log("      toolchain, not the generated code. Regenerating will")
        self._log("      not help until this compiles:")
        self._log(f"      compiler: {self.sandbox.compiler_version()}")
        self._log(f"      testlib:  {VENDORED_TESTLIB}")
        for line in _first_lines(message, limit=10):
            self._log(f"       {line}")
        self._log("")

    def _check_samples(self) -> List[SampleCheck]:
        """Run the validator on every official sample from the statement.

        A sample shipped with the problem is known-good input by definition, so
        this costs nothing to obtain and is the only independent evidence about
        the validator that the pipeline has.

        Samples whose "input" field is empty are skipped rather than counted as
        failures: that is a gap in the extraction, not a verdict on the
        validator.
        """
        checks: List[SampleCheck] = []

        for index, sample in enumerate(self.samples, start=1):
            text = (sample or {}).get("input") or ""
            if not text.strip():
                continue

            # Statements quote samples without the final newline that a test
            # file has to end with, and testlib's readEof() is right to insist
            # on it. Normalizing here keeps the check about the constraints
            # rather than about the transcription.
            if not text.endswith("\n"):
                text += "\n"

            try:
                accepted = self._validate_input(text)
                message = "" if accepted else "validator rejected this sample"
            except SandboxError as exc:
                accepted, message = False, str(exc)

            checks.append(SampleCheck(index=index, accepted=accepted, message=message))

        return checks

    def _blame_for_rejected_input(self, report: TestReport) -> str:
        """Say which file is at fault when the validator refuses a test.

        The generator and the validator are built from the same JSON by the
        same model, so a disagreement means one of them misread it. The samples
        decide which: a validator that accepts known-good input and rejects the
        generator's is being handed bad input.
        """
        if report.validator_trusted:
            return (
                "Validator rejected the generated input; it accepts the official "
                "samples, so the generator is the file at fault"
            )
        if report.samples:
            return (
                "Validator rejected the generated input, and it also rejects the "
                "official samples, so the validator is the file at fault"
            )
        return (
            "Validator rejected the generated input; no samples were available "
            "to establish which of the two is wrong"
        )

    # Each probe takes a correct answer and returns a broken version of it.
    # Fatal probes are the ones no problem can legitimately disagree with.
    def _build_probes(self, answer: str) -> List[tuple]:
        """Return (name, description, fatal, broken_output) for each probe."""
        tokens = answer.split()
        probes: List[tuple] = [
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
            # Advisory, because testlib already refuses unread output when a
            # checker quits _ok, so this passes for most checkers whether or
            # not their author thought about it.
            probes.append((
                "trailing_garbage",
                "a correct answer with an extra token appended",
                False,
                answer.rstrip("\n") + " 999999999\n",
            ))

            # The probe that matters. A checker that reads both files and never
            # compares them survives every other probe here -- empty and
            # truncated outputs fail on the read itself, which looks like a
            # rejection -- and this is the only one that asks whether the
            # comparison happens at all.
            #
            # Fatal, with one known way to be unfair: on a problem where
            # several answers are correct, a perturbed answer can legitimately
            # still be right. That direction of error is the safe one. It marks
            # a package "not ready" and names the probe, for a human to clear.
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
        """Hand the checker deliberately wrong outputs and record its verdicts.

        A checker is only worth anything if it can say no. Running it solely on
        the reference answer asks whether ``x == x``, which a checker whose body
        is ``quitf(_ok, ...)`` passes on every test.
        """
        results: List[CheckerProbe] = []

        for name, what, fatal, broken in self._build_probes(answer):
            probe = CheckerProbe(name=name, what=what, fatal=fatal)
            try:
                result = self._run_checker(input_data, broken, answer)
                probe.verdict = _verdict(result.exit_code)
                probe.message = (result.stderr or result.stdout).strip().splitlines()[:1]
                probe.message = probe.message[0] if probe.message else ""
                # _fail (exit 3) means the checker itself broke rather than
                # judging the submission, which is not a rejection.
                probe.rejected = probe.verdict in ("wa", "pe")
            except SandboxError as exc:
                probe.verdict = "error"
                probe.message = str(exc)
                probe.rejected = False
            results.append(probe)

        return results

    def _diagnose(self, report: TestReport) -> str:
        """One sentence naming what is wrong with this problem, if anything."""
        if not report.compilation.solution:
            return "The solution does not compile; nothing downstream can be trusted."
        if not report.compilation.generator:
            return "The generator does not compile, so there are no tests."
        if not report.compilation.validator:
            return "The validator does not compile, so no input was ever checked."
        if report.samples and not report.validator_trusted:
            return (
                "The validator rejects the problem's own samples. Fix the validator "
                "(or the extracted constraints) before trusting any test."
            )
        if not report.samples:
            return (
                "No samples were extracted, so the validator has nothing to be "
                "corroborated against. Treat every verdict below as unconfirmed."
            )
        if not report.compilation.checker:
            return "The checker does not compile, so no output was ever judged."
        if not report.checker_trusted:
            accepted = [p.name for p in report.checker_probes if p.fatal and not p.rejected]
            return (
                "The checker accepts outputs that are definitely wrong "
                f"({', '.join(accepted)}), so it would accept wrong submissions."
            )
        if not report.compilation.brute:
            return (
                "brute.cpp does not compile, so solution.cpp's correctness "
                "could not be cross-verified against an independent implementation."
            )
        mismatches = [tc for tc in report.test_cases if tc.matches_brute is False]
        if mismatches:
            idxs = ", ".join(str(tc.index) for tc in mismatches[:5])
            more = "" if len(mismatches) <= 5 else f" (+{len(mismatches) - 5} more)"
            return (
                f"solution.cpp disagrees with brute.cpp on {len(mismatches)} "
                f"test(s) (e.g. #{idxs}{more}) — solution.cpp most likely has a bug."
            )
        if not report.brute_verified:
            return (
                "brute.cpp compiled but never produced a confirmed comparison "
                "(every attempt timed out or was skipped), so solution.cpp's "
                "correctness remains unconfirmed."
            )
        if not report.modes_respected:
            return (
                "The generator ignores its mode argument, so no test deliberately "
                "reaches the problem's stated bounds. Regenerate it against the "
                "mode contract in prompts/generator.txt."
            )
        if report.failed_tests:
            return (
                f"{report.failed_tests} of {report.total_tests} generated tests are "
                "unusable and have been left out of the package."
            )
        return ""

    def _check_modes(self) -> tuple[bool, str]:
        """Does the generator actually respond to its mode argument?

        Nothing else in the pipeline can tell. Without a machine-readable
        description of the input there is no way to look at a file and decide
        whether it is "the largest legal input" -- but there is a way to find
        out whether the generator even tried.

        `min` describes exactly one input: every value at its smallest legal
        value. Ask for it twice with two different seeds. A generator that
        understands the mode returns identical bytes both times, because the
        constraints fixed the answer and the seed had nothing left to vary. A
        generator that ignored the argument returns two different files, and
        that difference is the proof.
        """
        for mode in seed_invariant_modes():
            try:
                first = self._generate_test([mode, "1"])
                second = self._generate_test([mode, "999"])
            except SandboxError as exc:
                return False, f"Generator failed on mode '{mode}': {exc}"

            if first != second:
                return False, (
                    f"Generator ignores its mode argument: '{mode}' produced "
                    "different output for two different seeds, but that mode "
                    "describes exactly one input. The shaped tests are really "
                    "just more random ones, so no test reaches a stated bound."
                )
            if not first.strip():
                return False, f"Generator produced an empty file for mode '{mode}'"

        evidence = "min and max are seed-independent, as the mode contract requires"

        if self.multitest:
            ok, budget_evidence = self._check_budget_shapes()
            if not ok:
                return False, budget_evidence
            evidence += f"; {budget_evidence}"

        return True, evidence

    def _check_budget_shapes(self) -> tuple[bool, str]:
        """Do the budget modes actually spend the budget differently?

        These cannot be checked the way `min` and `max` are. `one_big` fixes
        the *shape* -- a single test case -- but the values inside it are still
        random, so two seeds legitimately produce different files, and seed
        invariance would fail a perfectly good generator.

        What is checkable is the thing that distinguishes them: the number of
        test cases. If `one_big` and `many_small` come back with the same
        count, the generator is treating both as ordinary random tests and the
        distinction the modes exist for was never made.
        """
        try:
            one_big = self._generate_test(["one_big", "1"])
            many_small = self._generate_test(["many_small", "1"])
        except SandboxError as exc:
            return False, f"Generator failed on a budget mode: {exc}"

        big_count = _leading_count(one_big)
        small_count = _leading_count(many_small)

        if big_count is None or small_count is None:
            return True, "budget modes not checked (no leading test-case count found)"

        if big_count >= small_count:
            return False, (
                f"Generator ignores the budget modes: 'one_big' produced "
                f"{big_count} test case(s) and 'many_small' produced "
                f"{small_count}. A capped total has no single largest test, so "
                "these two must spend the same budget in different shapes."
            )

        return True, (
            f"budget modes differ as required (one_big: {big_count} case(s), "
            f"many_small: {small_count})"
        )

    def _generate_test(self, args: List[str]) -> str:
        """Run the generator with these arguments and return what it wrote."""
        generator_bin = self.generated_dir / "generator"
        result = self.sandbox.run_binary(
            generator_bin,
            args=args,
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

    def _run_brute(self, input_data: str) -> str:
        """Run brute.cpp on the given input.  Returns its output.

        brute.cpp is deliberately unoptimized (O(n^3) etc. is expected by
        design), so it gets its own, longer timeout (self.brute_time_limit)
        rather than self.time_limit. A timeout here means "inconclusive on
        this test's size", not "solution.cpp is wrong" -- the caller treats
        SandboxError from this method as non-fatal to the test.
        """
        brute_bin = self.generated_dir / "brute"
        result = self.sandbox.run_binary(
            brute_bin,
            stdin=input_data,
            timeout=self.brute_time_limit,
        )
        if result.status == "timeout":
            raise SandboxError(
                f"brute.cpp exceeded {self.brute_time_limit}s "
                "(inconclusive at this input size, not necessarily wrong)"
            )
        if result.status != "success":
            raise SandboxError(f"brute.cpp runtime error: {result.stderr}")
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
        """Write the tests to disk, keeping the shippable ones apart.

        Only usable tests get a numbered ``.in``/``.ans`` pair in tests_dir, and
        they are renumbered from 1 with no gaps. Writing a ``.in`` whose ``.ans``
        never materialized is what put inputs with no answers into the released
        package; a test that failed is evidence for debugging, not a test.

        Failures are kept under ``rejected/`` with their original index, along
        with the reason, so nothing is lost.
        """
        for path in list(self.tests_dir.glob("*.in")) + list(self.tests_dir.glob("*.ans")):
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
            # On a brute mismatch, keep brute.cpp's output too -- solution.cpp
            # (.ans) and brute.cpp (.brute) side by side is exactly what's
            # needed to debug which one is actually wrong.
            if tc.matches_brute is False and tc.brute_output:
                (rejected_dir / f"{tc.index:03d}.brute").write_text(
                    tc.brute_output, encoding="utf-8"
                )
            (rejected_dir / f"{tc.index:03d}.why").write_text(
                (tc.error or "did not complete the generate/validate/solve/check chain")
                + "\n",
                encoding="utf-8",
            )
