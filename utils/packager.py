"""
packager.py
============
Stage 04 of the AutoSetter workflow: **Release** (minus the Human Review
Gate and Polygon API publication, which are handled separately).

Bundles all generated artifacts, test data, and the validation report into
a self-contained release package directory, ready for human review or
Polygon upload.

Package layout
--------------
::

    package/
    ├── problem.json          # structured problem specification
    ├── statement.md          # rendered problem statement
    ├── solutions/
    │   └── solution.cpp      # reference solution
    ├── files/
    │   ├── validator.cpp     # input validator (testlib.h)
    │   ├── generator.cpp     # test generator (testlib.h)
    │   └── checker.cpp       # output checker (testlib.h)
    ├── tests/
    │   ├── 001.in            # generated test inputs
    │   ├── 001.ans           # expected outputs
    │   ├── 002.in
    │   ├── 002.ans
    │   └── ...
    ├── testlib.h             # bundled testlib.h for convenience
    └── validation_report.json
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional


class PackagerError(Exception):
    """Raised when packaging fails."""


class Packager:
    """
    Bundles all pipeline outputs into a release-ready package directory.

    Parameters
    ----------
    generated_dir : Path
        Directory containing generated artifacts (solution.cpp, etc.).
    tests_dir : Path
        Directory containing generated test inputs/outputs and the
        validation report.
    problem_json_path : Path
        Path to the problem.json file.
    package_dir : Path
        Destination directory for the assembled package.
    """

    def __init__(
        self,
        generated_dir: str | Path,
        tests_dir: str | Path,
        problem_json_path: str | Path,
        package_dir: str | Path,
    ) -> None:
        self.generated_dir = Path(generated_dir)
        self.tests_dir = Path(tests_dir)
        self.problem_json_path = Path(problem_json_path)
        self.package_dir = Path(package_dir)

    def build(self, progress_callback=None) -> Path:
        """
        Assemble the release package.  Returns the path to the package
        directory.
        """
        _log = progress_callback or (lambda msg: None)

        # Clean and recreate the package directory
        if self.package_dir.exists():
            shutil.rmtree(self.package_dir)
        self.package_dir.mkdir(parents=True, exist_ok=True)

        # ---- problem.json ------------------------------------------------
        _log("Packaging problem.json...")
        if self.problem_json_path.exists():
            shutil.copy2(self.problem_json_path, self.package_dir / "problem.json")
        else:
            _log("  ⚠️  problem.json not found, skipping")

        # ---- statement.md ------------------------------------------------
        _log("Packaging statement...")
        statement_src = self.generated_dir / "statement.md"
        if statement_src.exists():
            shutil.copy2(statement_src, self.package_dir / "statement.md")
        else:
            _log("  ⚠️  statement.md not found, skipping")

        # ---- solutions/ --------------------------------------------------
        _log("Packaging solutions...")
        solutions_dir = self.package_dir / "solutions"
        solutions_dir.mkdir(exist_ok=True)
        solution_src = self.generated_dir / "solution.cpp"
        if solution_src.exists():
            shutil.copy2(solution_src, solutions_dir / "solution.cpp")
        else:
            _log("  ⚠️  solution.cpp not found, skipping")

        # ---- files/ (validator, generator, checker) ----------------------
        _log("Packaging testlib files...")
        files_dir = self.package_dir / "files"
        files_dir.mkdir(exist_ok=True)
        for name in ("validator.cpp", "generator.cpp", "checker.cpp"):
            src = self.generated_dir / name
            if src.exists():
                shutil.copy2(src, files_dir / name)
            else:
                _log(f"  ⚠️  {name} not found, skipping")

        # ---- testlib.h ---------------------------------------------------
        # Look for testlib.h in the generated directory or its parent
        testlib_src = self.generated_dir / "testlib.h"
        if not testlib_src.exists():
            testlib_src = self.generated_dir.parent / "testlib.h"
        if testlib_src.exists():
            shutil.copy2(testlib_src, self.package_dir / "testlib.h")
            # Also put a copy in files/ so validator/generator/checker can
            # find it relative to themselves
            shutil.copy2(testlib_src, files_dir / "testlib.h")

        # ---- tests/ ------------------------------------------------------
        _log("Packaging tests...")
        tests_dest = self.package_dir / "tests"
        tests_dest.mkdir(exist_ok=True)

        if self.tests_dir.exists():
            # Copy all .in and .ans files
            for ext in ("*.in", "*.ans"):
                for f in sorted(self.tests_dir.glob(ext)):
                    shutil.copy2(f, tests_dest / f.name)

            # Copy validation report
            report_src = self.tests_dir / "validation_report.json"
            if report_src.exists():
                shutil.copy2(report_src, self.package_dir / "validation_report.json")
        else:
            _log("  ⚠️  Tests directory not found, skipping")

        # ---- manifest / summary ------------------------------------------
        _log("Creating package manifest...")
        manifest = self._build_manifest()
        manifest_path = self.package_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        _log(f"Package assembled at: {self.package_dir}")
        return self.package_dir

    def _build_manifest(self) -> Dict[str, Any]:
        """
        Build a manifest listing all files in the package.
        """
        files = []
        for p in sorted(self.package_dir.rglob("*")):
            if p.is_file():
                files.append(str(p.relative_to(self.package_dir)))

        # Load problem title from problem.json if available
        title = "Unknown"
        pjson = self.package_dir / "problem.json"
        if pjson.exists():
            try:
                data = json.loads(pjson.read_text(encoding="utf-8"))
                title = data.get("title", "Unknown")
            except Exception:
                pass

        # Load validation summary if available
        validation_summary = None
        report_path = self.package_dir / "validation_report.json"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                validation_summary = {
                    "total_tests": report.get("total_tests", 0),
                    "passed_tests": report.get("passed_tests", 0),
                    "all_passed": report.get("all_passed", False),
                }
            except Exception:
                pass

        return {
            "problem_title": title,
            "files": files,
            "file_count": len(files),
            "validation": validation_summary,
        }
