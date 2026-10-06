"""
AutoSetter
==========
AI-powered automated competitive programming problem packaging engine.
"""

from __future__ import annotations

__version__ = "0.3.0"

from autosetter.cli import main
from autosetter.runner import (
    AutoSetterError,
    AutoSetupError,
    PipelineResult,
    generate_from_image,
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
    generate_all_artifacts,
)

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
    PublishResult,
    publish_package,
    upload_problem_package,
)
from autosetter.remote import SSHTunnel, SSHTunnelError
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
    "publish_package",
    "PublishResult",
    "SSHTunnel",
    "SSHTunnelError",
]
