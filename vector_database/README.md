# Vector Database Pipeline

This module is a completely separate data-ingestion and vector-database pipeline designed to prepare a semantic search dataset of competitive programming problems for AutoSetter. 

It handles data collection, normalization, deduplication, generating embeddings via Sentence Transformers, and batch uploading to Qdrant.

## What This Pipeline Does

1. **Collects** problems from Codeforces and LeetCode (using official APIs or prepared JSONL datasets).
2. **Normalizes** the data (strips unnecessary HTML, standardizes whitespace, cleans tags) while preserving mathematical notations and code blocks.
3. **Deduplicates** problems based on stable IDs and normalized titles to avoid duplicate vectors.
4. **Converts** everything to a single Canonical JSON Schema.
5. **Generates Embeddings** using a configurable HuggingFace model (default: `BAAI/bge-small-en-v1.5`).
6. **Upserts** these embeddings and searchable metadata into a local Qdrant instance.

## Installation

1. Create and activate a Python virtual environment.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Qdrant Setup

The pipeline requires a running Qdrant instance. The easiest way to start one locally is via Docker:

```bash
docker run -p 6333:6333 -p 6334:6334 \
    -v $(pwd)/qdrant_storage:/qdrant/storage:z \
    qdrant/qdrant
```
This runs Qdrant on `http://localhost:6333` which is the default in `.env.example`.

## Configuration

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
You can edit `.env` to change limits, embedding batch sizes, or Qdrant connection settings. 

To change the embedding model, set `EMBEDDING_MODEL` in `.env`. The pipeline will automatically detect the new dimension and configure the Qdrant collection accordingly.

## Data Sources

The source collectors are built to support both official APIs and local datasets. 
Because Codeforces does not provide full problem statements via its API, and LeetCode has no open REST API, the pipeline prefers loading from raw JSONL files if they are placed in `vector_database/dataset/raw/`.

- For Codeforces: Place `codeforces.jsonl` in `dataset/raw/`
- For LeetCode: Place `leetcode.jsonl` in `dataset/raw/`

If `codeforces.jsonl` is not found, the pipeline will fall back to using the official Codeforces API (which will yield problems without statement bodies).

## Storage Locations

All generated data and logs are stored in the `vector_database/dataset/` directory:
- `dataset/raw/`: Place your initial dataset files here.
- `dataset/final/problems.jsonl`: The final, canonical, deduplicated, and normalized problems ready for embedding.
- `dataset/logs/`: Failed parsing/uploading logs are stored here as JSONL.
- `dataset/state.json`: Checkpoint state to allow pipeline resuming.
- `dataset/stats.json`: Data-quality checks and processing statistics.

## How to Run a Small Test

Before processing 10,000 problems, test the pipeline with a small batch:
```bash
python scripts/run_pipeline.py --limit 10
```

## How to Run the Full Pipeline

To process 10,000 problems as configured in `.env` (5000 CF, 5000 LeetCode):
```bash
python scripts/run_pipeline.py --source all
```

You can also run specific stages or sources independently:
```bash
python scripts/collect.py --source codeforces
python scripts/embed.py
```

## Resuming an Interrupted Run

The pipeline maintains a checkpoint in `dataset/state.json`. If execution stops unexpectedly, simply run `python scripts/run_pipeline.py` again. It will skip problems that have already been fully collected, embedded, and uploaded.

## Similarity Search Test

To verify that the vectors were correctly inserted into Qdrant, you can run the test search script:

```bash
python scripts/test_search.py "A problem about finding the shortest path in a graph" --k 5
```
This script will encode your query using the same embedding model and return the top 5 semantically similar problems stored in Qdrant.
