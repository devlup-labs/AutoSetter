"""
Unit tests for autosetter.generator (downstream code and markdown generation).
"""

from __future__ import annotations

from pathlib import Path
import pytest

from autosetter.generator import (
    ARTIFACTS,
    generate_all_artifacts,
    strip_code_fence,
)
from tests.conftest import StubOllamaClient

SAMPLE_DATA = {
    "title": "Two Sum",
    "story": "Find pair.",
    "input_format": "n and target",
    "output_format": "indices",
    "constraints": "2 <= n <= 1000",
    "samples": [{"input": "4 9\n2 7 11 15\n", "output": "0 1\n", "explanation": ""}],
    "time_limit": "2s",
    "memory_limit": "256MB",
    "notes": "",
}


def test_strip_code_fence_variants():
    cpp_fenced = "```cpp\n#include <iostream>\nint main() {}\n```"
    assert strip_code_fence(cpp_fenced) == "#include <iostream>\nint main() {}\n"

    plain_fenced = "```\n#include <iostream>\n```"
    assert strip_code_fence(plain_fenced) == "#include <iostream>\n"

    unfenced = "#include <iostream>\nint main() {}\n"
    assert strip_code_fence(unfenced) == "#include <iostream>\nint main() {}\n"


def test_generate_all_artifacts(tmp_path: Path):
    client = StubOllamaClient(default="```cpp\nint main() { return 0; }\n```")
    generated_dir = tmp_path / "generated"

    messages = []
    results = generate_all_artifacts(
        problem_data=SAMPLE_DATA,
        generated_dir=generated_dir,
        client=client,
        progress_callback=messages.append,
    )

    assert len(results) == 5
    for spec in ARTIFACTS:
        assert spec.name in results
        artifact_path = results[spec.name]
        assert artifact_path.exists()

    assert any("Generating statement" in m for m in messages)
    assert any("Generating validator" in m for m in messages)
    assert any("Generating generator" in m for m in messages)
    assert any("Generating solution" in m for m in messages)
    assert any("Generating checker" in m for m in messages)
