"""Extraction: parsing what a model actually returns, and refusing what it can't.

These need no model and no compiler — the Ollama call is the one seam, and the
stub replaces it.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import StubOllamaClient
from utils.json_generator import (
    JSONGenerationError,
    _parse_model_json,
    _validate_schema,
    generate_problem_json,
    save_problem_json,
)

PROMPTS_DIR = "prompts"

COMPLETE = {
    "title": "Sum",
    "story": "Add them.",
    "input_format": "One line with n.",
    "output_format": "Print 2n.",
    "constraints": "1 <= n <= 100",
    "samples": [{"input": "5\n", "output": "10\n", "explanation": ""}],
    "time_limit": "1s",
    "memory_limit": "256MB",
    "notes": "",
}


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(COMPLETE),
        "```json\n" + json.dumps(COMPLETE) + "\n```",
        "```\n" + json.dumps(COMPLETE) + "\n```",
        "Sure! Here is the JSON:\n" + json.dumps(COMPLETE) + "\nHope that helps.",
    ],
    ids=["bare", "json-fence", "plain-fence", "chatty-preamble"],
)
def test_model_replies_are_parsed_however_they_are_wrapped(raw):
    assert _parse_model_json(raw) == COMPLETE


def test_unparseable_reply_reports_what_the_model_said():
    with pytest.raises(JSONGenerationError) as excinfo:
        _parse_model_json("I'm afraid I can't read that image.")

    assert "can't read that image" in str(excinfo.value)


def test_empty_reply_says_so_rather_than_blaming_the_json():
    with pytest.raises(JSONGenerationError) as excinfo:
        _parse_model_json("")

    assert "empty response" in str(excinfo.value)


def test_missing_keys_are_named():
    incomplete = {k: v for k, v in COMPLETE.items() if k not in ("constraints", "notes")}

    with pytest.raises(JSONGenerationError) as excinfo:
        _validate_schema(incomplete)

    assert "constraints" in str(excinfo.value)
    assert "notes" in str(excinfo.value)


def test_samples_must_be_a_list_of_objects():
    with pytest.raises(JSONGenerationError):
        _validate_schema({**COMPLETE, "samples": "5 -> 10"})

    with pytest.raises(JSONGenerationError):
        _validate_schema({**COMPLETE, "samples": [{"input": "5"}]})


def test_generate_problem_json_end_to_end(tmp_path, monkeypatch):
    image = tmp_path / "statement.png"
    monkeypatch.setattr(
        "utils.json_generator.load_image_as_base64", lambda path: ["ZmFrZQ=="]
    )
    client = StubOllamaClient(default="```json\n" + json.dumps(COMPLETE) + "\n```")

    result = generate_problem_json(
        image_path=image, prompts_dir=PROMPTS_DIR, client=client
    )

    assert result == COMPLETE
    assert client.prompts, "the extraction prompt should have been sent"


def test_saved_json_round_trips(tmp_path):
    path = save_problem_json(COMPLETE, tmp_path / "nested" / "problem.json")
    assert json.loads(path.read_text()) == COMPLETE
