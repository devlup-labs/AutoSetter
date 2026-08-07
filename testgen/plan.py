"""Work out which tests a problem needs, from its constraints.

Writing a generator is the easy half. Knowing which tests to ask it for is the
part that decides whether the problem is any good, and it follows from the
constraints rather than from taste:

  a scalar range          needs its lowest and highest value used somewhere
  an array                needs to be all equal, all smallest, all largest
  an order guarantee      needs the flattest sequence the order still allows
  a sum across test cases needs each of the shapes that spends the budget

The rule behind all of it is that a bound nobody touches is a bound that does
not exist. If the statement promises n can reach 200000 and no test ever goes
above 1000, a solution that is too slow for 200000 passes anyway.

Each entry records why it exists. A test that cannot say what it would catch is
not worth generating.
"""

from __future__ import annotations

from dataclasses import dataclass

from testgen.ir.schema import ArrayVar, Problem, StringVar


@dataclass(frozen=True)
class PlannedTest:
    """One test, as the arguments to hand the generator plus its reason.

    `purpose` says what the test is in a few words, so a plan reads as a table.
    `catches` says which mistake it would expose, which is the part that
    justifies the test existing at all.
    """

    name: str
    mode: str
    seed: int
    purpose: str
    catches: str

    @property
    def args(self) -> list[str]:
        return [self.mode, str(self.seed)]


def plan_tests(problem: Problem) -> list[PlannedTest]:
    """Return the tests this problem's constraints call for."""
    tests: list[PlannedTest] = [
        PlannedTest("min", "min", 1,
            "every value at its lower bound",
            "empty loops and off-by-one"),
        PlannedTest("max", "max", 1,
            "every value at its upper bound",
            "a bound nothing ever reaches"),
    ]

    has_array = any(isinstance(v, ArrayVar) for v in problem.body.variables)
    has_string = any(isinstance(v, StringVar) for v in problem.body.variables)

    if has_array:
        tests.append(PlannedTest("flat", "flat", 1,
            "every element the same value",
            "code that assumes variety"))
        ordered = any(
            isinstance(v, ArrayVar) and v.monotone for v in problem.body.variables
        )
        if not ordered:
            tests += [
                PlannedTest("sorted", "sorted", 1,
                    "already in ascending order",
                    "a lucky best case"),
                PlannedTest("reversed", "reversed", 1,
                    "descending order",
                    "the worst case for anything insertion-like"),
            ]

    if has_string:
        tests.append(PlannedTest("one_letter", "flat", 1,
            "one repeated character",
            "code that assumes both letters appear"))

    # A sum across test cases can be spent in several shapes, and they break
    # different things: one huge case stresses the algorithm, ten thousand tiny
    # ones stress whatever the solution does per test case. There is no single
    # largest test, so all of them are needed.
    if problem.global_constraints:
        tests += [
            PlannedTest("budget_one_big", "budget_one_big", 1,
                "the whole budget in one test case",
                "a slow algorithm"),
            PlannedTest("budget_many_small", "budget_many_small", 1,
                "the budget across as many cases as allowed",
                "per test case work, such as clearing a global array"),
            PlannedTest("budget_skewed", "budget_skewed", 1,
                "one large case, the rest minimal",
                "both pressures at once"),
        ]

    tests += [
        PlannedTest(f"random_{seed}", "random", seed,
            "uniform random", "whatever the shaped tests missed")
        for seed in (1, 2, 3)
    ]

    return tests


def describe(problem: Problem) -> str:
    """A readable table of the plan, for looking at before generating."""
    rows = plan_tests(problem)
    name_w = max(len(t.name) for t in rows)
    args_w = max(len(" ".join(t.args)) for t in rows)
    what_w = max(len(t.purpose) for t in rows)

    out = [
        "",
        f"  {problem.name} - {len(rows)} tests",
        "",
        f"  {'TEST':<{name_w}}  {'ARGS':<{args_w}}  "
        f"{'WHAT IT IS':<{what_w}}  WHAT IT CATCHES",
        f"  {'-' * name_w}  {'-' * args_w}  {'-' * what_w}  {'-' * 40}",
    ]
    for test in rows:
        out.append(
            f"  {test.name:<{name_w}}  {' '.join(test.args):<{args_w}}  "
            f"{test.purpose:<{what_w}}  {test.catches}"
        )
    out.append("")
    return "\n".join(out)
