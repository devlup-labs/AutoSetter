"""
autosetter.extractor
====================
Extracts structured problem specifications (`problem.json`) from problem images/PDFs.

Coordinates:
1. Image loading and base64 encoding (via `autosetter.vision`).
2. Vision model call using `json_extraction.txt` template (via `autosetter.llm`).
3. Resilient JSON parsing and code fence stripping.
4. Strict schema validation to guarantee downstream compatibility.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from autosetter.config import DEFAULT_VISION_MODEL, PROMPTS_DIR
from autosetter.llm import OllamaCallError, OllamaClient
from autosetter.prompts import PromptError, load_prompt_template
from autosetter.vision import ImageParsingError, load_image_as_base64

logger = logging.getLogger(__name__)

# The exact top-level schema keys required in a problem specification.
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

# The keys required inside each sample in the "samples" array.
REQUIRED_SAMPLE_KEYS = {"input", "output", "explanation"}

# Default prompt template name
JSON_EXTRACTION_TEMPLATE = "json_extraction.txt"


class JSONExtractionError(Exception):
    """Raised when problem.json extraction, parsing, or schema validation fails."""


def strip_markdown_code_fences(raw_text: str) -> str:
    """
    Remove leading/trailing ``` or ```json markdown code fences if present.
    """
    text = raw_text.strip()
    fence_pattern = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
    match = fence_pattern.match(text)
    if match:
        return match.group(1).strip()
    return text


def extract_first_json_object(text: str, raw_text: str) -> str:
    """
    Locate the outermost JSON object spanning from first '{' to last '}'.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        preview = raw_text.strip()
        if not preview:
            preview = "(empty response -- the model returned no text at all)"
        elif len(preview) > 1000:
            preview = preview[:1000] + "... [truncated]"
        raise JSONExtractionError(
            "Could not locate a JSON object in the model's response.\n"
            f"--- Raw model output ---\n{preview}"
        )
    return text[start : end + 1]


def parse_model_json(raw_text: str) -> Dict[str, Any]:
    """Parse model text output into a dictionary with fallback strategies."""
    cleaned = strip_markdown_code_fences(raw_text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    candidate = extract_first_json_object(cleaned, raw_text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise JSONExtractionError(
            f"Model output was not valid JSON even after extraction cleanup: {exc}\n"
            f"--- Raw model output ---\n{raw_text}"
        ) from exc


def validate_schema(data: Dict[str, Any]) -> None:
    """
    Validate that the parsed dictionary satisfies the required problem specification schema.
    """
    if not isinstance(data, dict):
        raise JSONExtractionError(f"Expected a top-level JSON object, got {type(data)}")

    missing_keys = REQUIRED_TOP_LEVEL_KEYS - data.keys()
    if missing_keys:
        raise JSONExtractionError(
            f"Model JSON is missing required top-level keys: {sorted(missing_keys)}"
        )

    samples = data.get("samples")
    if not isinstance(samples, list):
        raise JSONExtractionError("The 'samples' field must be a JSON array.")

    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise JSONExtractionError(f"samples[{index}] must be a JSON object.")
        missing_sample_keys = REQUIRED_SAMPLE_KEYS - sample.keys()
        if missing_sample_keys:
            raise JSONExtractionError(
                f"samples[{index}] is missing keys: {sorted(missing_sample_keys)}"
            )


def sanitize_leetcode_strings(data: Dict[str, Any]) -> None:
    """
    Strip Leetcode-style formatting (e.g. `root = [...]`, commas, brackets) 
    from sample inputs and outputs so they behave like classic CP space-separated streams.
    """
    def clean_text(text: str) -> str:
        if not text:
            return text
        # Remove "Input:" or "Output:" labels
        text = re.sub(r'(?i)\b(?:Input|Output)\s*:\s*', '', text)
        # Remove variable assignments like "root = " or "nums = "
        text = re.sub(r'[a-zA-Z_0-9]+\s*=\s*', '', text)
        # Remove brackets and quotes
        text = re.sub(r'[\[\]"\'`]', ' ', text)
        # Replace commas with spaces
        text = text.replace(',', ' ')
        # Collapse multiple spaces but preserve newlines
        lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()]
        return '\n'.join(line for line in lines if line) + '\n'

    for sample in data.get("samples", []):
        if "input" in sample:
            sample["input"] = clean_text(str(sample["input"]))
        if "output" in sample:
            sample["output"] = clean_text(str(sample["output"]))


def generate_problem_json(
    image_path: str | Path,
    client: OllamaClient,
    prompts_dir: str | Path = PROMPTS_DIR,
    vision_model: str = DEFAULT_VISION_MODEL,
) -> Dict[str, Any]:
    """
    Extract and validate a structured problem specification from an image or PDF.

    Parameters
    ----------
    image_path : str | Path
        Path to the statement image (.png/.jpg) or document (.pdf).
    client : OllamaClient
        Configured Ollama client.
    prompts_dir : str | Path
        Directory containing `json_extraction.txt`.
    vision_model : str
        Name of the vision model to use.

    Returns
    -------
    Dict[str, Any]
        Validated problem specification dict.

    Raises
    ------
    JSONExtractionError
        If image loading, Ollama vision inference, JSON parsing, or schema validation fails.
    """
    try:
        images_base64 = load_image_as_base64(image_path)
    except ImageParsingError as exc:
        raise JSONExtractionError(f"Failed to load input image: {exc}") from exc

    try:
        extraction_prompt = load_prompt_template(
            JSON_EXTRACTION_TEMPLATE, prompts_dir=prompts_dir
        )
    except PromptError as exc:
        raise JSONExtractionError(str(exc)) from exc

    try:
        raw_reply = client.chat_with_images(
            prompt=extraction_prompt,
            images_base64=images_base64,
            model=vision_model,
            temperature=0.1,
        )
        logger.debug("Raw model output:\n%s", raw_reply)
    except OllamaCallError as exc:
        raise JSONExtractionError(f"Ollama vision inference failed: {exc}") from exc

    parsed = parse_model_json(raw_reply)
    validate_schema(parsed)
    sanitize_leetcode_strings(parsed)
    return parsed


def save_problem_json(data: Dict[str, Any], output_path: str | Path) -> Path:
    """Write the problem specification dictionary to disk as formatted JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        output_path.write_text(
            json.dumps(data, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise JSONExtractionError(f"Failed to write problem.json to {output_path}: {exc}") from exc

    return output_path
