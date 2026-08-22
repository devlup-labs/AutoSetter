"""
autosetter.generator
====================
Downstream artifact code generation from the validated `problem.json` specification.

This module orchestrates generation of ALL artifacts needed by the AutoSetter
pipeline. It splits generation between TWO model backends:

    ┌─────────────────────────────────────────────────────────────────────┐
    │  HuggingFace Backend (winglian/qwen-2.5-codeforces-cot-kd-v1)     │
    │  Fine-tuned Qwen 7B, local GPU inference via HF Transformers.     │
    │                                                                     │
    │  Generates ONLY:                                                    │
    │    • statement    → statement.tex   (LaTeX problem statement)       │
    │    • solution     → solution.cpp    (optimal reference solution)    │
    │    • solution_brute → brute.cpp     (brute-force for stress test)  │
    │    • checker      → checker.cpp     (testlib.h output checker)     │
    └─────────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────────────┐
    │  Ollama Backend (UNCHANGED — existing qwen2.5-coder:1.5b)          │
    │  All other artifacts continue through the original Ollama path.    │
    │                                                                     │
    │  Generates:                                                         │
    │    • validator     → validator.cpp                                  │
    │    • generator     → generator.cpp                                  │
    │    • solution_wa   → solution.wa.cpp                               │
    │    • solution_tle  → solution.tle.cpp                              │
    └─────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Import configuration defaults used by both generation backends.
# ─────────────────────────────────────────────────────────────────────────────
from autosetter.config import (
    DEFAULT_MAX_RETRIES,     # Max retry attempts on C++ syntax failure
    DEFAULT_TEXT_MODEL,      # Default Ollama text model name
    PROMPTS_DIR,             # Directory containing prompt templates
    VENDORED_TESTLIB,        # Path to vendored testlib.h header
)

# ─────────────────────────────────────────────────────────────────────────────
# Import the EXISTING Ollama client (used for artifacts NOT handled by HF).
# This import is UNCHANGED from the original code.
# ─────────────────────────────────────────────────────────────────────────────
from autosetter.llm import OllamaCallError, OllamaClient

# ─────────────────────────────────────────────────────────────────────────────
# Import the NEW HuggingFace model client (used for the four HF artifacts).
# ─────────────────────────────────────────────────────────────────────────────
from autosetter.hf_model import HFModelClient, HFModelError

# ─────────────────────────────────────────────────────────────────────────────
# Import prompt template utilities (shared by both backends).
# ─────────────────────────────────────────────────────────────────────────────
from autosetter.prompts import PromptError, load_and_render_prompt


# =============================================================================
# Exception Classes
# =============================================================================


class CodeGenerationError(Exception):
    """Raised when generating or saving downstream code/markdown artifacts fails."""


# Backwards compatibility alias
FileGenerationError = CodeGenerationError


# =============================================================================
# Artifact Specification
# =============================================================================


@dataclass(frozen=True)
class ArtifactSpec:
    """
    Specification describing a single generated artifact.

    Each artifact has a name, a prompt template file, an output filename,
    and flags indicating whether it is C++ code and/or uses testlib.h.
    """

    name: str              # e.g. "statement", "validator", "generator", "solution", "checker"
    prompt_template: str   # e.g. "statement.txt" — filename in the prompts/ directory
    output_filename: str   # e.g. "statement.tex", "validator.cpp"
    is_cpp: bool = False           # True if the artifact is C++ source code
    is_testlib: bool = False       # True if the artifact requires testlib.h
    strip_code_fence: bool = True  # True to strip ```...``` code fences from LLM output


# =============================================================================
# Artifact Routing: Which artifacts go to which model backend
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# HF_MODEL_ARTIFACTS — The set of artifact names that are generated by the
# fine-tuned Qwen 7B HuggingFace model. These four artifacts are the ONLY
# ones handled by the new HF backend:
#   - "statement"       → statement.tex   (LaTeX, NOT Markdown)
#   - "solution"        → solution.cpp    (optimal reference solution)
#   - "solution_brute"  → brute.cpp       (brute-force stress test solution)
#   - "checker"         → checker.cpp     (testlib.h output checker)
#
# All other artifact names are routed to the existing Ollama backend.
# ─────────────────────────────────────────────────────────────────────────────
HF_MODEL_ARTIFACTS = {"statement", "solution", "solution_brute", "checker"}


# =============================================================================
# Standard Artifact Definitions (pipeline generation order)
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# ARTIFACTS list — defines ALL artifacts produced during Stage 2 of the
# AutoSetter pipeline. The order matters: artifacts are generated sequentially
# in the order they appear here.
#
# CHANGES from the original code:
#   1. "statement" now outputs "statement.tex" (was "statement.md")
#      because the HF model generates LaTeX, not Markdown.
#   2. "solution_brute" now outputs "brute.cpp" (was "solution.brute.cpp")
#      per the new artifact naming convention.
#   3. All other artifact specs are IDENTICAL to the original code.
# ─────────────────────────────────────────────────────────────────────────────
ARTIFACTS: List[ArtifactSpec] = [
    # ── HF-routed artifact: LaTeX problem statement ──
    # Generated by the fine-tuned Qwen 7B model.
    # Output changed from statement.md to statement.tex.
    ArtifactSpec(
        name="statement",
        prompt_template="statement.txt",
        output_filename="statement.tex",     # CHANGED: was "statement.md"
        is_cpp=False,                        # LaTeX, not C++
        is_testlib=False,                    # Does not use testlib.h
        strip_code_fence=True,               # Strip any ```...``` fences from LLM output
    ),
    # ── Ollama-routed artifact: Input validator (UNCHANGED) ──
    # This artifact continues to use the existing Ollama backend.
    ArtifactSpec(
        name="validator",
        prompt_template="validator.txt",
        output_filename="validator.cpp",
        is_cpp=True,
        is_testlib=True,
        strip_code_fence=True,
    ),
    # ── Ollama-routed artifact: Test case generator (UNCHANGED) ──
    # This artifact continues to use the existing Ollama backend.
    ArtifactSpec(
        name="generator",
        prompt_template="generator.txt",
        output_filename="generator.cpp",
        is_cpp=True,
        is_testlib=True,
        strip_code_fence=True,
    ),
    # ── HF-routed artifact: Optimal reference solution ──
    # Generated by the fine-tuned Qwen 7B model.
    ArtifactSpec(
        name="solution",
        prompt_template="solution.txt",
        output_filename="solution.cpp",
        is_cpp=True,
        is_testlib=False,
        strip_code_fence=True,
    ),
    # ── HF-routed artifact: Output checker ──
    # Generated by the fine-tuned Qwen 7B model.
    ArtifactSpec(
        name="checker",
        prompt_template="checker.txt",
        output_filename="checker.cpp",
        is_cpp=True,
        is_testlib=True,
        strip_code_fence=True,
    ),
    # ── Ollama-routed artifact: Deliberately wrong solution (UNCHANGED) ──
    ArtifactSpec(
        name="solution_greedy",
        prompt_template="solution_greedy.txt",
        output_filename="solution.greedy.cpp",
        is_cpp=True,
        is_testlib=False,
        strip_code_fence=True,
    ),
    # ── HF-routed artifact: Brute-force solution ──
    # Generated by the fine-tuned Qwen 7B model.
    # Output changed from solution.brute.cpp to brute.cpp.
    ArtifactSpec(
        name="solution_brute",
        prompt_template="solution_brute.txt",
        output_filename="brute.cpp",         # CHANGED: was "solution.brute.cpp"
        is_cpp=True,
        is_testlib=False,
        strip_code_fence=True,
    ),
    # ── Ollama-routed artifact: TLE solution (UNCHANGED) ──
    ArtifactSpec(
        name="solution_heavy",
        prompt_template="solution_heavy.txt",
        output_filename="solution.heavy.cpp",
        is_cpp=True,
        is_testlib=False,
        strip_code_fence=True,
    ),
]


# =============================================================================
# Text Processing Utilities
# =============================================================================


def strip_code_fence(text: str) -> str:
    """
    Extract clean C++ code or LaTeX/Markdown content from LLM response.

    Both the Ollama and HuggingFace models may wrap their output in markdown
    code fences (```cpp ... ``` or ```latex ... ```). This function strips
    those fences and returns only the content inside.

    Handles:
    - Code inside ```cpp ... ``` or ```c++ ... ``` or ``` ... ```
    - LaTeX files wrapped in ```latex ... ``` or ```tex ... ```
    - Markdown files wrapped in ```markdown ... ```
    - Leading/trailing conversational text (e.g. '### Explanation')
    - Raw un-fenced code.
    """
    stripped = text.strip()

    # 1. Look for tagged code block (cpp, c++, c, latex, tex, markdown, md)
    fence_pattern = re.compile(
        r"```(?:cpp|c\+\+|c|latex|tex|markdown|md)?\s*\n(.*?)\n```",
        re.DOTALL | re.IGNORECASE,
    )
    match = fence_pattern.search(stripped)
    if match:
        return match.group(1).strip() + "\n"

    # 2. Look for any triple backtick block (language tag unrecognized or absent)
    generic_pattern = re.compile(r"```\s*\n(.*?)\n```", re.DOTALL)
    match = generic_pattern.search(stripped)
    if match:
        return match.group(1).strip() + "\n"

    # 3. If the entire text starts and ends with ```
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip() + "\n"

    # 4. Return as-is if no fences found
    return stripped + "\n"


def sanitize_cpp_code(code: str, is_testlib: bool = True) -> str:
    """
    Ensure standard competitive programming headers and `using namespace std;`
    are present in the generated C++ source code to prevent namespace and type errors.

    This function is shared by BOTH the Ollama and HuggingFace generation paths —
    it runs on any artifact where is_cpp=True, regardless of which model produced it.
    """
    cleaned = code.strip()
    if not cleaned:
        return code

    lines = cleaned.splitlines()

    # ── Check which standard headers/namespace are already present ──
    has_testlib = any('include "testlib.h"' in line or "<testlib.h>" in line for line in lines)
    has_namespace = any("using namespace std;" in line for line in lines)
    has_iostream = any("<iostream>" in line or "<bits/stdc++.h>" in line for line in lines)
    has_string = any("<string>" in line or "<bits/stdc++.h>" in line for line in lines)
    has_vector = any("<vector>" in line or "<bits/stdc++.h>" in line for line in lines)
    has_algorithm = any("<algorithm>" in line or "<bits/stdc++.h>" in line for line in lines)

    # ── Build list of missing headers to prepend ──
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

    This function is shared by BOTH the Ollama and HuggingFace generation paths.
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


# =============================================================================
# Single Artifact Generation — Ollama Backend (UNCHANGED LOGIC)
# =============================================================================


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
    Generate, sanitize, syntax-verify, and write a single artifact to disk
    using the EXISTING Ollama backend.

    This function is used for artifacts that are NOT in HF_MODEL_ARTIFACTS:
        - validator.cpp
        - generator.cpp
        - solution.wa.cpp
        - solution.tle.cpp

    The logic here is IDENTICAL to the original generate_single_artifact()
    from the pre-modification codebase. No behavioral changes have been made.

    If C++ syntax compilation fails, iteratively retries with compiler feedback.
    """
    # ── Resolve the testlib.h include directory ──
    testlib_include = include_dir or VENDORED_TESTLIB.parent

    # ── Load and render the prompt template with the problem JSON ──
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

    # ── Retry loop: generate → check syntax → repair if needed ──
    for attempt in range(max_retries + 1):
        # ── Call the Ollama text model for inference ──
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

        # ── Post-process: strip code fences and sanitize C++ headers ──
        content = strip_code_fence(raw_reply) if spec.strip_code_fence else raw_reply
        if spec.is_cpp:
            content = sanitize_cpp_code(content, is_testlib=spec.is_testlib)

        # ── If not C++, or if it's C++ and syntax check passes, we're done ──
        if not spec.is_cpp:
            break

        is_valid, err = check_cpp_syntax(content, testlib_include)
        if is_valid:
            break

        last_error = err
        # ── Prepare repair prompt for next iteration ──
        current_prompt = (
            f"The generated C++ code for '{spec.output_filename}' failed to compile with g++:\n\n"
            f"--- COMPILER ERRORS ---\n{err.strip()[:1500]}\n\n"
            f"--- CURRENT CODE ---\n{content.strip()}\n\n"
            f"Please fix all compiler errors. Output ONLY the complete, corrected, compilable C++ code."
        )

    # ── Write the final artifact to disk ──
    output_path = generated_dir / spec.output_filename
    try:
        generated_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise CodeGenerationError(
            f"Failed to write generated artifact {output_path}: {exc}"
        ) from exc

    return output_path


