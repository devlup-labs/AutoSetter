"""What the validation stage is supposed to catch.

Each test here corresponds to a way the pipeline used to pass something broken:

  a checker that accepts everything      -> checker_trusted must be False
  a generator that ignores the bounds    -> the tests must not be shippable
  a validator that rejects the samples   -> the validator must take the blame
  a test with no answer                  -> must never reach the tests directory
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.cpp_fixtures import (
    BRUTE,
    BRUTE_DISAGREES,
    CHECKER,
    CHECKER_ACCEPTS_ANYTHING,
    CHECKER_IGNORES_TRAILING,
    CHECKER_NEVER_COMPARES,
    GENERATOR,
    GENERATOR_OUT_OF_RANGE,
    SAMPLES,
    SOLUTION,
    VALIDATOR,
)
from tests.conftest import needs_gpp
from utils.sandbox_client import SandboxLocalClient
from utils.test_pipeline import TestPipeline

pytestmark = needs_gpp


def build_pipeline(
    tmp_path: Path,
    *,
    validator: str = VALIDATOR,
    generator: str = GENERATOR,
    solution: str = SOLUTION,
    checker: str = CHECKER,
    brute: str | None = BRUTE,
    samples=None,
    num_tests: int = 3,
) -> TestPipeline:
    generated = tmp_path / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "validator.cpp").write_text(validator)
    (generated / "generator.cpp").write_text(generator)
    (generated / "solution.cpp").write_text(solution)
    (generated / "checker.cpp").write_text(checker)
    # brute=None simulates brute.cpp not having been generated at all, so
    # tests can exercise that path without a source file that fails to compile.
    if brute is not None:
        (generated / "brute.cpp").write_text(brute)

    return TestPipeline(
        generated_dir=generated,
        tests_dir=tmp_path / "tests",
        sandbox=SandboxLocalClient(testlib_dir=generated),
        num_tests=num_tests,
        samples=SAMPLES if samples is None else samples,
    )


def test_a_correct_problem_passes(tmp_path):
    pipeline = build_pipeline(tmp_path)
    report = pipeline.run()

    assert report.all_passed
    assert report.validator_trusted
    assert report.checker_trusted
    assert report.brute_verified
    assert report.modes_respected
    assert report.passed_tests == report.total_tests == len(pipeline.plan)
    assert report.diagnosis == ""


def test_checker_that_never_compares_is_not_trusted(tmp_path):
    """The regression test for the bug this whole stage had.

    Every test still passes, because the solution's own output is correct and
    this checker approves of it — which is all the old pipeline ever asked.
    It also survives the empty and truncated probes, because those fail on the
    read rather than on the comparison. Only a well-formed wrong answer
    exposes it.
    """
    report = build_pipeline(tmp_path, checker=CHECKER_NEVER_COMPARES).run()

    assert all(tc.checker_ok for tc in report.test_cases), "old pipeline saw 3/3 passing"
    assert not report.checker_trusted
    assert not report.all_passed

    perturbed = next(p for p in report.checker_probes if p.name == "perturbed")
    assert not perturbed.rejected
    assert "accepts outputs that are definitely wrong" in report.diagnosis


def test_checker_that_ignores_the_output_fails_on_the_reference_answer(tmp_path):
    """testlib catches this one without help.

    A checker cannot quit _ok while the contestant's output still has unread
    tokens, so one that judges nothing reports PE on the jury's own answer.
    """
    report = build_pipeline(tmp_path, checker=CHECKER_ACCEPTS_ANYTHING).run()

    assert not any(tc.checker_ok for tc in report.test_cases)
    assert not report.all_passed


def test_trailing_garbage_probe_is_advisory(tmp_path):
    """Not every weakness is the checker author's fault.

    This checker never calls seekEof, yet testlib rejects the extra token on
    its behalf. The probe stays advisory because passing it proves little
    either way.
    """
    report = build_pipeline(tmp_path, checker=CHECKER_IGNORES_TRAILING).run()

    trailing = next(p for p in report.checker_probes if p.name == "trailing_garbage")
    assert not trailing.fatal
    assert trailing.rejected, "testlib refuses unread output on its own"
    assert report.checker_trusted
    assert report.all_passed


def test_out_of_range_generator_blames_the_generator(tmp_path):
    """When the two files disagree, the samples say which one is wrong."""
    report = build_pipeline(tmp_path, generator=GENERATOR_OUT_OF_RANGE).run()

    assert report.validator_trusted, "the validator accepts the official sample"
    assert report.passed_tests == 0
    assert not report.all_passed
    assert all("generator is the file at fault" in tc.error for tc in report.test_cases)


def test_broken_validator_blames_the_validator(tmp_path):
    """A validator whose bounds are wrong rejects the problem's own sample."""
    wrong_bounds = VALIDATOR.replace("inf.readInt(1, 100", "inf.readInt(1, 3")
    report = build_pipeline(tmp_path, validator=wrong_bounds).run()

    assert not report.validator_trusted
    assert "rejects the problem's own samples" in report.diagnosis
    for tc in report.test_cases:
        if tc.error:
            assert "validator is the file at fault" in tc.error


