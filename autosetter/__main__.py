"""
Executable module entry point for running AutoSetter via `python -m autosetter`.
"""

from __future__ import annotations

import sys
from autosetter.cli import main

if __name__ == "__main__":
    sys.exit(main())
