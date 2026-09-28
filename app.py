#!/usr/bin/env python3
"""
app.py
======
AutoSetter entry point.

Usage:
    python app.py path/to/problem.png
    python app.py path/to/problem.pdf --vision-model qwen2.5vl:3b --text-model qwen2.5-coder:7b

Or via module execution:
    python -m autosetter path/to/problem.png

Programmatic usage:
    from autosetter import generate_from_image
    result = generate_from_image("path/to/problem.png")
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Auto-discover project virtualenv if ollama is not available in current environment
_venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python3"
if _venv_python.exists() and sys.executable != str(_venv_python):
    try:
        import ollama  # noqa: F401
    except ImportError:
        os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

from autosetter.cli import (
    AutoSetterError,
    AutoSetupError,
    PipelineResult,
    generate_from_image,
    main,
)

__all__ = [
    "main",
    "generate_from_image",
    "PipelineResult",
    "AutoSetterError",
    "AutoSetupError",
]

if __name__ == "__main__":
    sys.exit(main())
