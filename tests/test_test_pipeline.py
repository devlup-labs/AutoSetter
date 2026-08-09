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
    CHECKER,
    CHECKER_ACCEPTS_ANYTHING,
    CHECKER_IGNORES_TRAILING,
    CHECKER_NEVER_COMPARES,
    GENERATOR,
    GENERATOR_IGNORES_MODE,
    GENERATOR_OUT_OF_RANGE,
    MULTI_CHECKER,
    MULTI_GENERATOR,
    MULTI_GENERATOR_IGNORES_BUDGET,
    MULTI_PROBLEM,
    MULTI_SOLUTION,
    MULTI_VALIDATOR,
    SAMPLES,
    SOLUTION,
    VALIDATOR,
)
from tests.conftest import needs_gpp
from utils.sandbox_client import SandboxLocalClient
from utils.test_pipeline import TestPipeline
from utils.test_plan import shaped_count

pytestmark = needs_gpp


def build_pipeline(
    tmp_path: Path,
    *,
    validator: str = VALIDATOR,
    generator: str = GENERATOR,
    solution: str = SOLUTION,
    checker: str = CHECKER,
    samples=None,
    num_tests: int = 3,
) -> TestPipeline:
    generated = tmp_path / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "validator.cpp").write_text(validator)
    (generated / "generator.cpp").write_text(generator)
    (generated / "solution.cpp").write_text(solution)
    (generated / "checker.cpp").write_text(checker)

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
    assert report.modes_respected
    # Six shaped tests plus however many random ones were asked for — the
    # shaped ones are fixed by the constraints, not by a count.
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


def test_generator_that_ignores_its_mode_is_caught(tmp_path):
    """The regression test for "no test ever reaches a stated bound".

    Every test this generator makes is valid, so validity checks cannot see the
    problem. What gives it away is that `min` — which describes exactly one
    input — comes back different for two different seeds.
    """
    report = build_pipeline(tmp_path, generator=GENERATOR_IGNORES_MODE).run()

    assert all(tc.usable for tc in report.test_cases), "every test is individually valid"
    assert not report.modes_respected
    assert not report.all_passed
    assert "ignores its mode argument" in report.mode_evidence
    assert "reaches the problem's stated bounds" in report.diagnosis


def test_shaped_generator_reaches_both_bounds(tmp_path):
    """min and max must actually be the extremes, not two more random tests."""
    report = build_pipeline(tmp_path).run()

    assert report.modes_respected
    by_name = {tc.name: tc.input_data.strip() for tc in report.test_cases}
    assert by_name["min"] == "1", "min must sit on the lower bound"
    assert by_name["max"] == "100", "max must sit on the upper bound"


def test_the_plan_is_shapes_not_seeds(tmp_path):
    """A single-test problem gets the six universal shapes, then random ones.

    The three budget shapes are absent, because a file that holds one test case
    has no budget to spend in different ways.
    """
    report = build_pipeline(tmp_path, num_tests=12).run()

    shaped = shaped_count(multitest=False)
    names = [tc.name for tc in report.test_cases]
    assert names[:shaped] == ["min", "max", "edge", "flat", "sorted", "reversed"]
    assert all(n.startswith("random_") for n in names[shaped:])
    assert all(tc.purpose for tc in report.test_cases), "every test says what it is for"


def build_multitest(tmp_path: Path, generator: str = MULTI_GENERATOR) -> TestPipeline:
    """A problem with t test cases and a cap on the total size."""
    generated = tmp_path / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "validator.cpp").write_text(MULTI_VALIDATOR)
    (generated / "generator.cpp").write_text(generator)
    (generated / "solution.cpp").write_text(MULTI_SOLUTION)
    (generated / "checker.cpp").write_text(MULTI_CHECKER)

    return TestPipeline(
        generated_dir=generated,
        tests_dir=tmp_path / "tests",
        sandbox=SandboxLocalClient(testlib_dir=generated),
        num_tests=12,
        problem=MULTI_PROBLEM,
    )


def test_budget_modes_spend_the_same_total_in_different_shapes(tmp_path):
    """A capped total has no single largest test, so each shape is required.

    `one_big` and `many_small` both spend the budget and both are legal — and
    they break different solutions, which is why running only one of them
    leaves half the problem untested.
    """
    report = build_multitest(tmp_path).run()

    assert report.all_passed, report.diagnosis
    shapes = {tc.name: tc.input_data for tc in report.test_cases}

    def test_case_count(text: str) -> int:
        return int(text.split("\n", 1)[0])

    assert test_case_count(shapes["one_big"]) == 1, "one enormous case"
    assert test_case_count(shapes["many_small"]) == 10, "as many cases as allowed"
    assert test_case_count(shapes["skewed"]) == 10, "one large case among minimal ones"
    assert shapes["one_big"] != shapes["many_small"], "the same budget, different shapes"


def test_max_respects_the_sum_across_test_cases(tmp_path):
    """t at its maximum AND n at its maximum is illegal, and must not be tried.

    This is the mistake the mode contract warns about: for t <= 10 and n <= 100
    with sum n <= 200, the naive "everything at its maximum" file is five times
    over the cap and the validator rejects it.
    """
    report = build_multitest(tmp_path).run()

    biggest = next(tc for tc in report.test_cases if tc.name == "max")
    assert biggest.validator_ok, "max must satisfy every constraint at once"

    lines = biggest.input_data.strip().split("\n")
    t = int(lines[0])
    sizes = [int(lines[1 + 2 * i]) for i in range(t)]
    assert sum(sizes) <= 200, "the sum cap binds even in the heaviest test"
    assert max(sizes) == 100, "and the per-case bound is still reached"


def test_no_samples_leaves_the_validator_uncorroborated(tmp_path):
    report = build_pipeline(tmp_path, samples=[]).run()

    assert not report.validator_trusted
    assert not report.all_passed
    assert "No samples" in report.diagnosis or "no samples" in report.diagnosis


def test_multitest_is_detected_from_the_statement(tmp_path):
    """The budget shapes are only planned where they mean something."""
    multi = build_multitest(tmp_path)
    assert multi.multitest
    assert [s.name for s in multi.plan][:9][-3:] == ["one_big", "many_small", "skewed"]

    single = build_pipeline(tmp_path / "single")
    assert not single.multitest, "the toy problem reads one test case per file"
    assert "one_big" not in [s.name for s in single.plan]


def test_generator_that_ignores_the_budget_modes_is_caught(tmp_path):
    """one_big and many_small must not produce the same shape.

    This generator handles min and max correctly, so seed invariance passes and
    every test it makes is valid. What gives it away is that the whole budget
    lands in the same number of test cases either way, so the distinction the
    modes exist for was never made.
    """
    report = build_multitest(tmp_path, generator=MULTI_GENERATOR_IGNORES_BUDGET).run()

    assert all(tc.usable for tc in report.test_cases), "every test is individually valid"
    assert not report.modes_respected
    assert "ignores the budget modes" in report.mode_evidence
    assert not report.all_passed
