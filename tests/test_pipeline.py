"""
Pipeline integration tests verifying attribution, ground-truth sample checks, and checker probes.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from autosetter.pipeline import TestPipeline
from autosetter.sandbox import SandboxLocalClient
from tests.conftest import needs_gpp
from tests.fixtures import (
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


def test_correct_problem_pipeline_passes(tmp_path: Path):
    report = build_pipeline(tmp_path).run()

    assert report.all_passed
    assert report.validator_trusted
    assert report.checker_trusted
    assert report.passed_tests == report.total_tests == 3
    assert report.diagnosis == ""


def test_checker_that_never_compares_is_flagged_by_probes(tmp_path: Path):
    """
    Checker reads files without comparing; survives basic run, caught by perturbed probe.
    """
    report = build_pipeline(tmp_path, checker=CHECKER_NEVER_COMPARES).run()

    assert not report.checker_trusted
    assert not report.all_passed

    perturbed = next(p for p in report.checker_probes if p.name == "perturbed")
    assert not perturbed.rejected
    assert "accepts definitely wrong outputs" in report.diagnosis


def test_checker_that_ignores_output_fails_on_reference_answer(tmp_path: Path):
    report = build_pipeline(tmp_path, checker=CHECKER_ACCEPTS_ANYTHING).run()

    assert not any(tc.checker_ok for tc in report.test_cases)
    assert not report.all_passed


def test_trailing_garbage_probe_is_advisory(tmp_path: Path):
    report = build_pipeline(tmp_path, checker=CHECKER_IGNORES_TRAILING).run()

    trailing = next(p for p in report.checker_probes if p.name == "trailing_garbage")
    assert not trailing.fatal
    assert trailing.rejected
    assert report.checker_trusted
    assert report.all_passed


def test_out_of_range_generator_blames_generator(tmp_path: Path):
    report = build_pipeline(tmp_path, generator=GENERATOR_OUT_OF_RANGE).run()

    assert report.validator_trusted
    assert report.passed_tests == 0
    assert not report.all_passed
    assert all("generator is the file at fault" in tc.error for tc in report.test_cases)


def test_broken_validator_blames_validator(tmp_path: Path):
    wrong_bounds = VALIDATOR.replace("inf.readInt(1, 100", "inf.readInt(1, 3")
    report = build_pipeline(tmp_path, validator=wrong_bounds).run()

    assert not report.validator_trusted
    assert "rejects official samples" in report.diagnosis
    for tc in report.test_cases:
        if tc.error:
            assert "validator is the file at fault" in tc.error


def test_unusable_tests_never_reach_tests_directory(tmp_path: Path):
    pipeline = build_pipeline(tmp_path, generator=GENERATOR_OUT_OF_RANGE)
    pipeline.run()

    tests_dir = tmp_path / "tests"
    assert list(tests_dir.glob("*.in")) == []
    assert list(tests_dir.glob("*.ans")) == []
    assert (tests_dir / "rejected").exists()
    assert list((tests_dir / "rejected").glob("*.why"))


def test_shipped_tests_are_numbered_sequentially_without_gaps(tmp_path: Path):
    report = build_pipeline(tmp_path, num_tests=4).run()

    tests_dir = tmp_path / "tests"
    names = sorted(p.name for p in tests_dir.glob("*.in"))
    assert names == ["001.in", "002.in", "003.in", "004.in"]
    for path in tests_dir.glob("*.in"):
        assert path.with_suffix(".ans").exists()
    assert report.passed_tests == 4


def test_missing_samples_leaves_validator_uncorroborated(tmp_path: Path):
    report = build_pipeline(tmp_path, samples=[]).run()

    assert not report.validator_trusted
    assert not report.all_passed
    assert "No samples extracted" in report.diagnosis
