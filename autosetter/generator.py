"""
autosetter.generator
====================
Downstream artifact code generation from the validated `problem.json` specification.

This module orchestrates generation of ALL artifacts needed by the AutoSetter
pipeline, using the Ollama text model for text and code generation.
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
# Import configuration defaults
# ─────────────────────────────────────────────────────────────────────────────
from autosetter.config import (
    DEFAULT_MAX_RETRIES,     # Max retry attempts on C++ syntax failure
    DEFAULT_TEXT_MODEL,      # Default Ollama text model name
    PROMPTS_DIR,             # Directory containing prompt templates
    VENDORED_TESTLIB,        # Path to vendored testlib.h header
)

# ─────────────────────────────────────────────────────────────────────────────
# Import the Ollama client
# ─────────────────────────────────────────────────────────────────────────────
from autosetter.llm import OllamaCallError, OllamaClient

# ─────────────────────────────────────────────────────────────────────────────
# Import prompt template utilities
# ─────────────────────────────────────────────────────────────────────────────
from autosetter.prompts import PromptError, load_and_render_prompt

from autosetter.extractor import JSONExtractionError, parse_model_json, strip_code_fence
from autosetter.testgen import (
    SpecError,
    TestGenError,
    check_samples,
    generate_test,
    load_spec,
)


# =============================================================================
# Exception Classes
# =============================================================================


class CodeGenerationError(Exception):
    """Raised when generating or saving downstream code/markdown artifacts fails."""





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
    is_test_spec: bool = False     # True for the Z3 test spec (JSON, checked against samples)


ARTIFACTS: List[ArtifactSpec] = [
    ArtifactSpec(
        name="statement",
        prompt_template="statement.txt",
        output_filename="statement.md",
        is_cpp=False,
        is_testlib=False,
        strip_code_fence=True,
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
    # ── Test generator: a declarative input spec that autosetter.testgen
    # turns into tests with Z3. Named "generator" so validation feedback and
    # self-healing target it like any other generator. ──
    ArtifactSpec(
        name="generator",
        prompt_template="test_spec.txt",
        output_filename="test_spec.json",
        is_cpp=False,
        is_testlib=False,
        strip_code_fence=False,
        is_test_spec=True,
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
    # ── Ollama-routed artifact: Deliberately wrong solution (UNCHANGED) ──
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



def sanitize_cpp_code(code: str, is_testlib: bool = True) -> str:
    """
    Ensure standard competitive programming headers and `using namespace std;`
    are present in the generated C++ source code to prevent namespace and type errors.
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


def prepare_test_spec(raw_reply: str, json_payload: str) -> Tuple[str, str]:
    """
    Parse and verify a model-written test spec.

    Returns (content_to_write, error). The spec must be valid, accept every
    official sample input, and generate the smallest and largest tests
    (seeds 1 and 2) without error. `error` is empty on success.
    """
    try:
        data = parse_model_json(raw_reply)
    except JSONExtractionError as exc:
        return raw_reply.strip() + "\n", f"The reply is not a JSON object: {str(exc)[:500]}"

    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    try:
        test_spec = load_spec(data)
    except SpecError as exc:
        return content, f"The spec is invalid:\n{exc}"

    samples = (json.loads(json_payload) or {}).get("samples") or []
    problems = check_samples(test_spec, samples)
    if problems:
        return content, (
            "The spec rejects the problem's official sample input(s), so it does not "
            "describe the input correctly:\n" + "\n".join(f"- {p}" for p in problems)
        )

    for seed in (1, 2):
        try:
            generate_test(test_spec, seed)
        except TestGenError as exc:
            return content, f"Generating a test from the spec failed (seed {seed}):\n{exc}"

    return content, ""


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
    using the Ollama backend.

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
    base_prompt = current_prompt

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

        # ── Test spec: validate against the schema and the official samples ──
        if spec.is_test_spec:
            content, last_error = prepare_test_spec(raw_reply, json_payload)
            if not last_error:
                break
            current_prompt = (
                f"{base_prompt}\n\n=========================================\n"
                f"Your previous test spec was rejected:\n{last_error[:2000]}\n\n"
                f"--- PREVIOUS SPEC ---\n{content.strip()[:4000]}\n\n"
                "Fix every problem listed above. Output ONLY the corrected JSON spec."
            )
            continue

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
# Orchestrator: Generate All Artifacts
# =============================================================================


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
    Generate all downstream artifacts from problem.json.

    Parameters
    ----------
    problem_data : Dict[str, Any]
        Structured problem specification dictionary (from problem.json).
    generated_dir : str | Path
        Target directory to write the generated files.
    client : OllamaClient
        Configured Ollama client.
    prompts_dir : str | Path
        Directory containing prompt templates.
    text_model : str
        Ollama text LLM model name.
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

    # ── Serialize problem data to JSON string for prompt rendering ──
    json_payload = json.dumps(problem_data, indent=2, ensure_ascii=False)
    feedback_context = feedback_context or {}

    written_paths: Dict[str, Path] = {}

    # ── Generate each artifact ──
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

