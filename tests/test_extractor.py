"""
Unit tests for autosetter.extractor (VLM response parsing & schema validation).
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from autosetter.extractor import (
    JSONExtractionError,
    generate_problem_json,
    parse_model_json,
    save_problem_json,
    strip_markdown_code_fences,
    validate_schema,
)
from tests.conftest import StubOllamaClient

COMPLETE_PROBLEM = {
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
        json.dumps(COMPLETE_PROBLEM),
        "```json\n" + json.dumps(COMPLETE_PROBLEM) + "\n```",
        "```\n" + json.dumps(COMPLETE_PROBLEM) + "\n```",
        "Sure! Here is the JSON:\n" + json.dumps(COMPLETE_PROBLEM) + "\nHope that helps.",
    ],
    ids=["bare-json", "json-fence", "plain-fence", "conversational-preamble"],
)
def test_model_replies_parsed_across_wrapper_formats(raw: str):
    assert parse_model_json(raw) == COMPLETE_PROBLEM


def test_strip_markdown_fences():
    fenced = "```json\n{\"a\": 1}\n```"
    assert strip_markdown_code_fences(fenced) == "{\"a\": 1}"


def test_unparseable_reply_reports_model_text():
    with pytest.raises(JSONExtractionError) as excinfo:
        parse_model_json("I'm afraid I cannot read this image clearly.")
    assert "cannot read this image" in str(excinfo.value)


def test_empty_reply_reports_informative_error():
    with pytest.raises(JSONExtractionError) as excinfo:
        parse_model_json("")
    assert "empty response" in str(excinfo.value)


def test_missing_keys_in_schema_are_named():
    incomplete = {
        k: v for k, v in COMPLETE_PROBLEM.items() if k not in ("constraints", "notes")
    }
    with pytest.raises(JSONExtractionError) as excinfo:
        validate_schema(incomplete)
    assert "constraints" in str(excinfo.value)
    assert "notes" in str(excinfo.value)


def test_samples_must_be_list_of_objects():
    with pytest.raises(JSONExtractionError):
        validate_schema({**COMPLETE_PROBLEM, "samples": "5 -> 10"})

    with pytest.raises(JSONExtractionError):
        validate_schema({**COMPLETE_PROBLEM, "samples": [{"input": "5"}]})


def test_generate_problem_json_end_to_end(tmp_path: Path, monkeypatch):
    dummy_img = tmp_path / "statement.png"
    dummy_img.write_bytes(b"dummy")

    monkeypatch.setattr(
        "autosetter.extractor.load_image_as_base64", lambda path: ["ZmFrZQ=="]
    )
    client = StubOllamaClient(
        default="```json\n" + json.dumps(COMPLETE_PROBLEM) + "\n```"
    )

    result = generate_problem_json(
        image_path=dummy_img,
        client=client,
    )
    assert result == COMPLETE_PROBLEM
    assert client.prompts, "Extraction prompt was sent to client"


def test_save_problem_json_round_trips(tmp_path: Path):
    out_file = tmp_path / "nested" / "problem.json"
    saved_path = save_problem_json(COMPLETE_PROBLEM, out_file)
    assert saved_path.exists()
    assert json.loads(saved_path.read_text(encoding="utf-8")) == COMPLETE_PROBLEM
