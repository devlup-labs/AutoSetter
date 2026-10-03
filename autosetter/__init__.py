"""
AutoSetter
==========
AI-powered automated competitive programming problem packaging engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Auto-discover project .venv site-packages if dependencies are not in current environment
try:
    import ollama  # noqa: F401
except ImportError:
    _venv_site = Path(__file__).resolve().parent.parent / ".venv" / "lib"
    for _site_packages in _venv_site.glob("python*/site-packages"):
        if _site_packages.is_dir() and str(_site_packages) not in sys.path:
            sys.path.insert(0, str(_site_packages))
            break

__version__ = "0.2.0"

from autosetter.cli import (
    AutoSetterError,
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
    generate_all_artifacts,
)

from autosetter.llm import OllamaCallError, OllamaClient
from autosetter.packager import Packager, PackagerError
from autosetter.pipeline import (
    PipelineError,
    TestCase,
    TestPipeline,
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
    "Config",
    "OllamaClient",
    "OllamaCallError",
    "load_image_as_base64",
    "ImageParsingError",
    "generate_problem_json",
    "save_problem_json",
    "JSONExtractionError",
    "generate_all_artifacts",
    "ArtifactSpec",
    "CodeGenerationError",
    "SandboxLocalClient",
    "SandboxError",
    "ExecutionResult",
    "ensure_testlib",
    "refresh_vendored_testlib",
    "TestPipeline",
    "TestReport",
    "TestCase",
    "PipelineError",
    "Packager",
    "PackagerError",
    "PolygonClient",
    "PolygonAPIError",
    "upload_problem_package",
]
