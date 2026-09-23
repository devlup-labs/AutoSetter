# This script acts as an alias to embed.py since embeddings are streamed directly to Qdrant
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
import embed

if __name__ == "__main__":
    print("Note: In this architecture, Embedding and Uploading are executed in the same batch stream.")
    embed.main()
