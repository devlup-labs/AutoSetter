import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from vector_database.pipeline import Pipeline

def main():
    print("Running embedding generation and upload to Qdrant...")
    pipeline = Pipeline()
    pipeline.embed_and_upload()

if __name__ == "__main__":
    main()
