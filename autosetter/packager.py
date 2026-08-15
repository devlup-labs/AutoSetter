"""
autosetter.packager
===================
Release packaging and manifest generation for Codeforces Polygon-ready bundles.

Assembles:
package/
├── problem.json          # Structured problem specification
├── statement.md          # Publication-ready Markdown statement
├── solutions/
│   └── solution.cpp      # Reference solution
├── files/
│   ├── validator.cpp     # Input validator (testlib.h)
│   ├── generator.cpp     # Test generator (testlib.h)
│   ├── checker.cpp       # Output checker (testlib.h)
│   └── testlib.h         # Bundled testlib header
├── tests/
│   ├── 001.in            # Shippable test inputs
│   ├── 001.ans           # Expected jury outputs
│   └── ...
├── testlib.h             # Root testlib header
├── validation_report.json
└── manifest.json         # Package contents, excluded tests, and release readiness
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from autosetter.config import VENDORED_TESTLIB


class PackagerError(Exception):
    """Raised when release bundle assembly fails."""


class Packager:
    """
    Bundles generated artifacts, validated test cases, and reports into a release package.

    Parameters
    ----------
    generated_dir : Path | str
        Directory containing generated artifacts (`statement.md`, `solution.cpp`, etc.).
    tests_dir : Path | str
        Directory containing generated `.in`/`.ans` pairs and `validation_report.json`.
    problem_json_path : Path | str
        Path to `problem.json`.
    package_dir : Path | str
        Target destination directory for the bundle.
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
        self._excluded_tests: List[str] = []

    def build(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Path:
        """Assemble the complete package directory."""
        _log = progress_callback or (lambda msg: None)

        if self.package_dir.exists():
            shutil.rmtree(self.package_dir)
        self.package_dir.mkdir(parents=True, exist_ok=True)

        # 1. Copy problem.json
        _log("Packaging problem.json...")
        if self.problem_json_path.exists():
            shutil.copy2(self.problem_json_path, self.package_dir / "problem.json")
        else:
            _log("  ⚠️  problem.json not found, skipping")

        # 2. Copy statement.md
        _log("Packaging statement...")
        statement_src = self.generated_dir / "statement.md"
        if statement_src.exists():
            shutil.copy2(statement_src, self.package_dir / "statement.md")
        else:
            _log("  ⚠️  statement.md not found, skipping")

        # 3. Copy solutions/
        _log("Packaging solutions...")
        solutions_dir = self.package_dir / "solutions"
        solutions_dir.mkdir(exist_ok=True)
        
        for sol_name in ["solution.cpp", "solution.wa.cpp", "solution.brute.cpp", "solution.tle.cpp"]:
            sol_src = self.generated_dir / sol_name
            if sol_src.exists():
                shutil.copy2(sol_src, solutions_dir / sol_name)
            elif sol_name == "solution.cpp":
                _log("  ⚠️  solution.cpp not found, skipping main solution")

        # 4. Copy files/ (validator, generator, checker)
        _log("Packaging testlib files...")
        files_dir = self.package_dir / "files"
        files_dir.mkdir(exist_ok=True)
        for name in ("validator.cpp", "generator.cpp", "checker.cpp"):
            src = self.generated_dir / name
            if src.exists():
                shutil.copy2(src, files_dir / name)
            else:
                _log(f"  ⚠️  {name} not found, skipping")

        # 5. Copy testlib.h
        testlib_src = self.generated_dir / "testlib.h"
        if not testlib_src.exists():
            testlib_src = VENDORED_TESTLIB
        if testlib_src.exists():
            shutil.copy2(testlib_src, self.package_dir / "testlib.h")
            shutil.copy2(testlib_src, files_dir / "testlib.h")
        else:
            _log("  ⚠️  testlib.h not found, skipping")

        # 6. Copy tests/ (only complete .in and .ans pairs)
        _log("Packaging tests...")
        tests_dest = self.package_dir / "tests"
        tests_dest.mkdir(exist_ok=True)
        self._excluded_tests = []

        generated_indices = set()
        report_src = self.tests_dir / "validation_report.json"
        if report_src.exists():
            try:
                report_data = json.loads(report_src.read_text(encoding="utf-8"))
                for tc in report_data.get("test_cases", []):
                    generated_indices.add(tc.get("index"))
            except json.JSONDecodeError:
                pass

        if self.tests_dir.exists():
            for input_path in sorted(self.tests_dir.glob("*.in")):
                answer_path = input_path.with_suffix(".ans")
                if not answer_path.exists():
                    self._excluded_tests.append(
                        f"{input_path.name}: no matching .ans file"
                    )
                    _log(f"  ⚠️  {input_path.name} has no answer file, excluded")
                    continue
                
                try:
                    idx = int(input_path.stem)
                    if idx in generated_indices:
                        # Skip generated tests; they will be defined in the script
                        continue
                except ValueError:
                    pass

                shutil.copy2(input_path, tests_dest / input_path.name)
                shutil.copy2(answer_path, tests_dest / answer_path.name)

            report_src = self.tests_dir / "validation_report.json"
            if report_src.exists():
                shutil.copy2(report_src, self.package_dir / "validation_report.json")
        else:
            _log("  ⚠️  Tests directory not found, skipping")

        if not list(tests_dest.glob("*.in")):
            _log("  ⚠️  Package contains no tests")

        # 7. Generate script file for Polygon (if tests were generated)
        _log("Generating test script...")
        script_content = ""
        report_src = self.tests_dir / "validation_report.json"
        if report_src.exists():
            try:
                report_data = json.loads(report_src.read_text(encoding="utf-8"))
                for tc in report_data.get("test_cases", []):
                    if "seed" in tc:
                        script_content += f"generator {tc['seed']} > $\n"
            except json.JSONDecodeError:
                pass
        
        if script_content:
            (self.package_dir / "script").write_text(script_content, encoding="utf-8")
        else:
            _log("  ⚠️  Could not generate script, missing validation report or test_cases")

        # 8. Generate manifest.json
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
        """Construct the package manifest dictionary."""
        files = []
        for p in sorted(self.package_dir.rglob("*")):
            if p.is_file():
                files.append(str(p.relative_to(self.package_dir)))

        title = "Unknown"
        pjson = self.package_dir / "problem.json"
        if pjson.exists():
            try:
                data = json.loads(pjson.read_text(encoding="utf-8"))
                title = data.get("title", "Unknown")
            except Exception:
                pass

        validation_summary = None
        report_path = self.package_dir / "validation_report.json"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                validation_summary = {
                    "total_tests": report.get("total_tests", 0),
                    "passed_tests": report.get("passed_tests", 0),
                    "all_passed": report.get("all_passed", False),
                    "validator_trusted": report.get("validator_trusted", False),
                    "checker_trusted": report.get("checker_trusted", False),
                    "diagnosis": report.get("diagnosis", ""),
                }
            except Exception:
                pass

        packaged_tests = len(list((self.package_dir / "tests").glob("*.in")))
        ready = bool(
            validation_summary
            and validation_summary.get("all_passed")
            and packaged_tests > 0
            and not self._excluded_tests
        )

        return {
            "problem_title": title,
            "ready_for_release": ready,
            "packaged_tests": packaged_tests,
            "excluded_tests": self._excluded_tests,
            "files": files,
            "file_count": len(files),
            "validation": validation_summary,
        }
