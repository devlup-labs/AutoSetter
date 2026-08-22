"""
autosetter.generator
====================
Downstream artifact code generation from the validated `problem.json` specification.

Generates:
- `statement.md`: Formatted problem statement in Markdown.
- `validator.cpp`: Input validator using `testlib.h`.
- `generator.cpp`: Test case generator using `testlib.h`.
- `solution.cpp`: Canonical reference solution in C++17.
- `checker.cpp`: Output correctness checker using `testlib.h`.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from autosetter.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TEXT_MODEL,
    PROMPTS_DIR,
    VENDORED_TESTLIB,
)
from autosetter.llm import OllamaCallError, OllamaClient
from autosetter.prompts import PromptError, load_and_render_prompt


class CodeGenerationError(Exception):
    """Raised when generating or saving downstream code/markdown artifacts fails."""


# Backwards compatibility alias
FileGenerationError = CodeGenerationError


@dataclass(frozen=True)
class ArtifactSpec:
    """Specification describing a single generated artifact."""

    name: str  # e.g. "statement", "validator", "generator", "solution", "checker"
    prompt_template: str  # e.g. "statement.txt"
    output_filename: str  # e.g. "statement.md", "validator.cpp"
    is_cpp: bool = False
    is_testlib: bool = False
    strip_code_fence: bool = True


# Standard artifacts in pipeline generation order
ARTIFACTS: List[ArtifactSpec] = [
    ArtifactSpec(
        name="statement",
        prompt_template="statement.txt",
        output_filename="statement.md",
        is_cpp=False,
        is_testlib=False,
        strip_code_fence=True,
    ),
    ArtifactSpec(
        name="validator",
        prompt_template="validator.txt",
        output_filename="validator.cpp",
        is_cpp=True,
        is_testlib=True,
        strip_code_fence=True,
    ),
    ArtifactSpec(
        name="generator",
        prompt_template="generator.txt",
        output_filename="generator.cpp",
        is_cpp=True,
        is_testlib=True,
        strip_code_fence=True,
    ),
    ArtifactSpec(
        name="solution",
        prompt_template="solution.txt",
        output_filename="solution.cpp",
        is_cpp=True,
        is_testlib=False,
        strip_code_fence=True,
    ),
    ArtifactSpec(
        name="checker",
        prompt_template="checker.txt",
        output_filename="checker.cpp",
        is_cpp=True,
        is_testlib=True,
        strip_code_fence=True,
    ),
    ArtifactSpec(
        name="solution_greedy",
        prompt_template="solution_greedy.txt",
        output_filename="solution.greedy.cpp",
        is_cpp=True,
        is_testlib=False,
        strip_code_fence=True,
    ),
    ArtifactSpec(
        name="solution_brute",
        prompt_template="solution_brute.txt",
        output_filename="solution.brute.cpp",
        is_cpp=True,
        is_testlib=False,
        strip_code_fence=True,
    ),
    ArtifactSpec(
        name="solution_heavy",
        prompt_template="solution_heavy.txt",
        output_filename="solution.heavy.cpp",
        is_cpp=True,
        is_testlib=False,
        strip_code_fence=True,
    ),
]


def strip_code_fence(text: str) -> str:
    """
    Extract clean C++ code or Markdown content from LLM response.
    Handles:
    - Code inside ```cpp ... ``` or ```c++ ... ``` or ``` ... ```
    - Markdown files wrapped in ```markdown ... ```
    - Leading/trailing conversational text (e.g. '### Explanation')
    - Raw un-fenced code.
    """
    stripped = text.strip()

    # 1. Look for tagged code block
    fence_pattern = re.compile(
        r"```(?:cpp|c\+\+|c|markdown|md)?\s*\n(.*?)\n```",
        re.DOTALL | re.IGNORECASE,
    )
    match = fence_pattern.search(stripped)
    if match:
        return match.group(1).strip() + "\n"

    # 2. Look for any triple backtick block
    generic_pattern = re.compile(r"```\s*\n(.*?)\n```", re.DOTALL)
    match = generic_pattern.search(stripped)
    if match:
        return match.group(1).strip() + "\n"

    # 3. If the entire text starts and ends with ```
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip() + "\n"

    return stripped + "\n"


def sanitize_cpp_code(code: str, is_testlib: bool = True) -> str:
    """
    Ensure standard competitive programming headers and `using namespace std;`
    are present in the generated C++ source code to prevent namespace and type errors.
    """
    cleaned = code.strip()
    if not cleaned:
        return code

    lines = cleaned.splitlines()
    has_testlib = any('include "testlib.h"' in line or "<testlib.h>" in line for line in lines)
    has_namespace = any("using namespace std;" in line for line in lines)
    has_iostream = any("<iostream>" in line or "<bits/stdc++.h>" in line for line in lines)
    has_string = any("<string>" in line or "<bits/stdc++.h>" in line for line in lines)
    has_vector = any("<vector>" in line or "<bits/stdc++.h>" in line for line in lines)
    has_algorithm = any("<algorithm>" in line or "<bits/stdc++.h>" in line for line in lines)

    headers_to_add: List[str] = []
    if is_testlib and not has_testlib:
        headers_to_add.append('#include "testlib.h"')
    if not has_iostream:
        headers_to_add.append("#include <iostream>")
    if not has_string:
        headers_to_add.append("#include <string>")
    if not has_vector:
        headers_to_add.append("#include <vector>")
    if not has_algorithm:
        headers_to_add.append("#include <algorithm>")

    if not has_namespace:
        headers_to_add.append("using namespace std;")

    if headers_to_add:
        # If testlib was already at top, insert namespace right after it
        if has_testlib and not has_namespace:
            for idx, line in enumerate(lines):
                if 'include "testlib.h"' in line or "<testlib.h>" in line:
                    lines.insert(idx + 1, "using namespace std;")
                    return "\n".join(lines) + "\n"
        return "\n".join(headers_to_add) + "\n\n" + cleaned + "\n"

    return cleaned + "\n"


def check_cpp_syntax(code: str, include_dir: Path) -> Tuple[bool, str]:
    """
    Fast syntax check using `g++ -fsyntax-only` to verify if C++ code is valid.
    Returns (is_valid, stderr_error_message).
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".cpp", mode="w", delete=False) as tmp:
            tmp.write(code)
            tmp.flush()
            tmp_path = Path(tmp.name)

        cmd = [
            "g++",
            "-std=c++17",
            "-O2",
            "-I",
            str(include_dir),
            "-fsyntax-only",
            str(tmp_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        tmp_path.unlink(missing_ok=True)
        return (res.returncode == 0, res.stderr)
    except Exception as exc:
        return (False, str(exc))


def generate_single_artifact(
    spec: ArtifactSpec,
    json_payload: str,
    client: OllamaClient,
    generated_dir: Path,
    prompts_dir: Path = PROMPTS_DIR,
    text_model: str = DEFAULT_TEXT_MODEL,
    max_retries: int = DEFAULT_MAX_RETRIES,
    include_dir: Optional[Path] = None,
    feedback_context: Optional[str] = None,
) -> Path:
    """
    Generate, sanitize, syntax-verify, and write a single artifact to disk.
    If C++ syntax compilation fails, iteratively retries with compiler feedback.
    """
    testlib_include = include_dir or VENDORED_TESTLIB.parent

    try:
        rendered_prompt = load_and_render_prompt(
            spec.prompt_template, json_payload, prompts_dir=prompts_dir
        )
    except PromptError as exc:
        raise CodeGenerationError(
            f"Failed to load prompt template for '{spec.name}': {exc}"
        ) from exc

    current_prompt = rendered_prompt
    if feedback_context:
        current_prompt = (
            f"⚠️ PREVIOUS ATTEMPT FAILED ⚠️\n"
            f"Your previous attempt to generate this file was rejected by the sandbox validation pipeline.\n"
            f"Here is the exact error/crash log from the sandbox:\n\n"
            f"{feedback_context.strip()}\n\n"
            f"=========================================\n\n"
            f"{current_prompt}"
        )

    last_error = ""

    for attempt in range(max_retries + 1):
        try:
            raw_reply = client.chat_text(
                prompt=current_prompt,
                model=text_model,
                temperature=0.2,
            )
        except OllamaCallError as exc:
            raise CodeGenerationError(
                f"Ollama text inference failed while generating '{spec.name}': {exc}"
            ) from exc

        content = strip_code_fence(raw_reply) if spec.strip_code_fence else raw_reply
        if spec.is_cpp:
            content = sanitize_cpp_code(content, is_testlib=spec.is_testlib)

        # If not C++, or if it's C++ and syntax check passes, we're done
        if not spec.is_cpp:
            break

        is_valid, err = check_cpp_syntax(content, testlib_include)
        if is_valid:
            break

        last_error = err
        # Prepare repair prompt for next iteration
        current_prompt = (
            f"The generated C++ code for '{spec.output_filename}' failed to compile with g++:\n\n"
            f"--- COMPILER ERRORS ---\n{err.strip()[:1500]}\n\n"
            f"--- CURRENT CODE ---\n{content.strip()}\n\n"
            f"Please fix all compiler errors. Output ONLY the complete, corrected, compilable C++ code."
        )

    output_path = generated_dir / spec.output_filename
    try:
        generated_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise CodeGenerationError(
            f"Failed to write generated artifact {output_path}: {exc}"
        ) from exc

    return output_path


def generate_all_artifacts(
    problem_data: Dict[str, Any],
    generated_dir: str | Path,
    client: OllamaClient,
    prompts_dir: str | Path = PROMPTS_DIR,
    text_model: str = DEFAULT_TEXT_MODEL,
    max_retries: int = DEFAULT_MAX_RETRIES,
    include_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    targets: Optional[List[str]] = None,
    feedback_context: Optional[Dict[str, str]] = None,
) -> Dict[str, Path]:
    """
    Generate statement.md and all four C++ artifacts (validator, generator, solution, checker).

    Parameters
    ----------
    problem_data : Dict[str, Any]
        Structured problem specification dictionary.
    generated_dir : str | Path
        Target directory to write the generated files.
    client : OllamaClient
        Configured Ollama client.
    prompts_dir : str | Path
        Directory containing prompt templates.
    text_model : str
        Text LLM model name.
    max_retries : int
        Maximum self-healing retries upon compilation failure.
    include_dir : Optional[Path]
        Directory containing testlib.h.
    progress_callback : Optional[Callable[[str], None]]
        Progress reporting callback.

    Returns
    -------
    Dict[str, Path]
        Mapping of artifact name to written file path.
    """
    prompts_dir_path = Path(prompts_dir)
    generated_dir_path = Path(generated_dir)

    json_payload = json.dumps(problem_data, indent=2, ensure_ascii=False)
    feedback_context = feedback_context or {}
    written_paths: Dict[str, Path] = {}

    for spec in ARTIFACTS:
        if targets is not None and spec.name not in targets:
            continue

        if progress_callback:
            progress_callback(f"Generating {spec.name}...")

        path = generate_single_artifact(
            spec=spec,
            json_payload=json_payload,
            client=client,
            generated_dir=generated_dir_path,
            prompts_dir=prompts_dir_path,
            text_model=text_model,
            max_retries=max_retries,
            include_dir=include_dir,
            feedback_context=feedback_context.get(spec.name),
        )
        written_paths[spec.name] = path

    return written_paths
