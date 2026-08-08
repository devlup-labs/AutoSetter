"""Turn an extracted problem.json into a draft constraint IR.

The extraction step produces a problem in prose: a title, a story, an input
format written for a person to read, and a constraints field that is a
transcription of the sentences in the statement. That is the right output for
showing a problem to somebody, and the wrong input for building a validator,
because a sentence has no bounds a program can act on.

This reads that file and pulls out whatever it can mechanically:

    "1 <= n <= 2*10^5"                          -> a range on n
    "the sum of n over all test cases <= 3*10^6" -> a constraint across cases

and reports everything it could not work out, rather than inventing it. A
draft IR with three TODOs in it is honest. A complete-looking IR with a guessed
bound is not, because the guess disappears into a validator that then enforces
a limit the problem never stated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# 10^5, 10**5, 2*10^5, 2 * 10^5, 2·10^5 and plain digits all appear in
# statements depending on who transcribed them.
POWER = re.compile(
    r"(?:(?P<mult>\d+)\s*[*x·⋅]\s*)?10\s*(?:\^|\*\*)\s*(?P<exp>\d+)"
)
PLAIN = re.compile(r"^\d+$")

# "1 <= n <= 2*10^5" and its unicode and LaTeX spellings.
RANGE = re.compile(
    r"(?P<lo>[-+]?[\w^*·\s]+?)\s*(?:<=|≤|\\le)\s*"
    r"(?P<name>[a-zA-Z][a-zA-Z0-9_]*(?:_[a-zA-Z0-9]+)?)\s*"
    r"(?:<=|≤|\\le)\s*(?P<hi>[-+]?[\w^*·\s]+?)(?=[,;.\n]|$)"
)

# "the sum of n over all test cases does not exceed 3*10^6"
SUM_OVER = re.compile(
    r"sum\s+of\s+(?P<name>[a-zA-Z][a-zA-Z0-9_]*)\s+over\s+all\s+test\s+cases"
    r"[^0-9]*(?P<value>[\d^*·\s]+)",
    re.IGNORECASE,
)


class AdaptationIncomplete(Exception):
    """Raised when the extracted problem cannot produce a usable IR."""


def parse_number(text: str) -> int | None:
    """Read a bound written the way statements write them."""
    text = text.strip().replace(" ", "").replace(",", "")
    if not text:
        return None

    if PLAIN.match(text):
        return int(text)

    match = POWER.search(text)
    if match:
        mult = int(match.group("mult") or 1)
        return mult * (10 ** int(match.group("exp")))

    negative = text.startswith("-")
    stripped = text.lstrip("+-")
    if PLAIN.match(stripped):
        return -int(stripped) if negative else int(stripped)

    return None


def find_ranges(text: str) -> dict[str, tuple[int, int]]:
    """Pull every `lo <= name <= hi` out of a constraints field."""
    found: dict[str, tuple[int, int]] = {}
    for match in RANGE.finditer(text):
        lo = parse_number(match.group("lo"))
        hi = parse_number(match.group("hi"))
        name = match.group("name")
        if lo is not None and hi is not None:
            found[name] = (lo, hi)
    return found


def find_sums(text: str) -> dict[str, int]:
    """Pull every `sum of X over all test cases <= N` out of a text field."""
    found: dict[str, int] = {}
    for match in SUM_OVER.finditer(text):
        value = parse_number(match.group("value"))
        if value is not None:
            found[match.group("name")] = value
    return found


def adapt(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a draft IR and the list of things a person still has to decide."""
    # Bounds are supposed to live in "constraints", but transcriptions
    # routinely leave them in the input format or the story instead, so all
    # three are searched.
    searchable = "\n".join(
        str(data.get(field, ""))
        for field in ("constraints", "input_format", "story", "notes")
    )

    ranges = find_ranges(searchable)
    sums = find_sums(searchable)
    todo: list[str] = []

    if not (data.get("constraints") or "").strip():
        todo.append(
            "the constraints field is empty, so every bound below was taken "
            "from the input format or the story, if it was found at all"
        )
    if not ranges:
        todo.append(
            "no bounds of the form 'lo <= name <= hi' were found anywhere; "
            "they have to be read off the statement or chosen deliberately"
        )

    multitest = "t" in ranges and bool(sums or "test case" in searchable.lower())
    variables = [
        {
            "kind": "int",
            "name": name,
            "domain": {"lo": lo, "hi": hi},
            "source": f"read from the extracted problem: {lo} <= {name} <= {hi}",
        }
        for name, (lo, hi) in ranges.items()
        if not (multitest and name == "t")
    ]

    ir: dict[str, Any] = {
        "name": data.get("title") or "Untitled",
        "source": "adapted from an extracted problem.json",
        "multitest": multitest,
        "body": {
            "variables": variables,
            # The layout cannot be read from prose, so it is left as one
            # variable per line for a person to correct.
            "lines": [{"tokens": [v["name"]]} for v in variables],
        },
        "global_constraints": [
            {
                "kind": "sum_over_tests",
                "var": name,
                "op": "<=",
                "value": value,
                "source": f"sum of {name} over all test cases <= {value}",
            }
            for name, value in sums.items()
        ],
        "output": {
            "unique_answer": True,
            "case_insensitive": _mentions_any_case(data.get("output_format", "")),
            "float_eps": None,
            "tokens_per_test": 1,
        },
    }

    if multitest:
        lo, hi = ranges["t"]
        ir["test_count"] = {
            "kind": "int",
            "name": "t",
            "domain": {"lo": lo, "hi": hi},
            "source": f"read from the extracted problem: {lo} <= t <= {hi}",
        }

    todo.append(
        "the line layout is a guess of one value per line; check it against "
        "the input format and fix `lines` if values share a line"
    )
    if not sums and multitest:
        todo.append(
            "the problem has multiple test cases but no sum across them was "
            "found; check whether the statement guarantees one"
        )

    return ir, todo


def _mentions_any_case(output_format: str) -> bool:
    text = output_format.lower()
    return "either case" in text or "any case" in text or "upper or lower" in text


def report(path: Path) -> int:
    """Read an extracted problem, print the draft IR and what is still missing."""
    data = json.loads(path.read_text())
    ir, todo = adapt(data)

    print(json.dumps(ir, indent=2))
    print()

    if todo:
        print("STILL TO DECIDE")
        for item in todo:
            print(f"  - {item}")
        print()
        print(
            "Nothing above was invented. Fill these in, save the result into\n"
            "testgen/ir/problems/, then run `python -m testgen check <name>`\n"
            "against the samples to confirm the bounds are right."
        )
        return 1

    print("every field was read from the extracted problem")
    return 0


def main() -> int:
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m testgen.adapt <problem.json>", file=sys.stderr)
        return 2
    return report(Path(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
