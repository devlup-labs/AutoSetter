from __future__ import annotations

import os
import sys
from pathlib import Path

# Auto-discover project virtualenv if ollama is not available in current environment
_venv_python = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python3"
if _venv_python.exists() and sys.executable != str(_venv_python):
    try:
        import ollama  # noqa: F401
    except ImportError:
        os.execv(str(_venv_python), [str(_venv_python), "-m", "autosetter"] + sys.argv[1:])

from autosetter.cli import main

if __name__ == "__main__":
    sys.exit(main())
