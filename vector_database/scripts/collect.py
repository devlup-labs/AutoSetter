import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from vector_database.pipeline import Pipeline
from vector_database import config

def main():
    parser = argparse.ArgumentParser(description="Run collection and normalization.")
    parser.add_argument("--source", type=str, choices=["codeforces", "leetcode", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None)
    
    args = parser.parse_args()
    sources = ["codeforces", "leetcode"] if args.source == "all" else [args.source]
    
    limits = {
        "codeforces": args.limit if args.limit else config.CODEFORCES_TARGET,
        "leetcode": args.limit if args.limit else config.LEETCODE_TARGET,
    }
    
    pipeline = Pipeline()
    pipeline.collect_and_normalize(sources=sources, limits=limits)

if __name__ == "__main__":
    main()
