"""Hand-written IR for the reference problems.

These are written by hand from the statements and act as the expected answers
when the extraction step is added later: whatever the model produces for a
statement should match the file here.

They are stored as JSON rather than Python so that a hand-written IR and an
extracted one are the same kind of object, and can be compared directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from testgen.ir.schema import Problem

PROBLEM_DIR = Path(__file__).parent


def load(name: str) -> Problem:
    """Load one problem by file stem, e.g. load("watermelon")."""
    path = PROBLEM_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"no IR file for {name!r} in {PROBLEM_DIR}")
    return Problem.model_validate_json(path.read_text())


def load_all() -> dict[str, Problem]:
    """Load every problem in this directory, keyed by file stem."""
    return {path.stem: load(path.stem) for path in sorted(PROBLEM_DIR.glob("*.json"))}
