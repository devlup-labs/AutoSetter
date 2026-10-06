"""
autosetter.similarity
=====================
Existing-problem lookup against the vector database built by `vector_database/`.

After extraction, the problem.json is embedded the same way the stored problems
were and searched in Qdrant with KNN (k=1 by default). The collection uses
cosine distance over normalized embeddings, so a match's score is its cosine
similarity. A nearest match scoring above the threshold (0.80 by default) is
treated as the same problem: the pipeline prints its link instead of
generating a new package.

This stage is optional: the heavy dependencies (sentence-transformers, torch,
qdrant-client) live in `vector_database/requirements.txt`, and a missing
dependency or unreachable Qdrant only skips the check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from autosetter.config import (
    EMBEDDING_MODEL,
    PROJECT_ROOT,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_URL,
    SIMILARITY_THRESHOLD,
    SIMILARITY_TOP_K,
)


class SimilaritySearchError(Exception):
    """Raised when the existing-problem lookup cannot run."""


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value or "").strip()


class ProblemDatabase:
    """
    Connection to the Qdrant problem database.

    Connecting checks that the collection exists and loads the embedding
    model, which is slow, so one instance is shared per (url, collection,
    model) through `get_database()`.
    """

    def __init__(
        self,
        url: str = QDRANT_URL,
        collection_name: str = QDRANT_COLLECTION,
        model_name: str = EMBEDDING_MODEL,
        api_key: Optional[str] = QDRANT_API_KEY,
    ) -> None:
        self.url = url
        self.collection_name = collection_name
        self.model_name = model_name
        self.api_key = api_key
        self._searcher = None

    def connect(self) -> "ProblemDatabase":
        """Open the connection; safe to call more than once."""
        if self._searcher is not None:
            return self
        # vector_database/ sits at the repository root, next to autosetter/
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.append(str(PROJECT_ROOT))
        try:
            from vector_database.search import ProblemSearcher
        except ImportError as exc:
            raise SimilaritySearchError(
                f"vector database dependencies are not installed ({exc}); "
                "run `pip install -r vector_database/requirements.txt`"
            ) from exc
        try:
            self._searcher = ProblemSearcher(
                url=self.url,
                collection_name=self.collection_name,
                model_name=self.model_name,
                api_key=self.api_key,
            )
        except Exception as exc:
            raise SimilaritySearchError(
                f"cannot open collection '{self.collection_name}' on Qdrant at {self.url}: {exc}"
            ) from exc
        return self

    def search(self, problem_data: Dict[str, Any], k: int = SIMILARITY_TOP_K) -> List[Dict[str, Any]]:
        """
        Return the k stored problems nearest to `problem_data`, best first.

        Each match is a dict with score (cosine similarity), title, source,
        source_id, difficulty, tags and url.
        """
        self.connect()
        from vector_database.processing.schema import Problem

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
            return self._searcher.search_problem(problem, k=k)
        except Exception as exc:
            raise SimilaritySearchError(f"search against Qdrant at {self.url} failed: {exc}") from exc


_databases: Dict[tuple, ProblemDatabase] = {}


def get_database(
    url: str = QDRANT_URL,
    collection_name: str = QDRANT_COLLECTION,
    model_name: str = EMBEDDING_MODEL,
    api_key: Optional[str] = QDRANT_API_KEY,
) -> ProblemDatabase:
    """Return the shared, connected database for these settings."""
    key = (url, collection_name, model_name, api_key)
    if key not in _databases:
        _databases[key] = ProblemDatabase(url, collection_name, model_name, api_key).connect()
    return _databases[key]


def find_similar_problems(
    problem_data: Dict[str, Any],
    k: int = SIMILARITY_TOP_K,
) -> List[Dict[str, Any]]:
    """Return the top-k stored problems most similar to `problem_data`."""
    return get_database().search(problem_data, k=k)


def find_existing_problem(
    matches: List[Dict[str, Any]],
    threshold: float = SIMILARITY_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """
    Return the nearest match if its cosine similarity is above `threshold`.

    Only the best match is considered; a score equal to the threshold does
    not count as an existing problem.
    """
    if not matches:
        return None
    best = max(matches, key=lambda m: m.get("score") or 0.0)
    return best if (best.get("score") or 0.0) > threshold else None


def save_similar_problems(matches: List[Dict[str, Any]], output_path: str | Path) -> Path:
    """Write the matches to a JSON file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(matches, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
