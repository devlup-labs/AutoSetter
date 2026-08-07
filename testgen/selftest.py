"""Compile the emitted validators and check they accept and reject correctly.

An emitter that produces plausible looking C++ is not worth much on its own.
What matters is whether the validator it produces actually rejects a bad input,
so every case here is compiled with g++ and run against real files.

Each problem lists inputs that must be accepted and inputs that must be
rejected. The rejected ones are deliberate mutations of a valid file: a value
pushed past its bound, a token removed, whitespace made wrong. If a mutation is
accepted, some constraint never made it out of the IR.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from testgen.emit.validator import emit_validator
from testgen.ir.problems import load

TESTLIB_DIR = Path(__file__).parent

# (input text, what makes it wrong)
CASES: dict[str, dict[str, list]] = {
    "watermelon": {
        "accept": ["8\n", "1\n", "100\n", "2\n"],
        "reject": [
            ("101\n", "above the upper bound"),
            ("0\n", "below the lower bound"),
            ("", "empty file"),
            ("8 9\n", "a second number on the line"),
            ("8\n\n", "a blank line at the end"),
            ("8 \n", "a trailing space"),
            (" 8\n", "a leading space"),
            ("8\n9\n", "a second line"),
            ("abc\n", "not an integer"),
        ],
    },
    "theatre_square": {
        "accept": ["6 6 4\n", "1 1 1\n", "1000000000 1000000000 1\n"],
        "reject": [
            ("6 6\n", "only two of the three values"),
            ("0 6 4\n", "n below its lower bound"),
            ("1000000001 6 4\n", "n above its upper bound"),
            ("6 6 4 5\n", "a fourth value"),
            ("6  6 4\n", "two spaces instead of one"),
            ("6\t6 4\n", "a tab instead of a space"),
            ("6\n6\n4\n", "the values split across lines"),
        ],
    },
    "max_of_array": {
        "accept": [
            "1\n1\n5\n",
            "2\n3\n1 2 3\n2\n7 7\n",
            # the budget spent exactly, in one big test case
            "1\n200000\n" + " ".join(["1"] * 200000) + "\n",
        ],
        "reject": [
            ("1\n0\n\n", "n below its lower bound"),
            ("1\n3\n1 2\n", "fewer values than n promises"),
            ("1\n2\n1 2 3\n", "more values than n promises"),
            ("1\n1\n0\n", "an element below its lower bound"),
            ("2\n1\n5\n", "fewer test cases than t promises"),
            ("1\n1\n5\n1\n5\n", "more test cases than t promises"),
            # each test case is legal on its own; only the total is illegal
            (
                "2\n" + ("200000\n" + " ".join(["1"] * 200000) + "\n") * 2,
                "the sum of n over all test cases exceeds the limit",
            ),
        ],
    },
}


def compile_validator(problem_name: str, workdir: Path) -> Path:
    source = workdir / f"{problem_name}_validator.cpp"
    source.write_text(emit_validator(load(problem_name)))

    binary = workdir / f"{problem_name}_validator"
    result = subprocess.run(
        ["g++", "-O2", "-o", str(binary), str(source), "-I", str(TESTLIB_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{problem_name} did not compile:\n{result.stderr}")
    return binary


def accepts(binary: Path, text: str) -> bool:
    result = subprocess.run(
        [str(binary)], input=text, capture_output=True, text=True
    )
    return result.returncode == 0


def run() -> int:
    failures = 0

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        for problem_name, cases in CASES.items():
            binary = compile_validator(problem_name, workdir)
            print(f"\n{problem_name}  (compiled)")

            for text in cases["accept"]:
                if accepts(binary, text):
                    print(f"  ok      accepted {_show(text)}")
                else:
                    failures += 1
                    print(f"  FAILED  rejected a valid input {_show(text)}")

            for text, reason in cases["reject"]:
                if accepts(binary, text):
                    failures += 1
                    print(f"  FAILED  accepted {reason}")
                else:
                    print(f"  ok      rejected {reason}")

    print()
    if failures:
        print(f"{failures} check(s) failed")
    else:
        print("all checks passed")
    return 1 if failures else 0


def _show(text: str, limit: int = 30) -> str:
    shown = text if len(text) <= limit else text[:limit] + "..."
    return repr(shown)


if __name__ == "__main__":
    sys.exit(run())
