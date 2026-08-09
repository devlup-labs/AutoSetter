"""
file_generator.py
==================
Step 4-5 of the pipeline: given the validated `problem.json` specification,
generate each downstream artifact (statement.md, solution.cpp, validator.cpp,
generator.cpp, checker.cpp) by calling Ollama once per artifact, each with
its own specialized prompt template from `prompts/`.

Every artifact's *content* comes entirely from the model -- this module only
handles orchestration (loading templates, injecting JSON, calling Ollama,
lightly cleaning code fences, and writing files to `generated/`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .ollama_client import OllamaClient, OllamaCallError
from .prompt_loader import load_and_render_prompt, PromptLoadError


class FileGenerationError(Exception):
    """Raised when generating or saving one of the downstream artifacts fails."""


@dataclass(frozen=True)
class ArtifactSpec:
    """Describes one generated artifact: its prompt template and output file name."""

    name: str  # human-readable label used in progress messages, e.g. "statement"
    prompt_template: str  # file name in prompts/, e.g. "statement.txt"
    output_filename: str  # file name in generated/, e.g. "statement.md"
    strip_code_fence: bool  # whether to strip ``` fences from the model's reply


# The six artifacts required by the pipeline, in generation order.
# "brute" is generated right after "generator": both exist purely to power
# the validation stage (utils/test_pipeline.py), which runs the generator's
# tests through brute.cpp and solution.cpp and cross-checks their outputs --
# the only step able to catch a solution.cpp that is fluent but wrong.
ARTIFACTS: List[ArtifactSpec] = [
    ArtifactSpec("statement", "statement.txt", "statement.md", strip_code_fence=True),
    ArtifactSpec("validator", "validator.txt", "validator.cpp", strip_code_fence=True),
    ArtifactSpec("generator", "generator.txt", "generator.cpp", strip_code_fence=True),
    ArtifactSpec("brute", "brute.txt", "brute.cpp", strip_code_fence=True),
    ArtifactSpec("solution", "solution.txt", "solution.cpp", strip_code_fence=True),
    ArtifactSpec("checker", "checker.txt", "checker.cpp", strip_code_fence=True),
]


def _strip_code_fence(text: str) -> str:
    """
    Remove a single leading/trailing markdown code fence (``` or ```cpp /
    ```markdown / ```json etc.) if the model wrapped its answer in one,
    without touching the actual generated content.
    """
    stripped = text.strip()
    fence_pattern = re.compile(r"^```[a-zA-Z0-9_+-]*\s*(.*?)\s*```$", re.DOTALL)
    match = fence_pattern.match(stripped)
    if match:
        return match.group(1).strip() + "\n"
    return stripped + "\n"


def _generate_single_artifact(
    spec: ArtifactSpec,
    json_payload: str,
    prompts_dir: Path,
    generated_dir: Path,
    client: OllamaClient,
    text_model: str,
) -> Path:
    """Generate one artifact end-to-end: render prompt -> call Ollama -> save file."""
    try:
        rendered_prompt = load_and_render_prompt(prompts_dir, spec.prompt_template, json_payload)
    except PromptLoadError as exc:
        raise FileGenerationError(
            f"Failed to load prompt template for '{spec.name}': {exc}"
        ) from exc

    try:
        raw_reply = client.chat_text(
            prompt=rendered_prompt,
            model=text_model,
            temperature=0.2,
        )
    except OllamaCallError as exc:
        raise FileGenerationError(
            f"Ollama call failed while generating '{spec.name}': {exc}"
        ) from exc

    content = _strip_code_fence(raw_reply) if spec.strip_code_fence else raw_reply

    output_path = generated_dir / spec.output_filename
    try:
        generated_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise FileGenerationError(
            f"Failed to write generated file {output_path}: {exc}"
        ) from exc

    return output_path


def generate_all_artifacts(
    problem_data: Dict[str, Any],
    prompts_dir: str | Path,
    generated_dir: str | Path,
    client: OllamaClient,
    text_model: str,
    progress_callback=None,
) -> Dict[str, Path]:
    """
    Generate statement.md, validator.cpp, generator.cpp, solution.cpp and
    checker.cpp from the given problem specification.

    Parameters
    ----------
    problem_data : Dict[str, Any]
        The validated problem specification (as produced by
        `json_generator.generate_problem_json`).
    prompts_dir : str | Path
        Directory containing the prompt templates.
    generated_dir : str | Path
        Directory to write the generated artifacts into.
    client : OllamaClient
        Configured Ollama client wrapper.
    text_model : str
        Name of the (typically text-only) model used for code/markdown
        generation. Qwen VL models generally also support text-only chat,
        so the same model can be reused here if desired.
    progress_callback : Optional[Callable[[str], None]]
        Optional callback invoked with a human-readable progress message
        before each artifact is generated (e.g. "Generating validator...").

    Returns
    -------
    Dict[str, Path]
        Mapping of artifact name -> path of the written file.
    """
    prompts_dir = Path(prompts_dir)
    generated_dir = Path(generated_dir)

    # Serialize once; every prompt template gets the exact same JSON text
    # substituted in place of {JSON}.
    json_payload = json.dumps(problem_data, indent=2, ensure_ascii=False)

    written_paths: Dict[str, Path] = {}

    for spec in ARTIFACTS:
        if progress_callback:
            progress_callback(f"Generating {spec.name}...")

        path = _generate_single_artifact(
            spec=spec,
            json_payload=json_payload,
            prompts_dir=prompts_dir,
            generated_dir=generated_dir,
            client=client,
            text_model=text_model,
        )
        written_paths[spec.name] = path

    return written_paths