# =============================================================================
# Single Artifact Generation — HuggingFace Backend (NEW)
# =============================================================================


def generate_single_artifact_hf(
    spec: ArtifactSpec,
    json_payload: str,
    hf_client: HFModelClient,
    generated_dir: Path,
    prompts_dir: Path = PROMPTS_DIR,
    max_retries: int = DEFAULT_MAX_RETRIES,
    include_dir: Optional[Path] = None,
) -> Path:
    """
    Generate, sanitize, syntax-verify, and write a single artifact to disk
    using the NEW HuggingFace Transformers backend.

    This function is used ONLY for artifacts in HF_MODEL_ARTIFACTS:
        - statement.tex    (LaTeX problem statement)
        - solution.cpp     (optimal reference solution)
        - brute.cpp        (brute-force solution)
        - checker.cpp      (testlib.h output checker)

    The generation flow mirrors generate_single_artifact() (Ollama version):
        1. Load and render prompt template.
        2. Call the model for inference (HF instead of Ollama).
        3. Strip code fences from the response.
        4. Sanitize C++ headers (if applicable).
        5. Syntax-check C++ code (if applicable).
        6. Retry with compiler feedback on failure.
        7. Write the final artifact to disk.

    Parameters
    ----------
    spec : ArtifactSpec
        Specification for the artifact to generate.
    json_payload : str
        Serialized problem.json as a string.
    hf_client : HFModelClient
        Initialized HuggingFace model client.
    generated_dir : Path
        Target directory to write the generated file.
    prompts_dir : Path
        Directory containing prompt template files.
    max_retries : int
        Maximum self-healing retries upon C++ compilation failure.
    include_dir : Optional[Path]
        Directory containing testlib.h for compilation checks.

    Returns
    -------
    Path
        Path to the written artifact file.
    """
    # ── Resolve the testlib.h include directory ──
    testlib_include = include_dir or VENDORED_TESTLIB.parent

    # ── Step 1: Load and render the prompt template with the problem JSON ──
    try:
        rendered_prompt = load_and_render_prompt(
            spec.prompt_template, json_payload, prompts_dir=prompts_dir
        )
    except PromptError as exc:
        raise CodeGenerationError(
            f"Failed to load prompt template for '{spec.name}': {exc}"
        ) from exc

    current_prompt = rendered_prompt
    last_error = ""

    # ── Step 2–6: Retry loop: generate → check syntax → repair if needed ──
    for attempt in range(max_retries + 1):
        # ── Call the HuggingFace model for inference ──
        # This calls HFModelClient.generate_text(), which runs the fine-tuned
        # Qwen 7B model locally on GPU via HuggingFace Transformers.
        try:
            raw_reply = hf_client.generate_text(
                prompt=current_prompt,
                temperature=0.2,  # Same temperature as the Ollama path for consistency
            )
        except HFModelError as exc:
            raise CodeGenerationError(
                f"HuggingFace model inference failed while generating "
                f"'{spec.name}': {exc}"
            ) from exc

        # ── Post-process: strip code fences from the raw LLM output ──
        content = strip_code_fence(raw_reply) if spec.strip_code_fence else raw_reply

        # ── Sanitize C++ headers if this artifact is C++ source code ──
        if spec.is_cpp:
            content = sanitize_cpp_code(content, is_testlib=spec.is_testlib)

        # ── If not C++ (e.g. statement.tex), skip syntax check and finish ──
        if not spec.is_cpp:
            break

        # ── Syntax-check the generated C++ code ──
        is_valid, err = check_cpp_syntax(content, testlib_include)
        if is_valid:
            break  # C++ compiles — we're done

        last_error = err

        # ── Prepare repair prompt for next iteration ──
        # On syntax failure, we feed the compiler errors back to the model
        # and ask it to fix the code. This self-healing loop runs up to
        # max_retries additional times.
        current_prompt = (
            f"The generated C++ code for '{spec.output_filename}' failed to "
            f"compile with g++:\n\n"
            f"--- COMPILER ERRORS ---\n{err.strip()[:1500]}\n\n"
            f"--- CURRENT CODE ---\n{content.strip()}\n\n"
            f"Please fix all compiler errors. Output ONLY the complete, "
            f"corrected, compilable C++ code."
        )

    # ── Step 7: Write the final artifact to disk ──
    output_path = generated_dir / spec.output_filename
    try:
        generated_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise CodeGenerationError(
            f"Failed to write generated artifact {output_path}: {exc}"
        ) from exc

    return output_path


