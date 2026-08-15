"""
Shared fixtures and test configuration for AutoSetter test suite.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from autosetter.config import VENDORED_TESTLIB


class StubOllamaClient:
    """
    An in-memory stub for OllamaClient that records prompts and returns canned replies.
    """

    def __init__(
        self,
        replies: Optional[Dict[str, str]] = None,
        default: str = "",
    ) -> None:
        self.replies = replies or {}
        self.default = default
        self.prompts: List[str] = []

    def _reply_for(self, prompt: str) -> str:
        self.prompts.append(prompt)
        for marker, reply in self.replies.items():
            if marker in prompt:
                return reply
        return self.default

    def chat_with_images(
        self,
        prompt: str,
        images_base64: List[str],
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        return self._reply_for(prompt)

    def chat_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        return self._reply_for(prompt)


@pytest.fixture
def stub_client():
    """Factory fixture returning StubOllamaClient class."""
    return StubOllamaClient


@pytest.fixture
def has_gpp() -> bool:
    """Check if g++ is available on PATH."""
    return shutil.which("g++") is not None


@pytest.fixture
def cpp_workdir(tmp_path: Path) -> Path:
    """Temporary directory with testlib.h present for C++ compilation."""
    shutil.copy2(VENDORED_TESTLIB, tmp_path / "testlib.h")
    return tmp_path


needs_gpp = pytest.mark.skipif(
    shutil.which("g++") is None,
    reason="g++ compiler is not available on PATH",
)
