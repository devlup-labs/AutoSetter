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

import sys
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
