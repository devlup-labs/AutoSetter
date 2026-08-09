"""Which tests a problem needs, and why each one exists.

Running the generator ten times with ten different seeds produces ten tests
from the middle of the input space. That is the least useful set of tests
there is: nothing reaches a bound, so a solution that is too slow at the
maximum, or that breaks at the minimum, passes anyway.

    A bound nobody touches is a bound that does not exist.

So the plan asks for *shapes*, not seeds. Each shape selects a form of input
rather than a size, and each one records what it would catch — a test that
cannot say what it is for is not worth generating.

The mode names here are a contract with `prompts/generator.txt`: the prompt
requires the generated `generator.cpp` to accept a mode as its first argument
and to understand these words. A mode that does not apply to a given problem
(`flat` on a problem with no array) must fall back to a valid random test
rather than fail, so the vocabulary is safe to use on every problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# The shared vocabulary. Anything outside this list is not in the contract.
MODES = (
    "min", "max", "flat", "sorted", "reversed", "edge", "random",
    # For problems with several test cases per file. See BUDGET_MODES.
    "one_big", "many_small", "skewed",
)

# When a problem reads `t` test cases and caps their total size — the familiar
# `sum of n over all test cases <= 2*10^5` — there is no single largest test.
# The same budget can be one enormous case or ten thousand tiny ones, and those
# break different solutions:
#
#   one_big     stresses the algorithm itself
#   many_small  stresses whatever runs once per test case — clearing a global
#               array, reallocating, flushing output
#   skewed      one large case among many small ones, so a solution has to
#               survive both pressures in the same run
#
# `max` alone cannot cover this, and asking for `t` at its maximum *and* `n` at
# its maximum usually produces an illegal file, because their product exceeds
# the stated sum by orders of magnitude.
BUDGET_MODES = ("one_big", "many_small", "skewed")

# Modes whose output is fully determined by the constraints, so the seed
# cannot change them. See `seed_invariant_modes` for why that matters.
#
# The budget modes are deliberately NOT in here. They are just as determined on
# a multitest problem, but on a single-test problem they fall back to a random
# test, and a fallback is allowed to vary with the seed. Checking them would
# report every single-test problem as broken.
DETERMINED_BY_CONSTRAINTS = ("min", "max")


# Phrases that mean "this file contains several test cases with a leading
# count". Deliberately narrow: a statement that merely says "in this test case"
# in passing must not match, or every problem gets planned as a multitest one.
MULTITEST_PHRASES = re.compile(
    r"number of test cases"
    r"|sum of .{1,40} over all test cases"
    r"|(?:each|every) test case"
    r"|\bt\b\s+test cases"
    r"|first line contains (?:a single |an )?integer\s+t\b",
    re.IGNORECASE,
)


def looks_multitest(problem: Optional[Dict[str, Any]]) -> bool:
    """Does this problem read several test cases from one file?

    Read off the statement text, because there is nothing better to read: the
    extracted problem.json describes the input in prose. Both ways of being
    wrong are cheap, which is what makes a heuristic acceptable here.

    A false positive plans three budget tests on a single-test problem. The
    generator's contract says an inapplicable mode falls back to a valid random
    test, so the cost is three redundant tests and no invalid ones.

    A false negative skips them, and the problem is tested exactly as well as
    it was before these modes existed.

    Neither can produce a wrong verdict, only a less thorough one.
    """
    if not problem:
        return False

    text = "\n".join(
        str(problem.get(field, ""))
        for field in ("input_format", "constraints", "story", "notes")
    )
    return bool(MULTITEST_PHRASES.search(text))


@dataclass(frozen=True)
class TestShape:
    """One planned test: how to ask for it, and what it is for."""

    name: str
    mode: str
    seed: int
    purpose: str
    catches: str

    @property
    def args(self) -> List[str]:
        return [self.mode, str(self.seed)]


def default_plan(random_tests: int = 4, multitest: bool = True) -> List[TestShape]:
    """The tests every problem gets, whatever it is about.

    Without a machine-readable description of the input there is no way to
    know which shapes are meaningful for a particular problem, so the plan
    asks for all of them and relies on the generator to fall back to a random
    test where a shape does not apply.

    `multitest` is the one exception: the three budget shapes only mean
    anything when a file holds several test cases, so a problem that reads one
    test case per file skips them rather than paying for three more random
    tests dressed up as shapes.
    """
    plan = [
        TestShape("min", "min", 1,
                  "every value at its lower bound",
                  "empty loops, off-by-one, and degenerate cases"),
        TestShape("max", "max", 1,
                  "every value at its upper bound",
                  "a solution too slow for the largest legal input"),
        TestShape("edge", "edge", 1,
                  "smallest and largest values in the same test",
                  "overflow, and sign handling"),
        TestShape("flat", "flat", 1,
                  "every element the same value",
                  "code that assumes the values vary"),
        TestShape("sorted", "sorted", 1,
                  "input already in ascending order",
                  "an accidental best case being mistaken for the worst"),
        TestShape("reversed", "reversed", 1,
                  "input in descending order",
                  "the worst case for anything insertion-like"),

    ]

    # A cap on a total across test cases has no single largest test, so each
    # way of spending the budget gets its own shape.
    budget = [
        TestShape("one_big", "one_big", 1,
                  "the whole budget spent on a single test case",
                  "an algorithm that is too slow at the maximum size"),
        TestShape("many_small", "many_small", 1,
                  "the budget spread over as many test cases as allowed",
                  "per-test-case work, such as clearing a global array"),
        TestShape("skewed", "skewed", 1,
                  "one large test case among many minimal ones",
                  "both pressures at once, which either shape alone misses"),
    ]
    if multitest:
        plan += budget

    plan += [
        TestShape(f"random_{seed}", "random", seed,
                  "uniformly random",
                  "whatever the shaped tests did not think of")
        for seed in range(1, random_tests + 1)
    ]

    return plan


def shaped_count(multitest: bool = True) -> int:
    """How many tests are fixed by the constraints rather than by a count."""
    return len(default_plan(random_tests=0, multitest=multitest))


def seed_invariant_modes() -> List[str]:
    """Modes that must produce the same file whatever the seed.

    This is how the pipeline finds out whether the generator honours its mode
    argument at all, which nothing else can detect without a machine-readable
    description of the input.

    "Every value at its lower bound" describes exactly one input. Ask for it
    twice with two different seeds and a generator that understands the mode
    returns identical bytes both times. A generator that ignored the argument
    and produced a random test returns two different files — and that
    difference is the proof.
    """
    return list(DETERMINED_BY_CONSTRAINTS)


def describe(plan: List[TestShape]) -> str:
    """A readable table of the plan, for looking at before generating."""
    name_w = max(len(t.name) for t in plan)
    args_w = max(len(" ".join(t.args)) for t in plan)
    what_w = max(len(t.purpose) for t in plan)

    out = [
        "",
        f"  {len(plan)} tests",
        "",
        f"  {'TEST':<{name_w}}  {'ARGS':<{args_w}}  "
        f"{'WHAT IT IS':<{what_w}}  WHAT IT CATCHES",
        f"  {'-' * name_w}  {'-' * args_w}  {'-' * what_w}  {'-' * 44}",
    ]
    for t in plan:
        out.append(
            f"  {t.name:<{name_w}}  {' '.join(t.args):<{args_w}}  "
            f"{t.purpose:<{what_w}}  {t.catches}"
        )
    out.append("")
    return "\n".join(out)
