"""
autosetter.config
=================
Centralized configuration management for AutoSetter.

All defaults can be overridden via environment variables or CLI arguments.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Project Paths
# ---------------------------------------------------------------------------
# The directory containing the autosetter package
PACKAGE_ROOT = Path(__file__).resolve().parent

# The repository root directory
PROJECT_ROOT = PACKAGE_ROOT.parent

# Standard directory locations
PROMPTS_DIR = PACKAGE_ROOT / "prompts"
INCLUDE_DIR = PACKAGE_ROOT / "include"
VENDORED_TESTLIB = INCLUDE_DIR / "testlib.h"
DEFAULT_OUT_DIR = PROJECT_ROOT / "out"

# Upstream testlib download URL (used only for manual maintenance)
TESTLIB_URL = "https://raw.githubusercontent.com/MikeMirzayanov/testlib/master/testlib.h"

# ---------------------------------------------------------------------------
# Default Constants & Environment Variable Overrides
# ---------------------------------------------------------------------------
DEFAULT_VISION_MODEL = os.environ.get("AUTOSETTER_VISION_MODEL", "qwen2.5vl:3b")
DEFAULT_TEXT_MODEL = os.environ.get("AUTOSETTER_TEXT_MODEL", "qwen2.5-coder:1.5b")
DEFAULT_OLLAMA_HOST = (
    os.environ.get("OLLAMA_HOST")
    or os.environ.get("AUTOSETTER_OLLAMA_HOST", "http://localhost:11434")
)

# ---------------------------------------------------------------------------
# HuggingFace Second Model (Fine-tuned Qwen 7B for Codeforces artifacts)
# ---------------------------------------------------------------------------
# This model is used ONLY for generating: statement.tex, solution.cpp,
# brute.cpp, checker.cpp.  All other artifacts remain on the Ollama path.
#
# AUTOSETTER_HF_MODEL      — HuggingFace Hub model identifier or local path.
# AUTOSETTER_HF_DEVICE      — Device placement: "auto" | "cuda" | "cpu".
# AUTOSETTER_HF_MAX_TOKENS  — Maximum new tokens per generation call.
# ---------------------------------------------------------------------------
DEFAULT_HF_MODEL_NAME = os.environ.get(
    "AUTOSETTER_HF_MODEL", "winglian/qwen-2.5-codeforces-cot-kd-v1"
)
DEFAULT_HF_DEVICE = os.environ.get("AUTOSETTER_HF_DEVICE", "auto")
DEFAULT_HF_MAX_NEW_TOKENS = int(os.environ.get("AUTOSETTER_HF_MAX_TOKENS", "4096"))

# C++ Compilation
CPP_STANDARD = os.environ.get("AUTOSETTER_CPP_STANDARD", "c++17")
CPP_OPTIMIZATION = os.environ.get("AUTOSETTER_CPP_OPTIMIZATION", "-O2")
DEFAULT_COMPILER = os.environ.get("AUTOSETTER_COMPILER", "g++")

# Pipeline Defaults
DEFAULT_NUM_TESTS = int(os.environ.get("AUTOSETTER_NUM_TESTS", "10"))
DEFAULT_MAX_RETRIES = int(os.environ.get("AUTOSETTER_MAX_RETRIES", "3"))
DEFAULT_EXECUTION_TIMEOUT = int(os.environ.get("AUTOSETTER_TIMEOUT", "5"))
DEFAULT_COMPILE_TIMEOUT = int(os.environ.get("AUTOSETTER_COMPILE_TIMEOUT", "60"))

# Vision / Image Processing
PDF_RENDER_DPI = int(os.environ.get("AUTOSETTER_PDF_DPI", "200"))
SUPPORTED_RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = SUPPORTED_RASTER_EXTENSIONS | SUPPORTED_PDF_EXTENSIONS

# Codeforces Polygon API
POLYGON_API_URL = os.environ.get("POLYGON_API_URL", "https://polygon.codeforces.com/api/")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
POLYGON_SECRET = os.environ.get("POLYGON_SECRET", "")


@dataclass
class Config:
    """Runtime configuration container for an AutoSetter pipeline run."""

    # ── Existing Ollama / pipeline configuration (UNCHANGED) ──
    vision_model: str = DEFAULT_VISION_MODEL
    text_model: str = DEFAULT_TEXT_MODEL
    ollama_host: str = DEFAULT_OLLAMA_HOST
    num_tests: int = DEFAULT_NUM_TESTS
    max_retries: int = DEFAULT_MAX_RETRIES
    execution_timeout: int = DEFAULT_EXECUTION_TIMEOUT
    compile_timeout: int = DEFAULT_COMPILE_TIMEOUT
    prompts_dir: Path = PROMPTS_DIR
    out_dir: Path = DEFAULT_OUT_DIR
    debug: bool = False
    skip_validation: bool = False

    # ── HuggingFace second model configuration (NEW) ──
    # These fields control the fine-tuned Qwen 7B model used for generating
    # statement.tex, solution.cpp, brute.cpp, and checker.cpp.
    hf_model_name: str = DEFAULT_HF_MODEL_NAME  # HuggingFace Hub model ID
    hf_device: str = DEFAULT_HF_DEVICE  # "auto", "cuda", or "cpu"
    hf_max_new_tokens: int = DEFAULT_HF_MAX_NEW_TOKENS  # Max tokens per generation

    @classmethod
    def from_env(cls) -> Config:
        """Create a Config object populated from environment variables."""
        return cls(
            vision_model=DEFAULT_VISION_MODEL,
            text_model=DEFAULT_TEXT_MODEL,
            ollama_host=DEFAULT_OLLAMA_HOST,
            num_tests=DEFAULT_NUM_TESTS,
            max_retries=DEFAULT_MAX_RETRIES,
            execution_timeout=DEFAULT_EXECUTION_TIMEOUT,
            compile_timeout=DEFAULT_COMPILE_TIMEOUT,
            prompts_dir=PROMPTS_DIR,
            out_dir=DEFAULT_OUT_DIR,
            debug=bool(os.environ.get("AUTOSETTER_DEBUG")),
            # ── Populate HF model config from environment variables ──
            hf_model_name=DEFAULT_HF_MODEL_NAME,
            hf_device=DEFAULT_HF_DEVICE,
            hf_max_new_tokens=DEFAULT_HF_MAX_NEW_TOKENS,
        )
