"""
autosetter.similarity
=====================
Similar-problem lookup against the vector database built by `vector_database/`.

After extraction, the problem.json is embedded the same way the stored problems
were and searched in Qdrant, so the setter can see whether the problem (or a
close variant) already exists, with links to the matches.

This stage is optional: the heavy dependencies (sentence-transformers, torch,
qdrant-client) live in `vector_database/requirements.txt`, and a missing
dependency or unreachable Qdrant only skips the check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from autosetter.config import (
    EMBEDDING_MODEL,
    PROJECT_ROOT,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_URL,
    SIMILARITY_TOP_K,
)


class SimilaritySearchError(Exception):
    """Raised when the similar-problem lookup cannot run."""


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value or "").strip()


def find_similar_problems(
    problem_data: Dict[str, Any],
    k: int = SIMILARITY_TOP_K,
) -> List[Dict[str, Any]]:
    """
    Return the top-k stored problems most similar to `problem_data`.

    Each match is a dict with score, title, source, source_id, difficulty, tags and url.
    """
    # vector_database/ sits at the repository root, next to autosetter/
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.append(str(PROJECT_ROOT))
    try:
        from vector_database.processing.schema import Problem
        from vector_database.search import ProblemSearcher
    except ImportError as exc:
        raise SimilaritySearchError(
            f"vector database dependencies are not installed ({exc}); "
            "run `pip install -r vector_database/requirements.txt`"
        ) from exc

    problem = Problem(
        id="autosetter_query",
        source="autosetter",
        source_id="",
        title=_as_text(problem_data.get("title")),
        statement=_as_text(problem_data.get("story")) or None,
        input_format=_as_text(problem_data.get("input_format")) or None,
        output_format=_as_text(problem_data.get("output_format")) or None,
        constraints=_as_text(problem_data.get("constraints")) or None,
    )

    try:
        searcher = ProblemSearcher(
            url=QDRANT_URL,
            collection_name=QDRANT_COLLECTION,
            model_name=EMBEDDING_MODEL,
            api_key=QDRANT_API_KEY,
        )
        return searcher.search_problem(problem, k=k)
    except Exception as exc:
        raise SimilaritySearchError(f"search against Qdrant at {QDRANT_URL} failed: {exc}") from exc


def save_similar_problems(matches: List[Dict[str, Any]], output_path: str | Path) -> Path:
    """Write the matches to a JSON file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(matches, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
