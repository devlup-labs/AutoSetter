"""
Unit tests for the similar-problem lookup (autosetter.similarity) and its
wiring into the CLI pipeline. Qdrant and the embedding model are stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from autosetter.cli import build_arg_parser, generate_from_image
from autosetter.similarity import SimilaritySearchError, save_similar_problems
from tests.test_cli import SAMPLE_PROBLEM

MATCHES = [
    {
        "score": 0.93,
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
    monkeypatch.setattr("autosetter.cli.OllamaClient", lambda **_: client)
    return generate_from_image(
        image_path=img_path, out_dir=tmp_path / "out", skip_validation=True, **kwargs
    )


def test_arg_parser_similarity_flags():
    args = build_arg_parser().parse_args(["p.png", "--no-similarity", "--similar-k", "3"])
    assert args.no_similarity is True
    assert args.similar_k == 3


def test_save_similar_problems(tmp_path: Path):
    path = save_similar_problems(MATCHES, tmp_path / "similar_problems.json")
    assert json.loads(path.read_text()) == MATCHES


def test_pipeline_saves_similar_problems(tmp_path: Path, monkeypatch, stub_client):
    seen = {}

    def fake_find(problem_data, k):
        seen["title"], seen["k"] = problem_data["title"], k
        return MATCHES

    monkeypatch.setattr("autosetter.cli.find_similar_problems", fake_find)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client, similar_k=3)

    assert seen == {"title": SAMPLE_PROBLEM["title"], "k": 3}
    assert result.similar_problems == MATCHES
    saved = json.loads((tmp_path / "out" / "similar_problems.json").read_text())
    assert saved[0]["url"] == "https://codeforces.com/problemset/problem/1/A"


def test_pipeline_continues_when_search_fails(tmp_path: Path, monkeypatch, stub_client):
    def failing_find(problem_data, k):
        raise SimilaritySearchError("Qdrant unreachable")

    monkeypatch.setattr("autosetter.cli.find_similar_problems", failing_find)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client)

    assert result.similar_problems == []
    assert (tmp_path / "out" / "package" / "manifest.json").exists()


def test_pipeline_skips_search_when_disabled(tmp_path: Path, monkeypatch, stub_client):
    def should_not_run(problem_data, k):
        raise AssertionError("search should be skipped")

    monkeypatch.setattr("autosetter.cli.find_similar_problems", should_not_run)
    result = _run_pipeline(tmp_path, monkeypatch, stub_client, similarity_check=False)
    assert result.similar_problems == []
