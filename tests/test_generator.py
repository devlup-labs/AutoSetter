"""
Unit tests for autosetter.generator (downstream code and markdown generation).
"""

from __future__ import annotations

from pathlib import Path
import pytest

import json

from autosetter.generator import (
    ARTIFACTS,
    generate_all_artifacts,
    strip_code_fence,
)
from tests.conftest import StubOllamaClient

TWO_SUM_SPEC = {
    "multi_test": None,
    "variables": [
        {"name": "n", "type": "int", "min": 2, "max": 1000},
        {"name": "target", "type": "int", "min": -2000000000, "max": 2000000000},
        {"name": "nums", "type": "array", "length": "n", "min": -1000000000, "max": 1000000000},
    ],
    "constraints": [],
    "global_constraints": [],
    "layout": [["n", "target"], ["nums"]],
}


class SequencedStub(StubOllamaClient):
    """Replies to test-spec prompts from a queue; everything else gets C++."""

    def __init__(self, spec_replies):
        super().__init__(default="```cpp\nint main() { return 0; }\n```")
        self.spec_replies = list(spec_replies)

    def chat_text(self, prompt, model=None, temperature=0.2):
        self.prompts.append(prompt)
        if "JSON \"test spec\"" in prompt and self.spec_replies:
            return self.spec_replies.pop(0)
        return self.default

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

    assert len(results) == len(ARTIFACTS)
    for spec in ARTIFACTS:
        assert spec.name in results
        artifact_path = results[spec.name]
        assert artifact_path.exists()
        assert any(f"Generating {spec.name}" in m for m in messages)


def test_test_spec_is_retried_until_it_accepts_the_samples(tmp_path: Path):
    # The sample has n = 4, so a spec claiming n >= 5 must be sent back.
    wrong = json.loads(json.dumps(TWO_SUM_SPEC))
    wrong["variables"][0]["min"] = 5
    client = SequencedStub([
        "Here is the spec:\n```json\n" + json.dumps(wrong) + "\n```",
        json.dumps(TWO_SUM_SPEC),
    ])

    results = generate_all_artifacts(
        problem_data=SAMPLE_DATA,
        generated_dir=tmp_path,
        client=client,
        targets=["generator"],
    )

    assert results["generator"].name == "test_spec.json"
    assert json.loads(results["generator"].read_text()) == TWO_SUM_SPEC
    assert len(client.prompts) == 2
    assert "rejects the problem's official sample" in client.prompts[1]
    assert "n = 4 is outside [5, 1000]" in client.prompts[1]
