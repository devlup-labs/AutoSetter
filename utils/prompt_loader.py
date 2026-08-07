"""
prompt_loader.py
=================
Loads prompt templates from the `prompts/` directory and safely injects
runtime values (most importantly the `{JSON}` placeholder, which is replaced
with the structured problem specification generated in step 3 of the
pipeline).

We deliberately avoid Python's built-in `str.format()` for substitution
because prompt templates and, more importantly, the JSON payload itself
contain literal curly braces (`{`, `}`), which `str.format()` would try to
interpret as format fields and raise `KeyError`/`ValueError` on. Instead we
do a simple, explicit `str.replace()` on a unique placeholder token.
"""

from __future__ import annotations

from pathlib import Path

# The literal placeholder token that every prompt template may contain.
# Kept as a constant so every module agrees on the exact same token.
JSON_PLACEHOLDER = "{JSON}"


class PromptLoadError(Exception):
    """Raised when a prompt template file is missing or unreadable."""


def load_prompt_template(prompts_dir: str | Path, template_name: str) -> str:
    """
    Read a raw prompt template file from disk.

    Parameters
    ----------
    prompts_dir : str | Path
        Directory containing the `.txt` prompt templates (e.g. `prompts/`).
    template_name : str
        File name of the template, e.g. "statement.txt".

    Returns
    -------
    str
        The raw template text (placeholders not yet substituted).
    """
    template_path = Path(prompts_dir) / template_name

    if not template_path.exists():
        raise PromptLoadError(f"Prompt template not found: {template_path}")

    try:
        return template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptLoadError(f"Failed to read prompt template {template_path}: {exc}") from exc


def render_prompt(template_text: str, json_payload: str) -> str:
    """
    Inject the JSON problem specification into a prompt template.

    Parameters
    ----------
    template_text : str
        Raw template text, expected to contain the `{JSON}` placeholder.
    json_payload : str
        The problem specification, pre-serialized to a JSON string
        (e.g. via `json.dumps(data, indent=2)`).

    Returns
    -------
    str
        The fully rendered prompt, ready to send to the model.
    """
    if JSON_PLACEHOLDER not in template_text:
        # Not fatal: a template author may have a static prompt that
        # doesn't need the JSON (unlikely for this pipeline, but we don't
        # want to hard-fail the whole run over a formatting nuance).
        return template_text

    return template_text.replace(JSON_PLACEHOLDER, json_payload)


def load_and_render_prompt(prompts_dir: str | Path, template_name: str, json_payload: str) -> str:
    """Convenience wrapper: load a template from disk and render it in one call."""
    template_text = load_prompt_template(prompts_dir, template_name)
    return render_prompt(template_text, json_payload)