def test_unusable_tests_never_reach_the_tests_directory(tmp_path):
    """An input with no answer is not a test, and must not be written as one."""
    pipeline = build_pipeline(tmp_path, generator=GENERATOR_OUT_OF_RANGE)
    pipeline.run()

    tests_dir = tmp_path / "tests"
    assert list(tests_dir.glob("*.in")) == []
    assert list(tests_dir.glob("*.ans")) == []
    # ...but the evidence is kept for debugging.
    assert (tests_dir / "rejected").exists()
    assert list((tests_dir / "rejected").glob("*.why"))


def test_shipped_tests_are_numbered_without_gaps(tmp_path):
    pipeline = build_pipeline(tmp_path, num_tests=8)
    report = pipeline.run()

    tests_dir = tmp_path / "tests"
    names = sorted(p.name for p in tests_dir.glob("*.in"))
    assert names == [f"{i:03d}.in" for i in range(1, len(pipeline.plan) + 1)]
    for path in tests_dir.glob("*.in"):
        assert path.with_suffix(".ans").exists()
    assert report.passed_tests == len(pipeline.plan)


def test_no_samples_leaves_the_validator_uncorroborated(tmp_path):
    report = build_pipeline(tmp_path, samples=[]).run()

    assert not report.validator_trusted
    assert not report.all_passed
    assert "No samples" in report.diagnosis or "no samples" in report.diagnosis


def test_brute_mismatch_blames_the_solution(tmp_path):
    """The regression test for the missing piece this stage used to have.

    Every earlier check only ever asks solution.cpp to agree with itself, so
    a solution that is fluent but simply computes the wrong thing sails
    through all of them. Only an independent brute-force oracle can catch it.
    """
    report = build_pipeline(tmp_path, brute=BRUTE_DISAGREES).run()

    assert not report.brute_verified
    assert not report.all_passed
    assert report.passed_tests == 0
    assert all(tc.matches_brute is False for tc in report.test_cases)
    assert all("disagrees with brute.cpp" in tc.error for tc in report.test_cases)
    assert "disagrees with brute.cpp" in report.diagnosis

    # And the mismatch evidence is kept for debugging, brute's output
    # included, the same way a validator rejection keeps its evidence.
    rejected_dir = tmp_path / "tests" / "rejected"
    brute_evidence = list(rejected_dir.glob("*.brute"))
    assert brute_evidence, "expected brute.cpp's disagreeing output to be kept"


