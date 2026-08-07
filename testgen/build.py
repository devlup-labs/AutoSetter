"""Compile an emitted validator.

Both the command line tool and the self test need to turn a Problem into a
runnable binary, so the compile lives here rather than in either of them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from testgen.emit.checker import emit_checker
from testgen.emit.generator import emit_generator
from testgen.emit.validator import emit_validator
from testgen.ir.schema import Problem

TESTLIB_DIR = Path(__file__).parent
DEFAULT_BUILD_DIR = Path(__file__).parent.parent / "build"


class CompileError(RuntimeError):
    """Raised when the emitted C++ does not compile."""


def _compile(source_text: str, name: str, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)

    source = outdir / f"{name}.cpp"
    source.write_text(source_text)

    binary = outdir / name
    result = subprocess.run(
        ["g++", "-O2", "-o", str(binary), str(source), "-I", str(TESTLIB_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CompileError(f"{name} did not compile:\n{result.stderr}")
    return binary


def build_validator(
    problem: Problem, name: str, outdir: Path | None = None
) -> Path:
    """Emit, compile and return the path to the validator binary."""
    return _compile(
        emit_validator(problem), f"{name}_validator", outdir or DEFAULT_BUILD_DIR
    )


def build_generator(
    problem: Problem, name: str, outdir: Path | None = None
) -> Path:
    """Emit, compile and return the path to the generator binary."""
    return _compile(
        emit_generator(problem), f"{name}_generator", outdir or DEFAULT_BUILD_DIR
    )


def build_checker(
    problem: Problem, name: str, outdir: Path | None = None
) -> Path:
    """Emit, compile and return the path to the checker binary.

    Raises for problems that should use a stock checker, or that need one
    written by hand.
    """
    return _compile(
        emit_checker(problem), f"{name}_checker", outdir or DEFAULT_BUILD_DIR
    )


def judge(
    binary: Path, test_input: str, output: str, answer: str, outdir: Path
) -> tuple[str, str]:
    """Run the checker on one submission, returning its verdict and message.

    testlib checkers take three file paths rather than reading stdin, so the
    three streams are written out first.
    """
    paths = {}
    for label, text in (("in", test_input), ("out", output), ("ans", answer)):
        path = outdir / f"judge.{label}"
        path.write_text(text)
        paths[label] = str(path)

    result = subprocess.run(
        [str(binary), paths["in"], paths["out"], paths["ans"]],
        capture_output=True,
        text=True,
    )
    message = (result.stderr or result.stdout).strip().splitlines()
    first = message[0] if message else ""

    # testlib reports the verdict through its exit code: 0 accepted,
    # 1 wrong answer, 2 presentation error, 3 a fault in the problem itself.
    verdict = {0: "ok", 1: "wa", 2: "pe", 3: "fail"}.get(
        result.returncode, f"exit{result.returncode}"
    )
    return verdict, first


def generate(binary: Path, args: list[str]) -> str:
    """Run the generator with these arguments and return the test it wrote."""
    result = subprocess.run(
        [str(binary), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"generator failed for args {args}: {result.stderr.strip()}"
        )
    return result.stdout


def accepts(binary: Path, text: str) -> tuple[bool, str]:
    """Run the validator on some input, returning whether it passed and why not."""
    result = subprocess.run(
        [str(binary)], input=text, capture_output=True, text=True
    )
    if result.returncode == 0:
        return True, ""
    message = (result.stderr or result.stdout).strip().splitlines()
    return False, message[0] if message else "rejected"
