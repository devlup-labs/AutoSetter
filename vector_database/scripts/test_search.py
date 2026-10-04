import argparse
import sys
from pathlib import Path

# Absolute path adjustments for local custom modules
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from vector_database import config
from vector_database.search import ProblemSearcher


def main():
    parser = argparse.ArgumentParser(description="Test similarity search in Qdrant.")
    parser.add_argument("query", type=str, nargs="?", help="Problem description to search for")
    parser.add_argument("--file", type=str, help="Read the query (e.g. a full problem statement) from a file")
    parser.add_argument("--k", type=int, default=5, help="Number of results to retrieve")
    args = parser.parse_args()

    if args.file:
        query = Path(args.file).read_text(encoding="utf-8")
    elif args.query:
        query = args.query
    else:
        parser.error("provide a query string or --file")

    print(f"Loading embedder ({config.EMBEDDING_MODEL}) and connecting to Qdrant at {config.QDRANT_URL}...")
    searcher = ProblemSearcher(
        url=config.QDRANT_URL,
        collection_name=config.QDRANT_COLLECTION,
        model_name=config.EMBEDDING_MODEL,
        api_key=config.QDRANT_API_KEY,
    )

    print(f"Searching for top {args.k} matches...")
    results = searcher.search_text(query, k=args.k)

    print("\n=== Matches ===")
    for i, res in enumerate(results, 1):
        print(f"""
[{i}] Score: {res['score']:.4f}
Title: {res['title']}
Source: {res['source']} ({res['source_id']})
Difficulty: {res['difficulty']}
Tags: {', '.join(res['tags'])}
URL: {res['url'] or 'N/A'}""")


if __name__ == "__main__":
    main()
