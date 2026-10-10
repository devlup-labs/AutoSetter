"""
reingest_from_hf.py
-------------------
Full re-ingestion script for a fresh Qdrant cluster.

Steps:
  1. Download the `open-r1/codeforces` HuggingFace dataset (train + test splits)
  2. Convert it to the JSONL format expected by CodeforcesSource → dataset/raw/codeforces.jsonl
  3. Reset pipeline state/stats (since the cluster is brand-new)
  4. Run collect_and_normalize  (reads raw JSONL → normalizes → writes final/problems.jsonl)
  5. Run embed_and_upload        (embeds with BAAI/bge-small-en-v1.5 → uploads to Qdrant)

Usage (run from the AutoSetter root, i.e. the folder containing vector_database/):
    python vector_database/scripts/reingest_from_hf.py

Or from the vector_database/scripts/ folder:
    python reingest_from_hf.py
"""

import sys
import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup: allow running from any directory
# ---------------------------------------------------------------------------
THIS_DIR = Path(__file__).resolve().parent            # vector_database/scripts/
VDB_DIR  = THIS_DIR.parent                             # vector_database/
ROOT_DIR = VDB_DIR.parent                              # AutoSetter/

for p in [str(ROOT_DIR), str(VDB_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Load .env BEFORE importing config so env-vars are visible
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv(VDB_DIR / ".env")

from vector_database import config
from vector_database.pipeline import Pipeline

# ---------------------------------------------------------------------------
# Step 1 – Check / install `datasets`
# ---------------------------------------------------------------------------
try:
    from datasets import load_dataset
except ImportError:
    print("The `datasets` library is not installed. Installing now...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "datasets"])
    from datasets import load_dataset

# ---------------------------------------------------------------------------
# Step 2 – Download open-r1/codeforces and convert to raw JSONL
# ---------------------------------------------------------------------------
RAW_JSONL = config.RAW_DIR / "codeforces.jsonl"

def download_and_convert(limit: int = None):
    """
    Download the open-r1/codeforces HF dataset and write it as a JSONL file
    that CodeforcesSource._collect_from_dataset() can read.

    The HF dataset fields are mapped as follows:
        id            -> id  (used as source_id inside CodeforcesSource)
        title         -> title
        description   -> statement  (full problem body)
        input_format  -> input_format
        output_format -> output_format
        note          -> constraints  (closest match; notes/explanations)
        examples      -> examples   (list of {input, output} dicts)
        rating        -> rating
        tags          -> tags
        contest_id    -> used to build URL
        index         -> used to build URL
    """
    print("=" * 60)
    print("STEP 1: Downloading open-r1/codeforces from HuggingFace...")
    print("=" * 60)

    # The dataset has two configs: 'default' (all problems) and 'verifiable'.
    # We use 'default' which contains the most problems (train + test splits).
    ds = load_dataset("open-r1/codeforces", name="default")

    # Combine train and test splits
    from datasets import concatenate_datasets
    combined = concatenate_datasets([ds["train"], ds["test"]])

    total = len(combined)
    if limit:
        total = min(total, limit)
    print(f"Total records to convert: {total:,}")

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)

    written = 0
    with open(RAW_JSONL, "w", encoding="utf-8") as f:
        for i, row in enumerate(combined):
            if limit and written >= limit:
                break

            contest_id = str(row.get("contest_id") or "")
            index      = str(row.get("index") or "")
            url = (
                f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
                if contest_id else None
            )

            # examples: list of dicts with 'input' and 'output' keys
            raw_examples = row.get("examples") or []
            examples = []
            for ex in raw_examples:
                if isinstance(ex, dict):
                    examples.append({
                        "input":  str(ex.get("input", "")),
                        "output": str(ex.get("output", ""))
                    })

            record = {
                "id":            row.get("id") or f"{contest_id}{index}",
                "title":         row.get("title") or "",
                "statement":     row.get("description") or "",
                "input_format":  row.get("input_format") or None,
                "output_format": row.get("output_format") or None,
                "constraints":   row.get("note") or None,   # 'note' = extra notes/hints
                "examples":      examples,
                "rating":        row.get("rating") or None,
                "tags":          list(row.get("tags") or []),
                "url":           url,
                # Extra fields stored in metadata (not required by pipeline)
                "contest_name":  row.get("contest_name") or None,
                "time_limit":    row.get("time_limit") or None,
                "memory_limit":  row.get("memory_limit") or None,
            }

            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

            if written % 1000 == 0:
                print(f"  Converted {written:,} / {total:,} records...")

    print(f"\nRaw JSONL written: {RAW_JSONL}  ({written:,} records)")
    return written


# ---------------------------------------------------------------------------
# Step 3 – Reset pipeline state (fresh cluster = start from scratch)
# ---------------------------------------------------------------------------
def reset_state():
    print("\n" + "=" * 60)
    print("STEP 2: Resetting pipeline state for fresh cluster...")
    print("=" * 60)

    for f in [config.STATE_FILE, config.STATS_FILE]:
        if f.exists():
            f.unlink()
            print(f"  Deleted {f}")

    # Also clear any existing final/problems.jsonl so we re-normalize cleanly
    final_file = config.FINAL_DIR / "problems.jsonl"
    if final_file.exists():
        final_file.unlink()
        print(f"  Deleted {final_file}")

    print("  State reset complete.")


# ---------------------------------------------------------------------------
# Step 4 & 5 – Run the full pipeline
# ---------------------------------------------------------------------------
def run_pipeline(cf_limit: int):
    print("\n" + "=" * 60)
    print("STEP 3: Running collect + normalize (from raw JSONL)...")
    print("=" * 60)

    pipeline = Pipeline()
    pipeline.collect_and_normalize(
        sources=["codeforces"],
        limits={"codeforces": cf_limit}
    )

    print("\n" + "=" * 60)
    print("STEP 4: Embedding & uploading to new Qdrant cluster...")
    print("=" * 60)
    pipeline.embed_and_upload()

    print("\n" + "=" * 60)
    print("Re-ingestion complete!")
    print(f"  Qdrant URL:        {config.QDRANT_URL}")
    print(f"  Collection:        {config.QDRANT_COLLECTION}")
    print(f"  Stats saved to:    {config.STATS_FILE}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Re-ingest open-r1/codeforces HF dataset into a fresh Qdrant cluster."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of problems to download & ingest (default: all). "
             "Use --limit 100 for a quick smoke-test.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip HF download (use if codeforces.jsonl already exists in dataset/raw/).",
    )
    args = parser.parse_args()

    cf_limit = args.limit or config.CODEFORCES_TARGET

    if not args.skip_download:
        download_and_convert(limit=args.limit)   # download all if no limit
    else:
        if not RAW_JSONL.exists():
            print(f"ERROR: --skip-download was set but {RAW_JSONL} does not exist.")
            sys.exit(1)
        print(f"Skipping download; using existing {RAW_JSONL}")

    reset_state()
    run_pipeline(cf_limit=cf_limit)
