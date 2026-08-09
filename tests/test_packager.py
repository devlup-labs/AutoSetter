"""The packager's job is to refuse to ship a broken package."""

from __future__ import annotations

import json
from pathlib import Path

from utils.packager import Packager


def make_dirs(tmp_path: Path):
    generated = tmp_path / "generated"
    tests = tmp_path / "tests"
    generated.mkdir()
    tests.mkdir()

    (generated / "statement.md").write_text("# Sum\n")
    (generated / "solution.cpp").write_text("int main() {}\n")
    for name in ("validator.cpp", "generator.cpp", "checker.cpp"):
        (generated / name).write_text("int main() {}\n")

    (tmp_path / "problem.json").write_text(json.dumps({"title": "Sum"}))
    return generated, tests


def write_report(tests: Path, **overrides):
    report = {
        "total_tests": 2,
        "passed_tests": 2,
        "all_passed": True,
        "validator_trusted": True,
        "checker_trusted": True,
        "diagnosis": "",
    }
    report.update(overrides)
    (tests / "validation_report.json").write_text(json.dumps(report))


def build(tmp_path: Path) -> dict:
    generated, tests = tmp_path / "generated", tmp_path / "tests"
    packager = Packager(
        generated_dir=generated,
        tests_dir=tests,
        problem_json_path=tmp_path / "problem.json",
        package_dir=tmp_path / "package",
    )
    packager.build()
    return json.loads((tmp_path / "package" / "manifest.json").read_text())


def test_complete_pairs_are_packaged(tmp_path):
    _, tests = make_dirs(tmp_path)
    for i in (1, 2):
        (tests / f"{i:03d}.in").write_text("5\n")
        (tests / f"{i:03d}.ans").write_text("10\n")
    write_report(tests)

    manifest = build(tmp_path)

    assert manifest["packaged_tests"] == 2
    assert manifest["excluded_tests"] == []
    assert manifest["ready_for_release"] is True


def test_input_without_an_answer_is_excluded(tmp_path):
    """The exact shape of the bug: 007.in shipped with no 007.ans."""
    _, tests = make_dirs(tmp_path)
    (tests / "001.in").write_text("5\n")
    (tests / "001.ans").write_text("10\n")
    (tests / "007.in").write_text("9\n")  # no answer
    write_report(tests)

    manifest = build(tmp_path)
    packaged = sorted(p.name for p in (tmp_path / "package" / "tests").iterdir())

    assert packaged == ["001.ans", "001.in"]
    assert manifest["excluded_tests"] == ["007.in: no matching .ans file"]
    assert manifest["ready_for_release"] is False


def test_failed_validation_is_not_ready_for_release(tmp_path):
    _, tests = make_dirs(tmp_path)
    (tests / "001.in").write_text("5\n")
    (tests / "001.ans").write_text("10\n")
    write_report(tests, all_passed=False, passed_tests=1, diagnosis="checker is a liar")

    manifest = build(tmp_path)

    assert manifest["ready_for_release"] is False
    assert manifest["validation"]["diagnosis"] == "checker is a liar"


def test_untrusted_checker_shows_up_in_the_manifest(tmp_path):
    _, tests = make_dirs(tmp_path)
    (tests / "001.in").write_text("5\n")
    (tests / "001.ans").write_text("10\n")
    write_report(tests, all_passed=False, checker_trusted=False)

    manifest = build(tmp_path)

    assert manifest["validation"]["checker_trusted"] is False
    assert manifest["ready_for_release"] is False
