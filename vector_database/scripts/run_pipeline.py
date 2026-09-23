import argparse
import sys
from pathlib import Path

# Add the parent directory to Python path to import vector_database
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from vector_database.pipeline import Pipeline
from vector_database import config

def main():
    parser = argparse.ArgumentParser(description="Run the full Vector Database Ingestion Pipeline.")
    parser.add_argument("--source", type=str, choices=["codeforces", "leetcode", "all"], default="all",
                        help="Data source to collect from")
    parser.add_argument("--limit", type=int, default=None,
                        help="Total limit for problems (overrides specific limits)")
    parser.add_argument("--cf-limit", type=int, default=config.CODEFORCES_TARGET,
                        help="Codeforces limit")
    parser.add_argument("--leetcode-limit", type=int, default=config.LEETCODE_TARGET,
                        help="LeetCode limit")
                        
    args = parser.parse_args()
    
    sources = ["codeforces", "leetcode"] if args.source == "all" else [args.source]
    
    limits = {
        "codeforces": args.limit if args.limit else args.cf_limit,
        "leetcode": args.limit if args.limit else args.leetcode_limit,
    }
    
    pipeline = Pipeline()
    print("=== Phase 1: Collect & Normalize ===")
    pipeline.collect_and_normalize(sources=sources, limits=limits)
    
    print("\n=== Phase 2: Embed & Upload ===")
    pipeline.embed_and_upload()
    
    print("\nPipeline execution finished.")
    print(f"Stats saved to: {config.STATS_FILE}")

if __name__ == "__main__":
    main()