def test_missing_brute_blocks_release_but_not_the_run(tmp_path):
    """brute.cpp not compiling degrades the run, it doesn't halt it.

    Unlike solution.cpp/generator.cpp, an absent brute.cpp doesn't stop the
    pipeline (there's still a package worth looking at), but the package
    must not claim to be release-ready without the cross-check having run.
    """
    pipeline = build_pipeline(tmp_path, brute=None)
    report = pipeline.run()

    assert not report.compilation.brute
    assert not report.brute_verified
    assert not report.all_passed
    assert "brute.cpp does not compile" in report.diagnosis
    # The rest of the chain still ran and produced usable tests.
    assert report.modes_respected
    assert report.passed_tests == report.total_tests == len(pipeline.plan)


def test_compile_failure_shows_the_compiler_error(tmp_path):
    """The one question a reader has is "why", and it used to be unanswerable.

    The error was always captured into the report; it was simply never printed,
    so a failure in the terminal said only that something went wrong.
    """
    log: list[str] = []
    generated = tmp_path / "generated"
    generated.mkdir(parents=True)
    (generated / "validator.cpp").write_text(VALIDATOR.replace("inf.readEof();", "inf.readEof()"))
    (generated / "generator.cpp").write_text(GENERATOR)
    (generated / "solution.cpp").write_text(SOLUTION)
    (generated / "checker.cpp").write_text(CHECKER)
    (generated / "brute.cpp").write_text(BRUTE)

    TestPipeline(
        generated_dir=generated,
        tests_dir=tmp_path / "tests",
        sandbox=SandboxLocalClient(testlib_dir=generated),
        num_tests=1,
        progress_callback=log.append,
    )._compile_all()

    printed = "\n".join(log)
    assert "validator: compilation failed" in printed
    assert "error:" in printed, "the compiler's own message must reach the terminal"
    assert "validator.cpp" in printed


def test_testlib_that_does_not_build_is_named_as_the_cause(tmp_path):
    """Three failures with one cause should be reported as one cause.

    The validator, generator and checker are the three artifacts that include
    testlib.h. When a toolchain cannot compile testlib they fail together, and
    that is not three broken files -- regenerating them changes nothing.
    """
    log: list[str] = []
    generated = tmp_path / "generated"
    generated.mkdir(parents=True)
    (generated / "validator.cpp").write_text(VALIDATOR)
    (generated / "generator.cpp").write_text(GENERATOR)
    (generated / "solution.cpp").write_text(SOLUTION)
    (generated / "checker.cpp").write_text(CHECKER)
    (generated / "brute.cpp").write_text(BRUTE)
    (generated / "testlib.h").write_text('#error "unusable here"\n')

    comp = TestPipeline(
        generated_dir=generated,
        tests_dir=tmp_path / "tests",
        sandbox=SandboxLocalClient(testlib_dir=generated),
        num_tests=1,
        progress_callback=log.append,
    )._compile_all()

    printed = "\n".join(log)
    assert comp.solution and comp.brute, "the files without testlib still build"
    assert not (comp.validator or comp.generator or comp.checker)
    assert "toolchain, not the generated code" in printed
    assert "compiler:" in printed, "the compiler version is part of the diagnosis"
    assert "testlib" in comp.errors, "and the report records it too"


def test_a_single_testlib_failure_is_not_blamed_on_the_toolchain(tmp_path):
    """One broken file among three working ones is a broken file."""
    log: list[str] = []
    generated = tmp_path / "generated"
    generated.mkdir(parents=True)
    (generated / "validator.cpp").write_text(VALIDATOR.replace("inf.readEof();", "inf.readEof()"))
    (generated / "generator.cpp").write_text(GENERATOR)
    (generated / "solution.cpp").write_text(SOLUTION)
    (generated / "checker.cpp").write_text(CHECKER)
    (generated / "brute.cpp").write_text(BRUTE)

    comp = TestPipeline(
        generated_dir=generated,
        tests_dir=tmp_path / "tests",
        sandbox=SandboxLocalClient(testlib_dir=generated),
        num_tests=1,
        progress_callback=log.append,
    )._compile_all()

    assert comp.generator and comp.checker
    assert "toolchain" not in "\n".join(log)
    assert "testlib" not in comp.errors
