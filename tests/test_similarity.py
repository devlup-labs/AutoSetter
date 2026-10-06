"""
Unit tests for the similar-problem lookup (autosetter.similarity) and its
wiring into the CLI pipeline. Qdrant and the embedding model are stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from autosetter.cli import build_arg_parser, generate_from_image
from autosetter.similarity import (
    SimilaritySearchError,
    find_existing_problem,
    save_similar_problems,
)
from tests.test_cli import SAMPLE_PROBLEM

# Below the 0.80 threshold: reported, but generation goes ahead.
MATCHES = [
    {
        "score": 0.63,
        "title": "Theatre Square",
        "source": "codeforces",
        "source_id": "1A",
        "difficulty": "1000",
        "tags": ["math"],
        "url": "https://codeforces.com/problemset/problem/1/A",
    }
]


def _run_pipeline(tmp_path: Path, monkeypatch, stub_client, **kwargs):
    img_path = tmp_path / "problem.png"
    Image.new("RGB", (50, 50), color="white").save(img_path, format="PNG")
    problem_reply = "```json\n" + json.dumps(SAMPLE_PROBLEM) + "\n```"
    client = stub_client(
        replies={
            "OCR specialist": problem_reply,
            "competitive programming problem statement": problem_reply,
        },
        default="```cpp\nint main() { return 0; }\n```",
    )
    monkeypatch.setattr("autosetter.runner.OllamaClient", lambda **_: client)
    return generate_from_image(
        image_path=img_path, out_dir=tmp_path / "out", skip_validation=True, **kwargs
    )


DUPLICATE = [dict(MATCHES[0], score=0.93)]


def test_arg_parser_similarity_defaults():
    args = build_arg_parser().parse_args(["p.png"])
    assert args.similar_k == 1
    assert args.similarity_threshold == 0.80
    assert args.force is False


def test_arg_parser_similarity_flags():
    args = build_arg_parser().parse_args(
        ["p.png", "--no-similarity", "--similar-k", "3", "--similarity-threshold", "0.9", "--force"]
    )
    assert args.no_similarity is True
    assert args.similar_k == 3
    assert args.similarity_threshold == 0.9
    assert args.force is True


def test_find_existing_problem_threshold():
    assert find_existing_problem(DUPLICATE, threshold=0.80) == DUPLICATE[0]
    assert find_existing_problem(MATCHES, threshold=0.80) is None
    # Strictly greater than the threshold
    assert find_existing_problem([dict(MATCHES[0], score=0.80)], threshold=0.80) is None
    assert find_existing_problem([], threshold=0.80) is None


def test_save_similar_problems(tmp_path: Path):
    path = save_similar_problems(MATCHES, tmp_path / "similar_problems.json")
    assert json.loads(path.read_text()) == MATCHES


def test_pipeline_saves_similar_problems(tmp_path: Path, monkeypatch, stub_client):
    seen = {}

    def fake_find(problem_data, k):
        seen["title"], seen["k"] = problem_data["title"], k
        return MATCHES

    monkeypatch.setattr("autosetter.runner.find_similar_problems", fake_find)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client)

    assert seen == {"title": SAMPLE_PROBLEM["title"], "k": 1}
    assert result.similar_problems == MATCHES
    assert result.existing_problem is None
    saved = json.loads((tmp_path / "out" / "similar_problems.json").read_text())
    assert saved[0]["url"] == "https://codeforces.com/problemset/problem/1/A"


def test_pipeline_continues_when_search_fails(tmp_path: Path, monkeypatch, stub_client):
    def failing_find(problem_data, k):
        raise SimilaritySearchError("Qdrant unreachable")

    monkeypatch.setattr("autosetter.runner.find_similar_problems", failing_find)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client)

    assert result.similar_problems == []
    assert (tmp_path / "out" / "package" / "manifest.json").exists()


def test_pipeline_skips_search_when_disabled(tmp_path: Path, monkeypatch, stub_client):
    def should_not_run(problem_data, k):
        raise AssertionError("search should be skipped")

    monkeypatch.setattr("autosetter.runner.find_similar_problems", should_not_run)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client, similarity_check=False)
    assert result.similar_problems == []


def test_pipeline_stops_when_problem_exists(tmp_path: Path, monkeypatch, stub_client):
    monkeypatch.setattr("autosetter.runner.find_similar_problems", lambda problem_data, k: DUPLICATE)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client)

    assert result.existing_problem["url"] == "https://codeforces.com/problemset/problem/1/A"
    assert result.ready_for_release is False
    assert not (tmp_path / "out" / "package").exists()
    assert not (tmp_path / "out" / "generated").exists()


def test_pipeline_force_generates_despite_existing_problem(tmp_path: Path, monkeypatch, stub_client):
    monkeypatch.setattr("autosetter.runner.find_similar_problems", lambda problem_data, k: DUPLICATE)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client, force=True)

    assert result.existing_problem is None
    assert (tmp_path / "out" / "package" / "manifest.json").exists()


def test_custom_threshold_controls_the_gate(tmp_path: Path, monkeypatch, stub_client):
    monkeypatch.setattr("autosetter.runner.find_similar_problems", lambda problem_data, k: DUPLICATE)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client, similarity_threshold=0.95)

    assert result.existing_problem is None
    assert (tmp_path / "out" / "package" / "manifest.json").exists()
