"""Report whether an extracted statement carries enough to build the tools.

The extraction step produces a description of a problem in prose: a title, a
story, an input format written for a human to read. That is the right output for
showing a statement to someone, but it is not enough to build a validator, a
generator or a checker, because none of those can act on a sentence.

This report says which pieces are missing and what each one is needed for, so a
gap is visible at extraction time rather than turning up later as a validator
that quietly checks nothing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Each entry is a field of the extracted statement, whether anything downstream
# can act on it, and what it is used for.
REQUIRED = [
    ("constraints", "the bounds on every variable", "validator, generator"),
    ("input_format", "the order and layout of the values", "validator, checker"),
    ("output_format", "what a correct answer looks like", "checker"),
    ("samples", "inputs a correct validator must accept", "validator"),
]

# A constraints field that carries usable bounds says something like
# "1 <= n <= 2*10^5". Prose without any comparison in it does not.
#
# The lookbehind keeps an arrow out of it. A LeetCode statement often gives a
# function signature where the input format should be -- `def threeSum(nums:
# List[int]) -> bool` -- and the `>` of `->` is not a comparison. Counting it
# as one makes a statement with no bounds at all look like a statement that
# has them.
BOUND = re.compile(r"(?<!-)[<>]=?|\\le|\\ge|≤|≥")

# An input format that is one worked example rather than a description of the
# shape: "Input = [2, 7, 4, 0, 9, 5, 1, 3] & Sum = 20", or a function
# signature. Brackets or an equals sign, and no bound anywhere.
EXAMPLE_SHAPED = re.compile(r"[\[\]]|=")


def looks_like_an_example(input_format: str) -> bool:
    """Is this one worked example rather than a description of the format?

    Worth having as its own function because two callers need it and they do
    different things with the answer. The report says the statement is not
    usable as it stands. Extraction uses it to know that the problem has no
    stdin format at all, which means one has to be designed -- and that the
    samples, being written in the statement's own notation rather than the
    designed one, cannot be used to check the result.
    """
    text = input_format or ""
    return bool(text and not BOUND.search(text) and EXAMPLE_SHAPED.search(text))


def report(path: Path) -> int:
    data = json.loads(path.read_text())
    print(f"{data.get('title', '(untitled)')}   {path}")
    print()

    problems: list[str] = []

    for field, what, used_by in REQUIRED:
        value = data.get(field)
        if value in (None, "", [], {}):
            print(f"  MISSING  {field:15} {what}")
            print(f"           {'':15} needed by: {used_by}")
            problems.append(field)
        else:
            print(f"  present  {field:15} {what}")

    text = data.get("constraints") or ""
    if text and not BOUND.search(text):
        print()
        print("  WARNING  constraints has text but no comparison in it,")
        print("           so no bound can be read out of it")
        problems.append("constraints")

    # An input format that is one worked example rather than a description of
    # the shape cannot say how many values there are or how big they get.
    fmt = data.get("input_format") or ""
    if looks_like_an_example(fmt):
        print()
        print("  WARNING  input_format looks like one example rather than a format:")
        print(f"           {fmt!r}")
        print("           a format names the variables and their order,")
        print("           an example only shows one case")
        problems.append("input_format")

    print()
    if problems:
        unique = sorted(set(problems))
        print(f"cannot build a validator: {', '.join(unique)}")
        print("the bounds have to come out of the statement as numbers,")
        print("not as prose, before any of the three tools can be emitted")
        return 1

    print("has the fields needed to attempt an IR")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "usage: python -m testgen.inspect_statement <extracted.json>",
            file=sys.stderr,
        )
        return 2
    return report(Path(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