# =============================================================================
# Orchestrator: Generate All Artifacts (dispatches to correct backend)
# =============================================================================


def generate_all_artifacts(
    problem_data: Dict[str, Any],
    generated_dir: str | Path,
    client: OllamaClient,
    hf_client: Optional[HFModelClient] = None,
    prompts_dir: str | Path = PROMPTS_DIR,
    text_model: str = DEFAULT_TEXT_MODEL,
    max_retries: int = DEFAULT_MAX_RETRIES,
    include_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    targets: Optional[List[str]] = None,
    feedback_context: Optional[Dict[str, str]] = None,
) -> Dict[str, Path]:
    """
    Generate all downstream artifacts from problem.json.

    This is the main entry point called by cli.py. It iterates over the
    ARTIFACTS list and dispatches each artifact to the correct model backend:

        - Artifacts in HF_MODEL_ARTIFACTS → generate_single_artifact_hf()
          (uses the fine-tuned Qwen 7B via HuggingFace Transformers)

        - All other artifacts → generate_single_artifact()
          (uses the existing Ollama qwen2.5-coder:1.5b — UNCHANGED)

    Parameters
    ----------
    problem_data : Dict[str, Any]
        Structured problem specification dictionary (from problem.json).
    generated_dir : str | Path
        Target directory to write the generated files.
    client : OllamaClient
        Configured Ollama client (used for non-HF artifacts).
    hf_client : Optional[HFModelClient]
        Configured HuggingFace model client (used for HF artifacts).
        If None, a default HFModelClient will be instantiated automatically.
    prompts_dir : str | Path
        Directory containing prompt templates.
    text_model : str
        Ollama text LLM model name (only used for non-HF artifacts).
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
        e.g. {"statement": Path("out/generated/statement.tex"),
              "solution": Path("out/generated/solution.cpp"), ...}
    """
    prompts_dir_path = Path(prompts_dir)
    generated_dir_path = Path(generated_dir)

    # ── Serialize problem data to JSON string for prompt rendering ──
    json_payload = json.dumps(problem_data, indent=2, ensure_ascii=False)
    feedback_context = feedback_context or {}

    # ── Initialize the HuggingFace client if not provided ──
    if hf_client is None:
        hf_client = HFModelClient()

    written_paths: Dict[str, Path] = {}

    # ── Generate each artifact, dispatching to the correct backend ──
    for spec in ARTIFACTS:
        if targets is not None and spec.name not in targets:
            continue

        if progress_callback:
            progress_callback(f"Generating {spec.name}...")

        if spec.name in HF_MODEL_ARTIFACTS:
            if progress_callback:
                progress_callback(
                    f"  → Using HuggingFace model for '{spec.name}'..."
                )
            path = generate_single_artifact_hf(
                spec=spec,
                json_payload=json_payload,
                hf_client=hf_client,
                generated_dir=generated_dir_path,
                prompts_dir=prompts_dir_path,
                max_retries=max_retries,
                include_dir=include_dir,
            )
        else:
            if progress_callback:
                progress_callback(
                    f"  → Using Ollama model for '{spec.name}'..."
                )
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

    # ── Return the complete mapping of artifact name → file path ──
    return written_paths
