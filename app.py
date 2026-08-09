#!/usr/bin/env python3
"""
app.py
======
AutoSetup -- automates the first phase of a competitive programming
problem-generation pipeline:

    1. Read a local problem-statement image (png/jpg/jpeg/pdf).
    2. Send it to a local Qwen vision model (via Ollama) to extract a
       structured JSON problem specification -> problem.json.
    3. Use that JSON to drive five further Ollama calls (one per
       specialized prompt template) that produce:
           generated/statement.md
           generated/solution.cpp
           generated/validator.cpp
           generated/generator.cpp
           generated/checker.cpp

Everything runs locally except for calls to the local Ollama daemon
(default: http://localhost:11434) -- no external network access is used.

Usage
-----
    python app.py /path/to/problem.png
    python app.py /path/to/problem.pdf --vision-model qwen2.5vl --text-model qwen2.5vl

Or, programmatically:

    from app import generate_from_image
    generate_from_image("/path/to/problem.png")
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from utils.ollama_client import OllamaClient, OllamaCallError
from utils.image_parser import ImageParsingError
from utils.json_generator import (
    generate_problem_json,
    save_problem_json,
    JSONGenerationError,
)
from utils.file_generator import generate_all_artifacts, FileGenerationError
from utils.sandbox_client import SandboxLocalClient, SandboxError, ensure_testlib
from utils.test_pipeline import TestPipeline, TestPipelineError
from utils.packager import Packager, PackagerError

# ---------------------------------------------------------------------------
# Project layout constants. Using pathlib throughout, resolved relative to
# this file's location so the app works regardless of the current working
# directory it's launched from.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
GENERATED_DIR = PROJECT_ROOT / "generated"
TESTS_DIR = PROJECT_ROOT / "tests"
PACKAGE_DIR = PROJECT_ROOT / "package"
PROBLEM_JSON_PATH = PROJECT_ROOT / "problem.json"

# Default model names. Overridable via CLI flags or function arguments.
# vision model = reads the problem image and extracts problem.json
# text model   = generates statement.md / solution.cpp / validator.cpp /
#                generator.cpp / checker.cpp from that JSON
DEFAULT_VISION_MODEL = "qwen2.5vl:3b"
DEFAULT_TEXT_MODEL = "qwen2.5-coder:1.5b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"


class AutoSetupError(Exception):
    """Top-level error type for any failure in the AutoSetup pipeline."""


def _log(message: str) -> None:
    """
    Centralized progress logger. Kept as a tiny wrapper (rather than raw
    `print`) so progress reporting is easy to redirect/extend later (e.g.
    to a GUI progress bar or a log file) without touching pipeline logic.
    """
    print(message, flush=True)


def generate_from_image(
    image_path: str | Path,
    vision_model: str = DEFAULT_VISION_MODEL,
    text_model: str = DEFAULT_TEXT_MODEL,
    ollama_host: str = DEFAULT_OLLAMA_HOST,
    num_tests: int = 10,
    skip_validation: bool = False,
) -> Path:
    """
    Run the ENTIRE AutoSetup pipeline for a single problem statement image.

    Parameters
    ----------
    image_path : str | Path
        Local path to the problem statement image or PDF.
    vision_model : str
        Name of the Qwen (or Qwen-compatible) vision model installed in
        the local Ollama instance, used for the image -> JSON extraction step.
    text_model : str
        Name of the model used for the five text-generation calls
        (statement/solution/validator/generator/checker). Can be the same
        vision model (most Qwen-VL models also handle text-only chat well)
        or a separate text-only model.
    ollama_host : str
        Base URL of the local Ollama server.

    Returns
    -------
    Path
        Path to the `generated/` directory containing all output files.

    Raises
    ------
    AutoSetupError
        Wraps any failure occurring anywhere in the pipeline, with a clear
        message about which stage failed.
    """
    image_path = Path(image_path)

    client = OllamaClient(host=ollama_host, default_model=vision_model)

    # ---- Stage 1: load image -------------------------------------------------
    _log("Loading image...")
    if not image_path.exists():
        raise AutoSetupError(f"Image not found: {image_path}")

    # ---- Stage 2: image -> structured JSON via Qwen VLM ----------------------
    _log("Generating JSON...")
    try:
        problem_data = generate_problem_json(
            image_path=image_path,
            prompts_dir=PROMPTS_DIR,
            client=client,
            vision_model=vision_model,
        )
        import json
        print(json.dumps(problem_data, indent=2)) # added extra to verify where it falls 
    except (JSONGenerationError, ImageParsingError, OllamaCallError) as exc:
        raise AutoSetupError(f"Failed while generating problem.json: {exc}") from exc

    # ---- Stage 3: save problem.json ------------------------------------------
    _log("Saving JSON...")
    try:
        save_problem_json(problem_data, PROBLEM_JSON_PATH)
    except JSONGenerationError as exc:
        raise AutoSetupError(f"Failed while saving problem.json: {exc}") from exc

    # ---- Stage 4: generate each downstream artifact ---------------------------
    # generate_all_artifacts() invokes `_log` (via progress_callback) with
    # messages like "Generating validator..." for each of the five files,
    # matching the exact progress-reporting style requested.
    try:
        generate_all_artifacts(
            problem_data=problem_data,
            prompts_dir=PROMPTS_DIR,
            generated_dir=GENERATED_DIR,
            client=client,
            text_model=text_model,
            progress_callback=_log,
        )
    except (FileGenerationError, OllamaCallError) as exc:
        raise AutoSetupError(f"Failed while generating output files: {exc}") from exc

    # ---- Stage 5: validate via sandbox ------------------------------------
    # This stage is *optional* — if validation fails, the pipeline still
    # produces a package; the validation report will indicate what failed.
    test_report = None
    if not skip_validation:
        _log("Starting validation pipeline...")
        try:
            # Ensure testlib.h is available for compilation
            ensure_testlib(GENERATED_DIR)

            sandbox = SandboxLocalClient(testlib_dir=GENERATED_DIR)
            pipeline = TestPipeline(
                generated_dir=GENERATED_DIR,
                tests_dir=TESTS_DIR,
                sandbox=sandbox,
                num_tests=num_tests,
                progress_callback=_log,
            )
            test_report = pipeline.run()

            if test_report.all_passed:
                _log(f"✅ All {test_report.passed_tests} tests passed!")
            else:
                _log(
                    f"⚠️  Validation: {test_report.passed_tests}/{test_report.total_tests} "
                    f"tests passed ({test_report.failed_tests} failed)"
                )
        except (SandboxError, TestPipelineError) as exc:
            _log(f"⚠️  Validation stage encountered an error: {exc}")
            _log("Continuing to packaging stage...")
    else:
        _log("Skipping validation (--skip-validation flag).")

    # ---- Stage 6: package for release ------------------------------------
    _log("Packaging release bundle...")
    try:
        packager = Packager(
            generated_dir=GENERATED_DIR,
            tests_dir=TESTS_DIR,
            problem_json_path=PROBLEM_JSON_PATH,
            package_dir=PACKAGE_DIR,
        )
        packager.build(progress_callback=_log)
    except PackagerError as exc:
        raise AutoSetupError(f"Failed while packaging: {exc}") from exc

    _log("Done.")
    return GENERATED_DIR


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line interface for running AutoSetup standalone."""
    parser = argparse.ArgumentParser(
        prog="AutoSetup",
        description=(
            "Automate the first phase of a competitive programming problem "
            "generation pipeline using a local Ollama Qwen vision model."
        ),
    )
    parser.add_argument(
        "image_path",
        type=str,
        help="Local path to the problem statement image (png/jpg/jpeg/pdf).",
    )
    parser.add_argument(
        "--vision-model",
        type=str,
        default=DEFAULT_VISION_MODEL,
        help=f"Ollama vision model name (default: {DEFAULT_VISION_MODEL}).",
    )
    parser.add_argument(
        "--text-model",
        type=str,
        default=DEFAULT_TEXT_MODEL,
        help=f"Ollama model name used for text generation (default: {DEFAULT_TEXT_MODEL}).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_OLLAMA_HOST,
        help=f"Local Ollama server URL (default: {DEFAULT_OLLAMA_HOST}).",
    )
    parser.add_argument(
        "--num-tests",
        type=int,
        default=10,
        help="Number of test cases to generate during validation (default: 10).",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        default=False,
        help="Skip the sandbox validation stage (compile/test/check).",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        output_dir = generate_from_image(
            image_path=args.image_path,
            vision_model=args.vision_model,
            text_model=args.text_model,
            ollama_host=args.host,
            num_tests=args.num_tests,
            skip_validation=args.skip_validation,
        )
    except AutoSetupError as exc:
        # Expected, well-classified pipeline failures: print a clean error
        # message without a Python traceback.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive catch-all
        # Anything truly unexpected still gets reported cleanly rather than
        # crashing with a raw traceback, per the "proper exception handling"
        # requirement.
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    print(f"\nAll files generated successfully in: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
