import argparse
import os
import sys
from pathlib import Path
from qdrant_client import QdrantClient
from google import genai

# Absolute path adjustments for local custom modules
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from vector_database import config
from vector_database.embeddings.embedder import ProblemEmbedder


def resolve_links_with_llm(results_summary: str) -> str:
    """
    Sends the raw structural database matches to Gemini to reconstruct
    the correct Codeforces URL variants.
    """
    print("\nRouting search data to LLM for precise URL mapping...")
    
    # Initialize the standard Gemini client (automatically loads GEMINI_API_KEY from environment)
    client = genai.Client()
    
    # FIX: Restored full explicit URL patterns so the LLM knows exactly how to build paths
    prompt = f"""
    You are an expert competitive programming assistant. Below are raw vector search results from a database containing Codeforces problems. 
    Analyze the 'ID', 'Title', and 'Source' properties to output the direct, clickable live link for every single match.

    Strict Structural Formatting Rules:
    1. Standard problem IDs (e.g., 1234/A) must use the standard public problemset view path:
       https://codeforces.com
    2. Gym IDs (e.g., 987654/B or any ID where the contest part is 6 digits or more) must route exactly to:
       https://codeforces.com

    Print the cleanly formatted results exactly as provided in the raw data layout, but replace the text placeholder blocks with the actual verified live URLs.

    Raw Results Data:
    {results_summary}
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        # Ensure we always return a string fallback if response content is unexpectedly empty
        return response.text if response.text else results_summary
    except Exception as e:
        return f"\n[LLM Error]: Could not resolve links dynamically ({e})\n{results_summary}"


def main():
    parser = argparse.ArgumentParser(description="Test similarity search in Qdrant with LLM link formatting.")
    parser.add_argument("query", type=str, help="Problem description to search for")
    parser.add_argument("--k", type=int, default=5, help="Number of results to retrieve")
    args = parser.parse_args()
    
    print(f"Loading embedder ({config.EMBEDDING_MODEL})...")
    embedder = ProblemEmbedder(model_name=config.EMBEDDING_MODEL)
    
    print("Generating embedding for query...")
    query_vector = embedder.model.encode([args.query], normalize_embeddings=True).tolist()[0]
    
    print(f"Connecting to Qdrant at {config.QDRANT_URL}...")
    client = QdrantClient(url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY)
    
    print(f"Searching for top {args.k} matches...")
    results = client.query_points(
        collection_name=config.QDRANT_COLLECTION,
        query=query_vector,
        limit=args.k
    ).points
    
    # Construct an unformatted text summary block to pass to the LLM
    results_summary = ""
    for i, res in enumerate(results, 1):
        payload = res.payload
        prob_id = payload.get('problem_id', '').replace('cf_', '')
        tags = payload.get('tags')
        tags_str = ', '.join(tags) if isinstance(tags, list) else tags
        
        results_summary += f"""
[{i}] Score: {res.score:.4f}
ID: {prob_id}
Title: {payload.get('title')}
Source: {payload.get('source')} ({payload.get('source_id')})
Difficulty: {payload.get('difficulty')}
Tags: {tags_str}
URL: [Insert Live Codeforces URL here]
"""

    # Pass the text to the LLM to get the final output containing functional links
    final_output = resolve_links_with_llm(results_summary)
    
    print("\n=== Final Formatted Matches ===")
    print(final_output)


if __name__ == "__main__":
    main()
