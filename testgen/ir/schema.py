"""Constraint IR for competitive programming problems.

A problem statement is really just a set of constraints on its input. This module
defines a machine readable form of those constraints, so that the generator, the
validator and the checker can all be produced from one description instead of
being written by hand three times.

The important design choice is that a bound is allowed to be either a literal
integer or the name of another variable. That is what makes constraints like
`1 <= l <= r <= n` expressible: `r` has lower bound `"l"` and upper bound `"n"`.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

# A bound is either a literal value or a reference to another variable by name.
Bound = Union[int, str]

ComparisonOp = Literal["<=", "<", "==", ">=", ">"]


# Where a bound came from. A statement that says "1 <= n <= 2*10^5" states its
# bounds; a LeetCode style statement states none at all, and somebody has to
# pick them before a validator can exist. Those two are not the same thing and
# must not look the same once they are in the IR, because `n <= 500` and
# `n <= 10^5` are different problems with different intended solutions.
#
# The default is "stated", so every hand-written IR keeps its current meaning.
BoundOrigin = Literal["stated", "chosen"]


class IntDomain(BaseModel):
    """The set of values an integer may take, as a closed interval."""

    lo: Bound
    hi: Bound
    origin: BoundOrigin = Field(
        default="stated",
        description="Whether the statement gave this range or we picked it.",
    )


class IntVar(BaseModel):
    """A single integer read from the input."""

    kind: Literal["int"] = "int"
    name: str
    domain: IntDomain
    source: str | None = Field(
        default=None,
        description="The sentence in the statement this came from.",
    )


# How the values of an array relate to their neighbours, when the statement
# guarantees an order. "non_increasing" allows equal neighbours, "decreasing"
# does not, and getting that wrong makes a validator reject legal tests.
Monotonicity = Literal[
    "increasing",
    "non_decreasing",
    "decreasing",
    "non_increasing",
]


class ArrayVar(BaseModel):
    """A sequence of integers whose length is given by another variable."""

    kind: Literal["array"] = "array"
    name: str
    length: Bound
    elem: IntDomain
    monotone: Monotonicity | None = Field(
        default=None,
        description="Set when the statement guarantees an order, e.g. 'in non-increasing order'.",
    )
    distinct: bool = Field(
        default=False,
        description="Set when the statement says the values are pairwise different.",
    )
    source: str | None = None


class StringVar(BaseModel):
    """A string of known length over a fixed alphabet."""

    kind: Literal["string"] = "string"
    name: str
    length: Bound
    alphabet: str = Field(description="Every character allowed, e.g. '01'.")
    source: str | None = None


Variable = Annotated[
    Union[IntVar, ArrayVar, StringVar],
    Field(discriminator="kind"),
]


class SumOverTests(BaseModel):
    """A cap on a variable summed across every test case.

    This is the constraint that cannot be checked while reading a single test
    case, because it is not a property of any one of them. It needs an
    accumulator and a check once the loop has finished.
    """

    kind: Literal["sum_over_tests"] = "sum_over_tests"
    var: str
    op: ComparisonOp = "<="
    value: int
    source: str | None = None


GlobalConstraint = Annotated[
    Union[SumOverTests],
    Field(discriminator="kind"),
]


class Line(BaseModel):
    """One line of input, given as the variables appearing on it in order.

    An array or string variable occupies the whole of its position: an array
    named `a` on a line means all of its elements, separated by single spaces.
    """

    tokens: list[str]


class Block(BaseModel):
    """A group of variables together with the layout that reads them."""

    variables: list[Variable] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)

    def variable(self, name: str) -> Variable | None:
        for var in self.variables:
            if var.name == name:
                return var
        return None


class OutputSpec(BaseModel):
    """What a correct answer looks like.

    This decides which checker the problem needs. A unique exact answer can use
    a stock token comparison; anything else needs more.
    """

    unique_answer: bool = True
    case_insensitive: bool = False
    float_eps: float | None = None
    tokens_per_test: Bound | None = Field(
        default=None,
        description="How many tokens the answer has per test case.",
    )


class Problem(BaseModel):
    """A complete description of a problem's input and output format."""

    name: str
    source: str | None = Field(
        default=None, description="Where the problem came from, e.g. 'CF 4A'."
    )
    multitest: bool = False
    test_count: IntVar | None = Field(
        default=None,
        description="The leading count of test cases, when the problem has one.",
    )
    body: Block = Field(
        description="Read once, or once per test case when multitest is set."
    )
    global_constraints: list[GlobalConstraint] = Field(default_factory=list)
    output: OutputSpec = Field(default_factory=OutputSpec)

    @model_validator(mode="after")
    def check_multitest_agrees(self) -> Problem:
        if self.multitest and self.test_count is None:
            raise ValueError("a multitest problem must declare test_count")
        if not self.multitest and self.test_count is not None:
            raise ValueError("test_count is only meaningful when multitest is set")
        if not self.multitest and self.global_constraints:
            raise ValueError(
                "global constraints sum across test cases, "
                "so they need multitest to be set"
            )
        return self

    @model_validator(mode="after")
    def check_names_resolve(self) -> Problem:
        """Every name used as a bound, length or token must exist.

        Catches the common extraction failure where a model invents a variable
        that the statement never mentions, or misspells one that it does.
        """
        known = {var.name for var in self.body.variables}
        if self.test_count is not None:
            known.add(self.test_count.name)

        def require(name: object, context: str) -> None:
            if isinstance(name, str) and name not in known:
                raise ValueError(f"{context} refers to unknown variable {name!r}")

        for var in self.body.variables:
            if isinstance(var, IntVar):
                require(var.domain.lo, f"lower bound of {var.name}")
                require(var.domain.hi, f"upper bound of {var.name}")
            else:
                require(var.length, f"length of {var.name}")
                if isinstance(var, ArrayVar):
                    require(var.elem.lo, f"element lower bound of {var.name}")
                    require(var.elem.hi, f"element upper bound of {var.name}")

        for constraint in self.global_constraints:
            require(constraint.var, "global constraint")

        for line in self.body.lines:
            for token in line.tokens:
                require(token, "input format")

        require(self.output.tokens_per_test, "output token count")
        return self
