"""
AutoSetter
==========
AI-powered automated competitive programming problem packaging engine.
"""

from __future__ import annotations

__version__ = "0.2.0"

from autosetter.cli import (
    AutoSetterError,
    AutoSetupError,
    PipelineResult,
    generate_from_image,
    main,
)
from autosetter.config import Config
from autosetter.extractor import (
    JSONExtractionError,
    generate_problem_json,
    save_problem_json,
)
from autosetter.generator import (
    ArtifactSpec,
    CodeGenerationError,
    FileGenerationError,
    HF_MODEL_ARTIFACTS,
    generate_all_artifacts,
)
# ── NEW: HuggingFace model client for the fine-tuned Qwen 7B ──
from autosetter.hf_model import HFModelClient, HFModelError
from autosetter.llm import OllamaCallError, OllamaClient
from autosetter.packager import Packager, PackagerError
from autosetter.pipeline import (
    PipelineError,
    TestCase,
    TestPipeline,
    TestPipelineError,
    TestReport,
)
from autosetter.polygon import (
    PolygonAPIError,
    PolygonClient,
    upload_problem_package,
)
from autosetter.sandbox import (
    ExecutionResult,
    SandboxError,
    SandboxHTTPClient,
    SandboxLocalClient,
    ensure_testlib,
    refresh_vendored_testlib,
)
from autosetter.vision import ImageParsingError, load_image_as_base64

__all__ = [
    "__version__",
    "main",
    "generate_from_image",
    "PipelineResult",
    "AutoSetterError",
    "AutoSetupError",
    "Config",
    "OllamaClient",
    "OllamaCallError",
    # ── NEW: HuggingFace model client exports ──
    "HFModelClient",
    "HFModelError",
    "HF_MODEL_ARTIFACTS",
    "load_image_as_base64",
    "ImageParsingError",
    "generate_problem_json",
    "save_problem_json",
    "JSONExtractionError",
    "generate_all_artifacts",
    "ArtifactSpec",
    "CodeGenerationError",
    "FileGenerationError",
    "SandboxLocalClient",
    "SandboxHTTPClient",
    "SandboxError",
    "ExecutionResult",
    "ensure_testlib",
    "refresh_vendored_testlib",
    "TestPipeline",
    "TestReport",
    "TestCase",
    "PipelineError",
    "TestPipelineError",
    "Packager",
    "PackagerError",
    "PolygonClient",
    "PolygonAPIError",
    "upload_problem_package",
]
