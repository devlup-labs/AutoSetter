"""
autosetter.cli
==============
Command-line interface and end-to-end pipeline driver for AutoSetter.

Executes:
1. Intake: Read statement image/PDF.
2. Extraction: Extract and validate `problem.json` via Qwen vision model (1st model — UNCHANGED).
3. Generation: Produce artifacts via TWO model backends:
   - HuggingFace (fine-tuned Qwen 7B): statement.tex, solution.cpp, brute.cpp, checker.cpp
   - Ollama (qwen2.5-coder:1.5b): validator.cpp, generator.cpp, solution.wa.cpp, solution.tle.cpp
4. Validation: Sandboxed compilation, sample verification, test case generation, and checker probing.
5. Packaging: Assemble release package bundle with manifest.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from autosetter.config import (
    DEFAULT_NUM_TESTS,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OUT_DIR,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
    # ── NEW: Import HuggingFace model defaults ──
    DEFAULT_HF_MODEL_NAME,
    PROMPTS_DIR,
)
from autosetter.extractor import (
    JSONExtractionError,
    generate_problem_json,
    save_problem_json,
)
from autosetter.generator import CodeGenerationError, generate_all_artifacts
# ── EXISTING: Ollama client for the 1st model (vision) and Ollama-routed artifacts ──
from autosetter.llm import OllamaCallError, OllamaClient
# ── NEW: HuggingFace client for the 4 fine-tuned Qwen 7B artifacts ──
from autosetter.hf_model import HFModelClient, HFModelError
from autosetter.packager import Packager, PackagerError
from autosetter.pipeline import PipelineError, TestPipeline, TestReport
from autosetter.sandbox import SandboxError, SandboxLocalClient, ensure_testlib
from autosetter.vision import ImageParsingError

logger = logging.getLogger(__name__)


class AutoSetterError(Exception):
    """Top-level error class for pipeline failures in AutoSetter."""


# Backwards compatibility alias
AutoSetupError = AutoSetterError


@dataclass
class PipelineResult:
    """Outcome of an AutoSetter pipeline run."""

    generated_dir: Path
    package_dir: Path
    report: Optional[TestReport] = None
    validation_error: str = ""

    @property
    def ready_for_release(self) -> bool:
        """Whether the assembled package is verified and fit to release."""
        return bool(self.report and self.report.all_passed)

    @property
    def summary(self) -> str:
        """Human-readable status summary of the validation report."""
        if self.validation_error:
            return f"validation could not run: {self.validation_error}"
        if self.report is None:
            return "validation was skipped, so package correctness is unconfirmed"
        if self.report.all_passed:
            return f"all {self.report.passed_tests} tests passed"
        return self.report.diagnosis or "validation did not pass"


def _log(message: str) -> None:
    """Centralized progress logger."""
    print(message, flush=True)


def generate_from_image(
    image_path: str | Path,
    vision_model: str = DEFAULT_VISION_MODEL,
    text_model: str = DEFAULT_TEXT_MODEL,
    ollama_host: str = DEFAULT_OLLAMA_HOST,
    num_tests: int = DEFAULT_NUM_TESTS,
    skip_validation: bool = False,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    prompts_dir: str | Path = PROMPTS_DIR,
    progress_callback: Optional[Callable[[str], None]] = None,
    # ── NEW PARAMETER: HuggingFace model name for the 2nd model ──
    # This controls which fine-tuned Qwen 7B model is loaded from
    # HuggingFace Hub for generating statement.tex, solution.cpp,
    # brute.cpp, and checker.cpp.
    hf_model: str = DEFAULT_HF_MODEL_NAME,
) -> PipelineResult:
    """
    Execute the end-to-end AutoSetter problem packaging pipeline.

    Parameters
    ----------
    image_path : str | Path
        Path to input problem statement image (.png/.jpg) or document (.pdf).
    vision_model : str
        Ollama vision model name (e.g. 'qwen2.5vl:3b') — 1st model, UNCHANGED.
    text_model : str
        Ollama text model name (e.g. 'qwen2.5-coder:1.5b') — for non-HF artifacts.
    ollama_host : str
        Base URL for the Ollama daemon.
    num_tests : int
        Number of test cases to generate and validate.
    skip_validation : bool
        If True, skip compilation and test execution stages.
    out_dir : str | Path
        Directory to write intermediate and final output packages.
    prompts_dir : str | Path
        Directory containing prompt templates.
    progress_callback : Optional[Callable[[str], None]]
        Progress reporting callback.
    hf_model : str
        HuggingFace Hub model identifier for the fine-tuned Qwen 7B.
        Default: 'winglian/qwen-2.5-codeforces-cot-kd-v1'

    Returns
    -------
    PipelineResult
        Contains generated directories, validation report, and release readiness status.
    """
    logger_fn = progress_callback or _log
    input_image_path = Path(image_path)
    output_root = Path(out_dir)

    generated_dir = output_root / "generated"
    tests_dir = output_root / "tests"
    package_dir = output_root / "package"
    problem_json_path = output_root / "problem.json"
    prompts_dir_path = Path(prompts_dir)

    # ── Instantiate the Ollama client (used for 1st model + Ollama-routed artifacts) ──
    # This is UNCHANGED from the original code.
    client = OllamaClient(host=ollama_host, default_model=vision_model)

    # ── Instantiate the HuggingFace client (used for the 4 HF-routed artifacts) ──
    # The model is loaded lazily on first inference call inside HFModelClient,
    # so this instantiation is lightweight and does not allocate GPU memory yet.
    logger_fn(f"Initializing HuggingFace model client (model: {hf_model})...")
    hf_client = HFModelClient(model_name=hf_model)

    # 1. Image Intake (UNCHANGED)
    logger_fn("Loading image...")
    if not input_image_path.exists():
        raise AutoSetterError(f"Input file not found: {input_image_path}")

    # 2. Vision Extraction -> problem.json (UNCHANGED — uses 1st model via Ollama)
    logger_fn("Generating JSON specification...")
    try:
        problem_data = generate_problem_json(
            image_path=input_image_path,
            client=client,
            prompts_dir=prompts_dir_path,
            vision_model=vision_model,
        )
        logger.debug("Extracted problem.json:\n%s", json.dumps(problem_data, indent=2))
    except (JSONExtractionError, ImageParsingError, OllamaCallError) as exc:
        raise AutoSetterError(f"Failed to generate problem.json: {exc}") from exc

    # 3. Save problem.json (UNCHANGED)
    logger_fn("Saving JSON specification...")
    try:
        save_problem_json(problem_data, problem_json_path)
    except JSONExtractionError as exc:
        raise AutoSetterError(f"Failed to save problem.json: {exc}") from exc

    # 4. Generate Downstream Artifacts (MODIFIED — now dispatches to HF + Ollama)
    # The generate_all_artifacts() function internally routes each artifact to
    # the correct backend based on the HF_MODEL_ARTIFACTS set.
    try:
        generate_all_artifacts(
            problem_data=problem_data,
            generated_dir=generated_dir,
            client=client,
            # ── NEW: Pass the HuggingFace client for HF-routed artifacts ──
            hf_client=hf_client,
            prompts_dir=prompts_dir_path,
            text_model=text_model,
            progress_callback=logger_fn,
        )
    except (CodeGenerationError, OllamaCallError, HFModelError) as exc:
        raise AutoSetterError(f"Failed while generating code artifacts: {exc}") from exc

    # 5. Sandboxed Validation Pipeline
    test_report = None
    validation_error = ""
    if not skip_validation:
        logger_fn("Starting validation pipeline...")
        try:
            ensure_testlib(generated_dir)
            sandbox = SandboxLocalClient(testlib_dir=generated_dir)
            pipeline = TestPipeline(
                generated_dir=generated_dir,
                tests_dir=tests_dir,
                sandbox=sandbox,
                num_tests=num_tests,
                progress_callback=logger_fn,
                samples=problem_data.get("samples") or [],
            )
            test_report = pipeline.run()

            if test_report.all_passed:
                logger_fn(f"✅ All {test_report.passed_tests} tests passed!")
            else:
                logger_fn(
                    f"⚠️  Validation: {test_report.passed_tests}/{test_report.total_tests} "
                    f"tests passed ({test_report.failed_tests} failed)"
                )
        except (SandboxError, PipelineError) as exc:
            validation_error = str(exc)
            logger_fn(f"⚠️  Validation encountered an error: {exc}")
            logger_fn("Continuing to packaging stage, but package is unverified...")
    else:
        logger_fn("Skipping validation (--skip-validation flag).")

    # 6. Release Packaging
    logger_fn("Packaging release bundle...")
    try:
        packager = Packager(
            generated_dir=generated_dir,
            tests_dir=tests_dir,
            problem_json_path=problem_json_path,
            package_dir=package_dir,
        )
        packager.build(progress_callback=logger_fn)
    except PackagerError as exc:
        raise AutoSetterError(f"Failed while packaging release bundle: {exc}") from exc

    logger_fn("Done.")
    return PipelineResult(
        generated_dir=generated_dir,
        package_dir=package_dir,
        report=test_report,
        validation_error=validation_error,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line interface parser."""
    parser = argparse.ArgumentParser(
        prog="autosetter",
        description=(
            "Automated Codeforces/Polygon competitive programming problem generator "
            "powered by local Qwen models through Ollama."
        ),
    )
    parser.add_argument(
        "image_path",
        type=str,
        help="Local path to problem statement image (.png/.jpg/.jpeg) or document (.pdf).",
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
        help=f"Ollama text model name (default: {DEFAULT_TEXT_MODEL}).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama server URL (default: {DEFAULT_OLLAMA_HOST}).",
    )
    parser.add_argument(
        "--num-tests",
        type=int,
        default=DEFAULT_NUM_TESTS,
        help=f"Number of test cases to generate (default: {DEFAULT_NUM_TESTS}).",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        default=False,
        help="Skip sandbox compilation and validation stage.",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(DEFAULT_OUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUT_DIR}).",
    )
    # ── NEW: HuggingFace model override for the fine-tuned Qwen 7B ──
    # This allows users to specify an alternative HuggingFace model
    # (e.g. a local path to a quantized variant) from the command line.
    parser.add_argument(
        "--hf-model",
        type=str,
        default=DEFAULT_HF_MODEL_NAME,
        help=(
            f"HuggingFace model for artifact generation "
            f"(default: {DEFAULT_HF_MODEL_NAME})."
        ),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main CLI entry point.

    Exit Codes:
    - 0: Package generated successfully and is verified fit for release.
    - 1: Pipeline encountered a fatal error.
    - 2: Artifacts produced, but validation failed (package is not fit for release).
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("AUTOSETTER_DEBUG") else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        result = generate_from_image(
            image_path=args.image_path,
            vision_model=args.vision_model,
            text_model=args.text_model,
            ollama_host=args.host,
            num_tests=args.num_tests,
            skip_validation=args.skip_validation,
            out_dir=args.out_dir,
            # ── NEW: Pass the HuggingFace model name from CLI args ──
            hf_model=args.hf_model,
        )
    except AutoSetterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    print(f"\nArtifacts written to: {result.generated_dir}")
    print(f"Package assembled at: {result.package_dir}")

    if result.ready_for_release:
        print(f"Ready for release — {result.summary}")
        return 0

    print(f"NOT ready for release — {result.summary}", file=sys.stderr)
    if result.report is not None and not result.report.validator_trusted:
        print(
            "  The validator disagrees with the problem's own samples; fix validator "
            "or constraints before release.",
            file=sys.stderr,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
