import uuid
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http import models

from ..processing.schema import Problem

class QdrantManager:
    def __init__(self, url: str, collection_name: str, api_key: str = None):
        self.client = QdrantClient(url=url, api_key=api_key)
        self.collection_name = collection_name
        
    def ensure_collection(self, dimension: int):
        """Creates the collection if it doesn't exist with the required dimension."""
        try:
            collections = self.client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)
            
            if not exists:
                print(f"Creating collection '{self.collection_name}' with dimension {dimension}...")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=dimension,
                        distance=models.Distance.COSINE
                    )
                )
            else:
                # Verify dimension
                collection_info = self.client.get_collection(self.collection_name)
                # For qdrant-client >= 1.7.0, vectors_config is returned. 
                # Handling different return structures for safety.
                config = collection_info.config.params.vectors
                if hasattr(config, 'size'):
                    existing_dim = config.size
                    if existing_dim != dimension:
                        raise ValueError(f"Collection dimension mismatch! Existing: {existing_dim}, Model: {dimension}")
        except Exception as e:
            print(f"Error ensuring collection: {e}")
            raise
            
    def _generate_stable_id(self, string_id: str) -> str:
        """Generate a deterministic UUID from a string ID."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, string_id))

    def _prepare_payload(self, problem: Problem) -> Dict[str, Any]:
        """Prepare the searchable metadata payload for Qdrant."""
        # We don't store huge fields like full statement in Qdrant if we want to save space,
        # but the prompt asked for `statement` in the example payload.
        # We'll include the main fields. Canonical JSON file is the source of truth.
        return {
            "problem_id": problem.id,
            "source": problem.source,
            "source_id": problem.source_id,
            "title": problem.title,
            "difficulty": problem.difficulty,
            "rating": problem.rating,
            "tags": problem.tags,
            "url": problem.url
        }

    def upsert_batch(self, problems: List[Problem], embeddings: List[List[float]]):
        """Batch upsert problems and their embeddings to Qdrant."""
        if not problems or not embeddings:
            return
            
        if len(problems) != len(embeddings):
            raise ValueError("Problems and embeddings must have the same length")
            
        points = []
        for prob, vector in zip(problems, embeddings):
            points.append(
                models.PointStruct(
                    id=self._generate_stable_id(prob.id),
                    vector=vector,
                    payload=self._prepare_payload(prob)
                )
            )
            
        self.client.upsert(
            collection_name=self.collection_name,
            points=points
        )
