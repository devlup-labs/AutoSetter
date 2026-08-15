"""
autosetter.prompts
==================
Prompt template loading and placeholder substitution.

Safely injects structured JSON payloads into prompt templates without using
`str.format()` (which would fail on literal curly braces within JSON or C++ code).
"""

from __future__ import annotations

from pathlib import Path

from autosetter.config import PROMPTS_DIR

# The placeholder token substituted in prompt templates
JSON_PLACEHOLDER = "{JSON}"


class PromptError(Exception):
    """Raised when a prompt template file is missing or cannot be read."""


def load_prompt_template(
    template_name: str,
    prompts_dir: str | Path = PROMPTS_DIR,
) -> str:
    """
    Read a prompt template from disk.

    Parameters
    ----------
    template_name : str
        Filename of the template (e.g. 'statement.txt', 'json_extraction.txt').
    prompts_dir : str | Path
        Directory containing the prompt template files.

    Returns
    -------
    str
        The raw template content.
    """
    template_path = Path(prompts_dir) / template_name

    if not template_path.exists():
        raise PromptError(f"Prompt template not found: {template_path}")

    try:
        return template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"Failed to read prompt template {template_path}: {exc}") from exc


def render_prompt(template_text: str, json_payload: str) -> str:
    """
    Safely replace `{JSON}` with the JSON payload string.

    Parameters
    ----------
    template_text : str
        Template string containing `{JSON}`.
    json_payload : str
        Pre-formatted JSON string.

    Returns
    -------
    str
        Rendered prompt string ready for LLM consumption.
    """
    if JSON_PLACEHOLDER not in template_text:
        return template_text
    return template_text.replace(JSON_PLACEHOLDER, json_payload)


def load_and_render_prompt(
    template_name: str,
    json_payload: str,
    prompts_dir: str | Path = PROMPTS_DIR,
) -> str:
    """Convenience helper to load a template and substitute its JSON payload."""
    template_text = load_prompt_template(template_name, prompts_dir=prompts_dir)
    return render_prompt(template_text, json_payload)
