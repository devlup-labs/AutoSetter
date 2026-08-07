"""Compile an emitted validator.

Both the command line tool and the self test need to turn a Problem into a
runnable binary, so the compile lives here rather than in either of them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from testgen.emit.validator import emit_validator
from testgen.ir.schema import Problem

TESTLIB_DIR = Path(__file__).parent
DEFAULT_BUILD_DIR = Path(__file__).parent.parent / "build"


class CompileError(RuntimeError):
    """Raised when the emitted C++ does not compile."""


def build_validator(
    problem: Problem, name: str, outdir: Path | None = None
) -> Path:
    """Emit, compile and return the path to the validator binary."""
    outdir = outdir or DEFAULT_BUILD_DIR
    outdir.mkdir(parents=True, exist_ok=True)

    source = outdir / f"{name}_validator.cpp"
    source.write_text(emit_validator(problem))

    binary = outdir / f"{name}_validator"
    result = subprocess.run(
        ["g++", "-O2", "-o", str(binary), str(source), "-I", str(TESTLIB_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CompileError(f"{name} did not compile:\n{result.stderr}")
    return binary


def accepts(binary: Path, text: str) -> tuple[bool, str]:
    """Run the validator on some input, returning whether it passed and why not."""
    result = subprocess.run(
        [str(binary)], input=text, capture_output=True, text=True
    )
    if result.returncode == 0:
        return True, ""
    message = (result.stderr or result.stdout).strip().splitlines()
    return False, message[0] if message else "rejected"
