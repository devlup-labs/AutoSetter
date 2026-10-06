import re
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient

from .embeddings.embedder import ProblemEmbedder
from .processing.schema import Problem


def build_codeforces_url(source_id: str) -> Optional[str]:
    """
    Deterministically builds a Codeforces URL from an ID like '1234A', '1234/A' or '1352C1'.
    Contest IDs of 6+ digits are gym contests.
    """
    match = re.fullmatch(r"(\d+)/?([A-Za-z]\d*)", source_id or "")
    if not match:
        return None
    contest_id, index = match.groups()
    if len(contest_id) >= 6:
        return f"https://codeforces.com/gym/{contest_id}/problem/{index}"
    return f"https://codeforces.com/problemset/problem/{contest_id}/{index}"


def resolve_url(payload: Dict[str, Any]) -> Optional[str]:
    """Prefer the URL stored in Qdrant; fall back to building it from the ID."""
    if payload.get("url"):
        return payload["url"]
    if payload.get("source") == "codeforces":
        return build_codeforces_url(payload.get("source_id", ""))
    return None


class ProblemSearcher:
    """
    Semantic (KNN) search over the problems stored in Qdrant.

    The collection uses cosine distance and the embeddings are normalized, so
    each match's `score` is its cosine similarity to the query (1.0 = identical).
    """

    def __init__(self, url: str, collection_name: str, model_name: str, api_key: Optional[str] = None):
        # Connect and check the collection before loading the embedding model,
        # so an unreachable database fails fast instead of after a model load.
        self.client = QdrantClient(url=url, api_key=api_key)
        self.collection_name = collection_name
        self.client.get_collection(collection_name)
        self.embedder = ProblemEmbedder(model_name=model_name)

    def search_text(self, query: str, k: int = 1) -> List[Dict[str, Any]]:
        """Search with free text (e.g. a pasted problem statement)."""
        vector = self.embedder.model.encode([query], normalize_embeddings=True).tolist()[0]
        return self._search(vector, k)

    def search_problem(self, problem: Problem, k: int = 1) -> List[Dict[str, Any]]:
        """Search with a structured problem, embedded in the same format as the stored problems."""
        return self._search(self.embedder.embed_batch([problem])[0], k)

    def _search(self, vector: List[float], k: int) -> List[Dict[str, Any]]:
        points = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=k
        ).points

        return [
            {
                "score": round(p.score, 4),
                "title": p.payload.get("title"),
                "source": p.payload.get("source"),
                "source_id": p.payload.get("source_id"),
                "difficulty": p.payload.get("difficulty"),
                "tags": p.payload.get("tags") or [],
                "url": resolve_url(p.payload),
            }
            for p in points
        ]
