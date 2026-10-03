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


def load_and_render_prompt(
    template_name: str,
    json_payload: str,
    prompts_dir: str | Path = PROMPTS_DIR,
) -> str:
    """Read a prompt template and substitute its JSON payload."""
    template_path = Path(prompts_dir) / template_name
    if not template_path.exists():
        raise PromptError(f"Prompt template not found: {template_path}")
    try:
        return template_path.read_text(encoding="utf-8").replace("{JSON}", json_payload)
    except OSError as exc:
        raise PromptError(f"Failed to read prompt template {template_path}: {exc}") from exc

