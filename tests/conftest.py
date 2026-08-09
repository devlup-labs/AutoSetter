"""Shared fixtures.

The pipeline's one external dependency is the Ollama server, and it sits behind
a single class. Stubbing that class is enough to test everything downstream of
it without a model running, which is why the tests here need nothing installed
beyond pytest and a C++ compiler.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDORED_TESTLIB = PROJECT_ROOT / "third_party" / "testlib.h"


class StubOllamaClient:
    """An OllamaClient that returns canned replies instead of calling a model.

    Records every prompt it was given, so a test can assert what was asked as
    well as what came back.
    """

    def __init__(self, replies: dict[str, str] | None = None, default: str = "") -> None:
        self.replies = replies or {}
        self.default = default
        self.prompts: list[str] = []

    def _reply_for(self, prompt: str) -> str:
        self.prompts.append(prompt)
        for marker, reply in self.replies.items():
            if marker in prompt:
                return reply
        return self.default

    def chat_with_images(self, prompt, images_base64, model=None, temperature=0.2):
        return self._reply_for(prompt)

    def chat_text(self, prompt, model=None, temperature=0.2):
        return self._reply_for(prompt)


@pytest.fixture
def stub_client():
    return StubOllamaClient


@pytest.fixture
def has_gpp() -> bool:
    return shutil.which("g++") is not None


@pytest.fixture
def cpp_workdir(tmp_path: Path) -> Path:
    """A directory with testlib.h in it, ready to compile C++ against."""
    shutil.copy2(VENDORED_TESTLIB, tmp_path / "testlib.h")
    return tmp_path


needs_gpp = pytest.mark.skipif(
    shutil.which("g++") is None, reason="g++ is not installed"
)
