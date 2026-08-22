import argparse
import sys
from pathlib import Path
from qdrant_client import QdrantClient

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from vector_database import config
from vector_database.embeddings.embedder import ProblemEmbedder

def main():
    parser = argparse.ArgumentParser(description="Test similarity search in Qdrant.")
    parser.add_argument("query", type=str, help="Problem description to search for")
    parser.add_argument("--k", type=int, default=5, help="Number of results to retrieve")
    args = parser.parse_args()
    
    print(f"Loading embedder ({config.EMBEDDING_MODEL})...")
    embedder = ProblemEmbedder(model_name=config.EMBEDDING_MODEL)
    
    print("Generating embedding for query...")
    # Wrap in list since embed_batch expects list of Problems, but here we just need raw text embedding
    # We can directly use the model to encode
    query_vector = embedder.model.encode([args.query], normalize_embeddings=True)[0].tolist()
    
    print(f"Connecting to Qdrant at {config.QDRANT_URL}...")
    client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY)
    
    print(f"Searching for top {args.k} matches...")
    results = client.search(
        collection_name=config.QDRANT_COLLECTION,
        query_vector=query_vector,
        limit=args.k
    )
    
    print("\n=== Top Matches ===")
    for i, res in enumerate(results, 1):
        payload = res.payload
        print(f"\n[{i}] Score: {res.score:.4f}")
        print(f"ID: {payload.get('problem_id')}")
        print(f"Title: {payload.get('title')}")
        print(f"Source: {payload.get('source')} ({payload.get('source_id')})")
        print(f"URL: {payload.get('url')}")
        print(f"Difficulty: {payload.get('difficulty')}")
        print(f"Tags: {', '.join(payload.get('tags', []))}")

if __name__ == "__main__":
    main()
