"""Compile the emitted validators and check they accept and reject correctly.

An emitter that produces plausible looking C++ is not worth much on its own.
What matters is whether the validator it produces actually rejects a bad input,
so every case here is compiled with g++ and run against real files.

Each problem lists inputs that must be accepted and inputs that must be
rejected. The rejected ones are deliberate mutations of a valid file: a value
pushed past its bound, a token removed, whitespace made wrong, an order broken.
If a mutation is accepted, some constraint never made it out of the IR.

The cases at the bottom are not real problems. They exist to reach the parts of
the emitter that none of the reference problems happen to use, so that those
paths are still compiled and run rather than only being generated.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from testgen.build import (
    accepts,
    build_checker,
    build_generator,
    build_validator,
    generate,
    judge,
)
from testgen.emit.checker import CheckerNeedsHuman, emit_checker
from testgen.ir.problems import load
from testgen.ir.schema import Problem
from testgen.plan import plan_tests

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
    "next_round": {
        "accept": [
            "8 5\n10 9 8 7 7 7 5 5\n",
            "4 2\n0 0 0 0\n",
            "1 1\n100\n",
        ],
        "reject": [
            ("3 5\n1 2 3\n", "k above n, which is its upper bound"),
            ("3 2\n1 2 3\n", "an increasing sequence where non-increasing is required"),
            ("3 2\n3 2 1 0\n", "more values than n promises"),
            ("3 2\n3 2 101\n", "an element above its upper bound"),
            ("51 2\n" + " ".join(["1"] * 51) + "\n", "n above its upper bound"),
        ],
    },
    "three_sum": {
        "accept": [
            "8 20\n2 7 4 0 9 5 1 3\n",
            "3 0\n-100000 0 100000\n",
            "3 -300000\n-100000 -100000 -100000\n",
        ],
        "reject": [
            ("2 20\n2 7\n", "n below 3, so no triplet could exist"),
            ("8 20\n2 7 4 0 9 5 1\n", "fewer values than n promises"),
            ("8 20\n2 7 4 0 9 5 1 999999\n", "an element above its upper bound"),
            ("8 400000\n2 7 4 0 9 5 1 3\n", "a target above its upper bound"),
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

# Emitter paths that no reference problem reaches.
SYNTHETIC: dict[str, dict] = {
    "distinct_values": {
        "ir": {
            "name": "Distinct values",
            "multitest": False,
            "body": {
                "variables": [
                    {"kind": "int", "name": "n", "domain": {"lo": 1, "hi": 100}},
                    {
                        "kind": "array",
                        "name": "a",
                        "length": "n",
                        "elem": {"lo": 1, "hi": 100},
                        "distinct": True,
                    },
                ],
                "lines": [{"tokens": ["n"]}, {"tokens": ["a"]}],
            },
            "output": {"tokens_per_test": 1},
        },
        "accept": ["3\n1 2 3\n", "1\n7\n", "3\n9 4 1\n"],
        "reject": [
            ("3\n1 2 1\n", "a repeated value where the values must be distinct"),
            ("2\n5 5\n", "both values the same"),
        ],
    },
    "binary_string": {
        "ir": {
            "name": "Binary string",
            "multitest": False,
            "body": {
                "variables": [
                    {"kind": "int", "name": "n", "domain": {"lo": 1, "hi": 100}},
                    {
                        "kind": "string",
                        "name": "s",
                        "length": "n",
                        "alphabet": "01",
                    },
                ],
                "lines": [{"tokens": ["n"]}, {"tokens": ["s"]}],
            },
            "output": {"tokens_per_test": 1},
        },
        "accept": ["3\n010\n", "1\n0\n", "5\n11111\n"],
        "reject": [
            ("3\n012\n", "a character outside the alphabet"),
            ("3\n01\n", "a string shorter than n"),
            ("3\n0101\n", "a string longer than n"),
        ],
    },
}


def _show(text: str, limit: int = 30) -> str:
    shown = text if len(text) <= limit else text[:limit] + "..."
    return repr(shown)


def _check(binary: Path, cases: dict) -> int:
    failures = 0

    for text in cases["accept"]:
        ok, reason = accepts(binary, text)
        if ok:
            print(f"  ok      accepted {_show(text)}")
        else:
            failures += 1
            print(f"  FAILED  rejected a valid input {_show(text)}: {reason}")

    for text, description in cases["reject"]:
        ok, _ = accepts(binary, text)
        if ok:
            failures += 1
            print(f"  FAILED  accepted {description}")
        else:
            print(f"  ok      rejected {description}")

    return failures


def _check_generator(name: str, workdir: Path) -> int:
    """Every test the generator produces must satisfy the validator.

    This is the check worth having. The generator and the validator are built
    from the same constraints but in opposite directions, one finding an input
    and the other judging one, so if either has misread the IR they disagree
    here. Neither could catch that on its own.
    """
    problem = load(name)
    generator = build_generator(problem, name, workdir)
    validator = build_validator(problem, name, workdir)
    failures = 0

    for test in plan_tests(problem):
        produced = generate(generator, test.args)
        ok, reason = accepts(validator, produced)
        if ok:
            print(f"  ok      {test.name:<18} passes its own validator")
        else:
            failures += 1
            print(f"  FAILED  {test.name:<18} rejected by its own validator: {reason}")

    return failures


# Checker behaviour, as (contestant output, jury answer, expected verdict, why).
CHECKER_IR = {
    "name": "Yes or no in any case",
    "multitest": False,
    "body": {
        "variables": [{"kind": "int", "name": "w", "domain": {"lo": 1, "hi": 100}}],
        "lines": [{"tokens": ["w"]}],
    },
    "output": {
        "unique_answer": True,
        "case_insensitive": True,
        "tokens_per_test": 1,
    },
}

CHECKER_CASES = [
    ("YES", "YES", "ok", "an identical answer"),
    ("yes", "YES", "ok", "all lowercase, which is the reason this checker exists"),
    ("yEs", "YES", "ok", "mixed case"),
    ("NO", "YES", "wa", "a wrong answer"),
    ("MAYBE", "YES", "wa", "a token that is not an answer at all"),
    ("", "YES", "pe", "an empty output"),
    ("YES extra", "YES", "pe", "an extra token after the answer"),
]


def _check_checker(workdir: Path) -> int:
    """The generated checker has to give the right verdict, not just compile."""
    problem = Problem.model_validate(CHECKER_IR)
    binary = build_checker(problem, "case_insensitive", workdir)
    failures = 0

    for output, answer, expected, description in CHECKER_CASES:
        verdict, _ = judge(binary, "8\n", output + "\n", answer + "\n", workdir)
        if verdict == expected:
            print(f"  ok      {expected:<4} for {description}")
        else:
            failures += 1
            print(f"  FAILED  expected {expected} but got {verdict} for {description}")

    # A problem whose answer is not unique cannot have its checker derived, and
    # emitting one anyway would reject correct submissions.
    try:
        emit_checker(load("three_sum"))
    except CheckerNeedsHuman:
        print("  ok      refused to generate a checker where several answers are correct")
    else:
        failures += 1
        print("  FAILED  generated a checker for a problem with several correct answers")

    return failures


def run() -> int:
    failures = 0

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        for name, cases in CASES.items():
            binary = build_validator(load(name), name, workdir)
            print(f"\n{name}  (validator)")
            failures += _check(binary, cases)

        for name, cases in SYNTHETIC.items():
            problem = Problem.model_validate(cases["ir"])
            binary = build_validator(problem, name, workdir)
            print(f"\n{name}  (validator, synthetic)")
            failures += _check(binary, cases)

        for name in CASES:
            print(f"\n{name}  (generator against validator)")
            failures += _check_generator(name, workdir)

        print("\ncase_insensitive  (checker verdicts)")
        failures += _check_checker(workdir)

    print()
    if failures:
        print(f"{failures} check(s) failed")
    else:
        print("all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
