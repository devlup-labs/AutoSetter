"""
autosetter.runner
=================
End-to-end pipeline driver for AutoSetter, independent of the command line.

Executes:
1. Intake: Read statement image/PDF.
2. Extraction: Extract and validate `problem.json` via the vision model.
3. Existing-problem check: KNN (k=1) search in the vector database. If the
   nearest stored problem's cosine similarity is above the threshold (0.80),
   its link is returned and nothing is generated (unless `force=True`).
4. Generation: Produce artifacts via the text model (validator, test spec,
   solutions, checker, statement), with self-healing retries.
5. Validation: Sandboxed compilation, sample verification, test case generation, and checker probing.
6. Packaging: Assemble the Polygon package with manifest.

Publishing to Polygon is a separate step (`autosetter.polygon.publish_package`),
driven by the CLI once the package exists.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from autosetter.config import (
    DEFAULT_NUM_TESTS,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OUT_DIR,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
    PROMPTS_DIR,
    SIMILARITY_ENABLED,
    SIMILARITY_THRESHOLD,
    SIMILARITY_TOP_K,
)
from autosetter.extractor import (
    JSONExtractionError,
    generate_problem_json,
    save_problem_json,
)
from autosetter.generator import CodeGenerationError, generate_all_artifacts
from autosetter.llm import OllamaCallError, OllamaClient
from autosetter.packager import Packager, PackagerError
from autosetter.pipeline import PipelineError, TestPipeline, TestReport
from autosetter.sandbox import SandboxError, SandboxLocalClient, ensure_testlib
from autosetter.similarity import (
    SimilaritySearchError,
    find_existing_problem,
    find_similar_problems,
    save_similar_problems,
)
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
    similar_problems: List[Dict[str, Any]] = field(default_factory=list)
    # Set when the vector database already holds this problem; the run then
    # stops before generation and no package is built.
    existing_problem: Optional[Dict[str, Any]] = None

    @property
    def ready_for_release(self) -> bool:
        """Whether the assembled package is verified and fit to release."""
        return self.existing_problem is None and bool(self.report and self.report.all_passed)

    @property
    def summary(self) -> str:
        """Human-readable status summary of the validation report."""
        if self.existing_problem is not None:
            return "the problem already exists, so no package was generated"
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
    similarity_check: bool = SIMILARITY_ENABLED,
    similar_k: int = SIMILARITY_TOP_K,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    force: bool = False,
) -> PipelineResult:
    """
    Execute the end-to-end AutoSetter problem packaging pipeline.

    Parameters
    ----------
    image_path : str | Path
        Path to input problem statement image (.png/.jpg) or document (.pdf).
    vision_model : str
        Ollama vision model name (e.g. 'qwen3-vl:32b').
    text_model : str
        Ollama text model name (e.g. 'Qwen3-Coder-Next:latest') — for text/code artifacts.
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
    similarity_check : bool
        If True, look up the problem in the vector database (Qdrant) first.
    similar_k : int
        Number of nearest problems to retrieve (default 1).
    similarity_threshold : float
        Cosine similarity above which the nearest problem counts as existing.
    force : bool
        If True, generate a package even when the problem already exists.

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

    client = OllamaClient(host=ollama_host, default_model=vision_model)

    # 1. Image Intake
    logger_fn("Loading image...")
    if not input_image_path.exists():
        raise AutoSetterError(f"Input file not found: {input_image_path}")

    # 2. Vision Extraction -> problem.json
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

    # 3. Save problem.json
    logger_fn("Saving JSON specification...")
    try:
        save_problem_json(problem_data, problem_json_path)
    except JSONExtractionError as exc:
        raise AutoSetterError(f"Failed to save problem.json: {exc}") from exc

    # 3b. Existing-problem lookup in the vector database (optional; a
    # database error only skips it)
    similar_problems: List[Dict[str, Any]] = []
    if similarity_check:
        logger_fn("Searching vector database for an existing copy of this problem...")
        try:
            similar_problems = find_similar_problems(problem_data, k=similar_k)
            save_similar_problems(similar_problems, output_root / "similar_problems.json")
            for match in similar_problems:
                logger_fn(f"  {match['score']:.4f}  {match['title']}  {match['url'] or 'N/A'}")
        except SimilaritySearchError as exc:
            logger_fn(f"⚠️  Existing-problem search skipped: {exc}")

        existing = find_existing_problem(similar_problems, threshold=similarity_threshold)
        if existing is not None:
            logger_fn(
                f"Existing problem found: {existing['title']} "
                f"(cosine similarity {existing['score']:.2%} > {similarity_threshold:.0%})"
            )
            logger_fn(f"  {existing['url'] or 'no link stored for this problem'}")
            if not force:
                return PipelineResult(
                    generated_dir=generated_dir,
                    package_dir=package_dir,
                    similar_problems=similar_problems,
                    existing_problem=existing,
                )
            logger_fn("Continuing anyway (--force).")

    # 4. Generate Downstream Artifacts & Validation Loop
    test_report = None
    validation_error = ""

    if skip_validation:
        logger_fn("Skipping validation (--skip-validation flag). Generating artifacts once.")
        try:
            generate_all_artifacts(
                problem_data=problem_data,
                generated_dir=generated_dir,
                client=client,
                prompts_dir=prompts_dir_path,
                text_model=text_model,
                progress_callback=logger_fn,
            )
        except (CodeGenerationError, OllamaCallError) as exc:
            raise AutoSetterError(f"Failed while generating code artifacts: {exc}") from exc
    else:
        targets = None
        feedback_context = {}

        for iteration in range(3):
            if iteration > 0:
                logger_fn(f"\n🔄 Initiating Self-Healing Iteration {iteration}/3 for targets: {targets or 'all'}")
    
            try:
                generate_all_artifacts(
                    problem_data=problem_data,
                    generated_dir=generated_dir,
                    client=client,
                    prompts_dir=prompts_dir_path,
                    text_model=text_model,
                    progress_callback=logger_fn,
                    targets=targets,
                    feedback_context=feedback_context,
                )
            except (CodeGenerationError, OllamaCallError) as exc:
                raise AutoSetterError(f"Failed while generating code artifacts: {exc}") from exc

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
                    break  # Success!
                else:
                    logger_fn(
                        f"⚠️  Validation: {test_report.passed_tests}/{test_report.total_tests} "
                        f"tests passed ({test_report.failed_tests} failed)"
                    )

                    # Analyze failure for next iteration
                    targets = []
                    feedback_context = {}

                    # Files that failed to build (including an unusable test spec)
                    for name, error in test_report.compilation.errors.items():
                        if name in ("validator", "generator", "solution", "checker"):
                            targets.append(name)
                            feedback_context[name] = (
                                f"Your file could not be used by the pipeline:\n{error[:2000]}"
                            )

                    if "validator rejects official samples" in test_report.diagnosis:
                        targets.append("validator")
                        feedback_context["validator"] = "The validator you generated rejected the official problem samples provided in the problem description."

                    if test_report.test_cases:
                        generator_errors = [tc.error for tc in test_report.test_cases if not tc.generator_ok or (tc.generator_ok and not tc.validator_ok)]
                        if generator_errors:
                            targets.append("generator")
                            feedback_context["generator"] = f"Your generator produced output that violates the constraints or crashed. Error: {generator_errors[0]}"
    
                        solution_errors = [tc.error for tc in test_report.test_cases if not tc.solution_ok]
                        if solution_errors:
                            targets.append("solution")
                            feedback_context["solution"] = f"Your reference solution crashed or gave Wrong Answer. Error: {solution_errors[0]}"
        
                    if not test_report.checker_trusted and not "validator rejects official samples" in test_report.diagnosis:
                        if "checker" not in targets:
                            targets.append("checker")
                        feedback_context["checker"] = "The checker accepts definitely wrong outputs, meaning it is flawed and would accept wrong contestant submissions. You must write a strict checker."
        
                    if not targets:
                        # Fallback if we can't pinpoint the error
                        targets = None
                        feedback_context = {}
    
            except (SandboxError, PipelineError) as exc:
                validation_error = str(exc)
                logger_fn(f"⚠️  Validation encountered a fatal error: {exc}")
                logger_fn("Continuing to packaging stage, but package is unverified...")
                break

    # 6. Polygon Packaging
    logger_fn("Packaging Polygon bundle...")
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
        similar_problems=similar_problems,
    )
