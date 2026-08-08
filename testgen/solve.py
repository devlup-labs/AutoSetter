"""Use a solver to choose the shape of a test.

Most of a test can be produced by asking for random numbers in a range, and the
generator does exactly that. What random numbers are bad at is landing on a
combination that satisfies several constraints at once, particularly a cap on a
sum across test cases: pick sizes independently and their total lands near the
middle of what is allowed, never at the edge where the interesting cases are.

So the split is:

    the solver decides the shape       how many test cases, how big each one is
    the generator fills the bulk       the actual values inside them

The solver is not used for the bulk. Neither Z3 nor any other will hand back a
two hundred thousand element array in reasonable time, and it does not need to,
because random values in a range are exactly what random number generators are
for.

One thing worth knowing about the answers it gives. The solver works by walking
between corners of the region its constraints describe, so the shapes it returns
are lopsided by construction: one value at its maximum and the rest at their
minimum, rather than anything balanced. For a test generator that bias is a gift,
because corners are where constraints are tight and tight constraints are edge
cases.

The consequence is that it is also deterministic, and returns that same corner
every time. Forbidding an answer and asking again only moves it to the
neighbouring corner, which is barely a different test. Changing the direction it
is optimising in moves it somewhere else entirely, so that is what the seed does
here: it picks random weights for the objective.
"""

from __future__ import annotations

import random

from z3 import Int, Optimize, Sum, sat

from testgen.ir.schema import Problem


class NoShapeFound(Exception):
    """Raised when the constraints cannot be satisfied at all."""


def solve_sizes(problem: Problem, test_count: int, seed: int) -> list[int]:
    """Choose a size for each test case, obeying every constraint at once.

    Returns one size per test case for the variable a global constraint caps.
    """
    if not problem.global_constraints:
        raise NoShapeFound("this problem has no constraint across test cases")

    constraint = problem.global_constraints[0]
    sized = problem.body.variable(constraint.var)
    lo, hi = sized.domain.lo, sized.domain.hi

    sizes = [Int(f"n_{i}") for i in range(test_count)]
    opt = Optimize()

    for size in sizes:
        opt.add(size >= lo, size <= hi)
    opt.add(Sum(sizes) <= constraint.value)

    # A random direction rather than a random value. Asking the solver to
    # forbid its previous answer only nudges it to the corner next door; asking
    # it to prefer a different direction sends it to a different part of the
    # region altogether.
    #
    # This only varies the answer while the cap is actually binding. If every
    # test case can sit at its maximum and still stay under the total, nothing
    # is competing for the budget and any direction gives the same all-maximum
    # answer. See `budget_binds` for the test.
    rng = random.Random(seed)
    opt.maximize(Sum([rng.randint(0, 1000) * size for size in sizes]))

    if opt.check() != sat:
        raise NoShapeFound(
            f"no split of {test_count} test cases satisfies "
            f"{lo} <= {constraint.var} <= {hi} with a total of "
            f"{constraint.op} {constraint.value}"
        )

    model = opt.model()
    return [model[size].as_long() for size in sizes]


def budget_binds(problem: Problem, test_count: int) -> bool:
    """Is the cap on the total actually restricting anything?

    If every test case could sit at its maximum and the total would still be
    under the cap, then nothing is competing for the budget: each size is free,
    and the solver will put all of them at their maximum whatever it is asked
    to prefer. Below that many test cases there is no interesting split to find.
    """
    constraint = problem.global_constraints[0]
    sized = problem.body.variable(constraint.var)
    return test_count * sized.domain.hi > constraint.value


def saturating_count(problem: Problem) -> int:
    """How many test cases spend the whole budget at maximum size.

    A cap on a sum has no single largest test. If the budget is several times
    one test case's maximum, then the heaviest legal input is not one enormous
    case, it is however many maximum sized cases fit. That shape is missed
    entirely by asking for one big case or for many tiny ones.
    """
    constraint = problem.global_constraints[0]
    sized = problem.body.variable(constraint.var)
    count = constraint.value // sized.domain.hi
    return max(1, min(count, problem.test_count.domain.hi))


def describe_shape(sizes: list[int]) -> str:
    """A short summary, since printing ten thousand numbers helps nobody."""
    total = sum(sizes)
    largest = max(sizes)
    smallest = min(sizes)
    at_max = sum(1 for s in sizes if s == largest)
    return (
        f"{len(sizes)} test cases, total {total:,}, "
        f"largest {largest:,} ({at_max} of them), smallest {smallest:,}"
    )
