"""Emit a testlib validator from a constraint IR.

A validator reads a test input and decides whether it obeys every rule in the
statement. It never looks at the output. Two things have to be checked, and the
second is the one people forget:

  values  -- every number inside its declared range
  format  -- exactly one space between tokens, one newline at the end of a line,
             and nothing at all after the last one

testlib is strict about this on purpose. `readSpace()` means one space, not a
tab and not two spaces, so a test file that would parse differently in different
languages gets rejected before it ever reaches a contestant.
"""

from __future__ import annotations

from testgen.ir.schema import Bound, IntVar, Problem

HEADER = """// Generated from the constraint IR. Do not edit by hand.
// Problem: {name}{source}
#include "testlib.h"

int main(int argc, char* argv[]) {{
    registerValidation(argc, argv);
"""

FOOTER = """
    inf.readEof();
    return 0;
}
"""


def _bound(bound: Bound) -> str:
    """Render a bound as C++.

    A bound is either a literal, which prints as itself, or the name of a
    variable read earlier, which prints as that variable.
    """
    return str(bound)


def _referenced_names(problem: Problem) -> set[str]:
    """Names used as somebody else's bound, length or count.

    Only these need to be stored in a C++ variable. Reading the rest into a
    variable would just produce unused-variable warnings.
    """
    names: set[str] = set()

    def note(bound: object) -> None:
        if isinstance(bound, str):
            names.add(bound)

    for var in problem.body.variables:
        if isinstance(var, IntVar):
            note(var.domain.lo)
            note(var.domain.hi)
        else:
            note(var.length)

    for constraint in problem.global_constraints:
        names.add(constraint.var)

    note(problem.output.tokens_per_test)
    return names


def _read_int(var: IntVar, store: bool, indent: str = "    ") -> str:
    lo = _bound(var.domain.lo)
    hi = _bound(var.domain.hi)
    call = f'inf.readInt({lo}, {hi}, "{var.name}")'
    if store:
        return f"{indent}int {var.name} = {call};"
    return f"{indent}{call};"


def emit_validator(problem: Problem) -> str:
    """Return the C++ source of a validator for this problem."""
    if problem.multitest:
        raise NotImplementedError("multitest problems are not supported yet")

    stored = _referenced_names(problem)
    source = f" ({problem.source})" if problem.source else ""
    parts = [HEADER.format(name=problem.name, source=source)]

    for line in problem.body.lines:
        for position, token in enumerate(line.tokens):
            var = problem.body.variable(token)
            if not isinstance(var, IntVar):
                raise NotImplementedError(
                    f"{token!r} is not a plain integer; "
                    "arrays and strings are not supported yet"
                )
            parts.append(_read_int(var, store=var.name in stored))
            if position + 1 < len(line.tokens):
                parts.append("    inf.readSpace();")
        parts.append("    inf.readEoln();")

    parts.append(FOOTER)
    return "\n".join(parts)


def main() -> None:
    import sys

    from testgen.ir.problems import load

    if len(sys.argv) != 2:
        print("usage: python -m testgen.emit.validator <problem>", file=sys.stderr)
        raise SystemExit(2)

    print(emit_validator(load(sys.argv[1])), end="")


if __name__ == "__main__":
    main()
