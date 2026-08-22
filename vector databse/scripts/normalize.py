# This script acts as an alias to collect.py since collect and normalize are pipelined
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
import collect

if __name__ == "__main__":
    print("Note: In this architecture, Collection and Normalization are executed in the same stream.")
    collect.main()
