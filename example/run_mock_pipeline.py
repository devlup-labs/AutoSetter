#!/usr/bin/env python3
"""
run_mock_pipeline.py
====================
Runs the sandboxed validation and packaging stages of the AutoSetter
pipeline for the Two Sum problem locally on this machine without requiring Ollama.
"""

import sys
from pathlib import Path
import json

# Add parent directory to sys.path to import utils
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.sandbox_client import SandboxLocalClient, SandboxError, ensure_testlib
from utils.test_pipeline import TestPipeline
from utils.packager import Packager

def run_mock():
    print("🚀 Starting Mock AutoSetter Pipeline Execution for 'Two Sum'...")
    
    # 1. Setup paths
    base_dir = Path(__file__).resolve().parent
    intake_dir = base_dir / "01_intake"
    build_dir = base_dir / "02_build"
    validate_dir = base_dir / "03_validate"
    package_dir = base_dir / "04_package"
    problem_json_path = intake_dir / "problem.json"
    
    print("\n--- Step 1: Check Intake Spec ---")
    if not problem_json_path.exists():
        print(f"❌ Error: {problem_json_path} does not exist.")
        return
    with open(problem_json_path, "r") as f:
        problem_data = json.load(f)
    print(f"Found problem specification: '{problem_data.get('title')}'")
    
    print("\n--- Step 2: Provide testlib.h ---")
    testlib_path = ensure_testlib(build_dir)
    print(f"testlib.h copied from third_party/ to {testlib_path}")
    
    print("\n--- Step 3: Run Validation & Sandbox Execution ---")
    sandbox = SandboxLocalClient(testlib_dir=build_dir)
    
    # Clean previous validation outputs if they exist
    if validate_dir.exists():
        import shutil
        shutil.rmtree(validate_dir)
    validate_dir.mkdir(parents=True, exist_ok=True)
    
    pipeline = TestPipeline(
        generated_dir=build_dir,
        tests_dir=validate_dir,
        sandbox=sandbox,
        num_tests=10,
        progress_callback=print,
        # The statement's own samples, so the validator can be corroborated
        # before anything it says about generated tests is believed.
        samples=problem_data.get("samples") or [],
    )
    
    print("Running test pipeline (compiling artifacts, generating test inputs, validating inputs, solving, and checking outputs)...")
    report = pipeline.run()
    
    print("\n--- Step 4: Packaging Release Bundle ---")
    packager = Packager(
        generated_dir=build_dir,
        tests_dir=validate_dir,
        problem_json_path=problem_json_path,
        package_dir=package_dir
    )
    packager.build(progress_callback=print)

    print("\n--- Mock AutoSetter Pipeline finished ---")
    print(f"Validation Report: {validate_dir / 'validation_report.json'}")
    print(f"Package Manifest:  {package_dir / 'manifest.json'}")

    # "Finished" and "worked" are different things, and the old message said
    # the second one regardless.
    if report.all_passed:
        print("\n✨ The package is fit to release. ✨")
        return 0
    print(f"\n⚠️  Not fit to release: {report.diagnosis}")
    return 2

if __name__ == "__main__":
    sys.exit(run_mock())
