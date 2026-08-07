"""Choose or emit a checker from a constraint IR.

A checker decides whether a contestant's output is correct. When the answer is
a single exact value, comparing the two files is enough and testlib already
ships a checker that does it, so the right move is to pick one rather than
write another. Writing a checker where a stock one would do means shipping
untested code in the one place a bug silently fails correct submissions.

Choosing is most of the job:

    unique and numeric        -> ncmp, stock
    unique tokens             -> wcmp, stock
    letter case irrelevant    -> a small template, because no stock file does it
    real numbers with a limit -> rcmp with the tolerance from the IR
    more than one right answer -> REFUSE

The last one matters. If several answers are correct, deciding between them
needs the rules of the problem, not the shape of its output, and no amount of
looking at the IR supplies that. Emitting something anyway would produce a
checker that rejects correct submissions, so this raises instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from testgen.ir.schema import Problem


class CheckerNeedsHuman(Exception):
    """Raised when the problem's rules are needed to judge an answer."""


@dataclass(frozen=True)
class CheckerChoice:
    """Which checker a problem needs, and why."""

    kind: str
    stock: str | None
    reason: str

    @property
    def is_stock(self) -> bool:
        return self.stock is not None


def choose_checker(problem: Problem) -> CheckerChoice:
    """Decide which checker this problem's output calls for."""
    output = problem.output

    if not output.unique_answer:
        raise CheckerNeedsHuman(
            f"{problem.name}: more than one answer may be correct, so the checker "
            "has to verify a submitted answer against the rules of the problem. "
            "That cannot be derived from the output format. Write it by hand."
        )

    if output.float_eps is not None:
        return CheckerChoice(
            "float",
            None,
            f"real numbers accepted within {output.float_eps}",
        )

    if output.case_insensitive:
        return CheckerChoice(
            "case_insensitive",
            None,
            "letter case does not matter, which no stock checker handles",
        )

    return CheckerChoice(
        "tokens",
        "wcmp.cpp",
        "one exact answer, so comparing tokens is enough",
    )


HEADER = """// Generated from the constraint IR. Do not edit by hand.
// Problem: {name}{source}
//
// {reason}
#include "testlib.h"

int main(int argc, char* argv[]) {{
    registerTestlibCmd(argc, argv);
"""

# Contestant output is read through ouf so that anything malformed becomes a
# verdict instead of undefined behaviour. Reading it with cin would let a
# truncated or enormous file crash the checker instead of failing the
# submission.
CASE_INSENSITIVE_BODY = """
    int index = 0;
    while (!ans.seekEof()) {
        index++;
        std::string expected = upperCase(ans.readToken());
        std::string found = upperCase(ouf.readToken());

        if (expected != found)
            quitf(_wa, "token %d: expected %s, found %s",
                index, expected.c_str(), found.c_str());
    }

    // A submission that prints the right answers followed by anything else is
    // still wrong, so the end of the file has to be checked too.
    if (!ouf.seekEof())
        quitf(_pe, "%d tokens were expected, but the output continues", index);

    quitf(_ok, "%d token(s) correct", index);
}
"""

FLOAT_BODY = """
    const double EPS = {eps};
    int index = 0;

    while (!ans.seekEof()) {{
        index++;
        double expected = ans.readDouble();
        double found = ouf.readDouble();

        if (!doubleCompare(expected, found, EPS))
            quitf(_wa, "value %d: expected %.10f, found %.10f (allowed %.10f)",
                index, expected, found, EPS);
    }}

    if (!ouf.seekEof())
        quitf(_pe, "%d values were expected, but the output continues", index);

    quitf(_ok, "%d value(s) within %.10f", index, EPS);
}}
"""


def emit_checker(problem: Problem) -> str:
    """Return the C++ source of a checker, for the kinds that need a custom one.

    Raises CheckerNeedsHuman when the answer is not unique, and ValueError when
    a stock checker should be used instead of generating one.
    """
    choice = choose_checker(problem)

    if choice.is_stock:
        raise ValueError(
            f"{problem.name} should use testlib's {choice.stock} rather than a "
            f"generated checker: {choice.reason}"
        )

    source = f" ({problem.source})" if problem.source else ""
    header = HEADER.format(name=problem.name, source=source, reason=choice.reason)

    if choice.kind == "case_insensitive":
        return header + CASE_INSENSITIVE_BODY

    if choice.kind == "float":
        return header + FLOAT_BODY.format(eps=problem.output.float_eps)

    raise NotImplementedError(f"no template for checker kind {choice.kind!r}")


def describe(problem: Problem) -> str:
    """A one line summary of what this problem's checker should be."""
    try:
        choice = choose_checker(problem)
    except CheckerNeedsHuman as exc:
        return f"{problem.name}: WRITE BY HAND\n  {exc}"

    where = f"testlib's {choice.stock}" if choice.is_stock else "generated here"
    return f"{problem.name}: {choice.kind} ({where})\n  {choice.reason}"


def main() -> None:
    import sys

    from testgen.ir.problems import load

    if len(sys.argv) != 2:
        print("usage: python -m testgen.emit.checker <problem>", file=sys.stderr)
        raise SystemExit(2)

    problem = load(sys.argv[1])
    try:
        print(emit_checker(problem), end="")
    except (CheckerNeedsHuman, ValueError) as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
