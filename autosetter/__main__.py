from __future__ import annotations

import os
import sys
from pathlib import Path

from autosetter.config import ensure_venv
ensure_venv(["-m", "autosetter"] + sys.argv[1:])

from autosetter.cli import main

if __name__ == "__main__":
    sys.exit(main())
