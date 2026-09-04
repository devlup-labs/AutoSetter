import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Base paths
BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = Path(os.getenv("DATASET_DIR", BASE_DIR / "dataset"))

RAW_DIR = DATASET_DIR / "raw"
NORMALIZED_DIR = DATASET_DIR / "normalized"
FINAL_DIR = DATASET_DIR / "final"
EMBEDDINGS_DIR = DATASET_DIR / "embeddings"
LOGS_DIR = DATASET_DIR / "logs"

# Ensure directories exist
for d in [RAW_DIR, NORMALIZED_DIR, FINAL_DIR, EMBEDDINGS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Data targets
TOTAL_TARGET = int(os.getenv("TOTAL_TARGET", "10000"))
CODEFORCES_TARGET = int(os.getenv("CODEFORCES_TARGET", "5000"))
LEETCODE_TARGET = int(os.getenv("LEETCODE_TARGET", "5000"))

# Embeddings Config
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))

# Qdrant Config
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "competitive_programming_problems")
QDRANT_BATCH_SIZE = int(os.getenv("QDRANT_BATCH_SIZE", "64"))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)

STATE_FILE = DATASET_DIR / "state.json"
STATS_FILE = DATASET_DIR / "stats.json"
