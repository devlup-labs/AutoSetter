"""
json_generator.py
==================
Step 3-4 of the pipeline: take the raw image(s) of a problem statement,
send them to the local Qwen vision model via Ollama, and turn the model's
reply into a validated `problem.json` structured specification.

The JSON schema produced (and enforced) here is:

{
    "title": "",
    "story": "",
    "input_format": "",
    "output_format": "",
    "constraints": "",
    "samples": [
        {"input": "", "output": "", "explanation": ""}
    ],
    "time_limit": "",
    "memory_limit": "",
    "notes": ""
}

Nothing in this schema's *values* is ever hardcoded by this module -- all
field values come directly from the model's reading of the image. This
module only enforces the *shape* (keys/types) so downstream steps
(statement/solution/validator/generator/checker generation) can rely on a
predictable structure.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

# The raw model reply is genuinely useful when extraction goes wrong, so it is
# kept -- but behind a logger rather than a print, so a normal run stays quiet.
# Run with AUTOSETTER_DEBUG=1 (see app.py) to see it.
logger = logging.getLogger(__name__)

from .image_parser import load_image_as_base64, ImageParsingError
from .ollama_client import OllamaClient, OllamaCallError
from .prompt_loader import load_prompt_template, PromptLoadError

# The exact top-level keys required in the final problem specification.
REQUIRED_TOP_LEVEL_KEYS = {
    "title",
    "story",
    "input_format",
    "output_format",
    "constraints",
    "samples",
    "time_limit",
    "memory_limit",
    "notes",
}

# The keys required inside each element of the "samples" list.
REQUIRED_SAMPLE_KEYS = {"input", "output", "explanation"}

# Name of the prompt template (in prompts/) used to instruct the vision
# model on how to extract and structure the problem statement.
JSON_EXTRACTION_TEMPLATE_NAME = "json_extraction.txt"


class JSONGenerationError(Exception):
    """Raised when the model's output cannot be turned into a valid problem spec."""


def _strip_markdown_code_fences(raw_text: str) -> str:
    """
    Vision/LLM models frequently wrap JSON output in ``` or ```json code
    fences even when explicitly told not to. Strip those defensively before
    attempting to parse, without altering the actual JSON content.
    """
    text = raw_text.strip()

    fence_pattern = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
    match = fence_pattern.match(text)
    if match:
        return match.group(1).strip()

    return text


def _extract_first_json_object(text: str, raw_text: str) -> str:
    """
    As a last-resort fallback (in case the model adds any stray preamble/
    epilogue text around the JSON), extract the substring spanning the
    first '{' to the matching last '}'. This is a defensive measure only;
    the primary path is a clean, direct `json.loads`.

    `raw_text` (the untouched original model reply) is included in the
    error message on failure so the actual cause -- an empty reply, a
    refusal, a conversational answer instead of JSON, etc. -- is visible
    to the user instead of a bare "could not locate JSON" message.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        preview = raw_text.strip()
        if not preview:
            preview = "(empty response -- the model returned no text at all)"
        elif len(preview) > 1000:
            preview = preview[:1000] + "... [truncated]"
        raise JSONGenerationError(
            "Could not locate a JSON object in the model's response.\n"
            f"--- Raw model output ---\n{preview}"
        )
    return text[start : end + 1]


def _parse_model_json(raw_text: str) -> Dict[str, Any]:
    """Robustly parse the model's textual reply into a Python dict."""
    cleaned = _strip_markdown_code_fences(raw_text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass  # fall through to the more lenient extraction attempt below

    fallback_candidate = _extract_first_json_object(cleaned, raw_text)
    try:
        return json.loads(fallback_candidate)
    except json.JSONDecodeError as exc:
        raise JSONGenerationError(
            f"Model output was not valid JSON, even after cleanup. "
            f"Parse error: {exc}\n--- Raw model output ---\n{raw_text}"
        ) from exc


def _validate_schema(data: Dict[str, Any]) -> None:
    """
    Validate that the parsed dict matches the required problem-spec shape.
    Raises JSONGenerationError with a precise message on any mismatch.
    """
    if not isinstance(data, dict):
        raise JSONGenerationError(f"Expected a JSON object at the top level, got {type(data)}")

    missing_keys = REQUIRED_TOP_LEVEL_KEYS - data.keys()
    if missing_keys:
        raise JSONGenerationError(f"Model JSON is missing required keys: {sorted(missing_keys)}")

    samples = data.get("samples")
    if not isinstance(samples, list):
        raise JSONGenerationError("The 'samples' field must be a JSON array.")

    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise JSONGenerationError(f"samples[{index}] must be a JSON object.")
        missing_sample_keys = REQUIRED_SAMPLE_KEYS - sample.keys()
        if missing_sample_keys:
            raise JSONGenerationError(
                f"samples[{index}] is missing keys: {sorted(missing_sample_keys)}"
            )


def _build_extraction_prompt(prompts_dir: Path) -> str:
    """
    Load the JSON-extraction prompt template. This template does not need
    the {JSON} placeholder (there is no prior JSON yet at this stage), but
    we route through the same loader for consistency and centralized error
    handling.
    """
    try:
        return load_prompt_template(prompts_dir, JSON_EXTRACTION_TEMPLATE_NAME)
    except PromptLoadError as exc:
        raise JSONGenerationError(str(exc)) from exc


def generate_problem_json(
    image_path: str | Path,
    prompts_dir: str | Path,
    client: OllamaClient,
    vision_model: str = "qwen2.5vl",
) -> Dict[str, Any]:
    """
    Full step: image path -> validated problem specification dict.

    Parameters
    ----------
    image_path : str | Path
        Local path to the problem statement image/PDF.
    prompts_dir : str | Path
        Directory containing prompt templates (expects json_extraction.txt).
    client : OllamaClient
        Configured Ollama client wrapper.
    vision_model : str
        Name of the Qwen (or Qwen-compatible) vision model installed in Ollama.

    Returns
    -------
    Dict[str, Any]
        The parsed and schema-validated problem specification.
    """
    prompts_dir = Path(prompts_dir)

    # 1. Load and normalize the image(s) into base64 PNG payload(s).
    try:
        images_base64 = load_image_as_base64(image_path)
    except ImageParsingError as exc:
        raise JSONGenerationError(f"Failed to load image: {exc}") from exc

    # 2. Build the extraction prompt.
    extraction_prompt = _build_extraction_prompt(prompts_dir)

    # 3. Call the Qwen vision model through Ollama.
    try:
        raw_reply = client.chat_with_images(
            prompt=extraction_prompt,
            images_base64=images_base64,
            model=vision_model,
            temperature=0.1,  # low temperature: this is a structured-extraction task
        )
        logger.debug("raw model output:\n%s", raw_reply)
    except OllamaCallError as exc:
        raise JSONGenerationError(f"Ollama vision call failed: {exc}") from exc

    # 4. Parse and validate the model's JSON reply.
    parsed = _parse_model_json(raw_reply)
    _validate_schema(parsed)

    return parsed


def save_problem_json(data: Dict[str, Any], output_path: str | Path) -> Path:
    """Serialize the problem specification dict to disk as pretty-printed JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        output_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise JSONGenerationError(f"Failed to write problem.json to {output_path}: {exc}") from exc

    return output_path
