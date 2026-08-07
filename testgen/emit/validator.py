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

from testgen.ir.schema import ArrayVar, Bound, IntVar, Problem, StringVar, Variable

INDENT = "    "


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
            if isinstance(var, ArrayVar):
                note(var.elem.lo)
                note(var.elem.hi)

    for constraint in problem.global_constraints:
        names.add(constraint.var)

    note(problem.output.tokens_per_test)
    return names


def _emit_token(var: Variable, stored: bool, depth: int) -> list[str]:
    """Emit the read for one variable, without any surrounding whitespace."""
    pad = INDENT * depth

    if isinstance(var, IntVar):
        call = f'inf.readInt({_bound(var.domain.lo)}, ' \
               f'{_bound(var.domain.hi)}, "{var.name}")'
        if stored:
            return [f"{pad}int {var.name} = {call};"]
        return [f"{pad}{call};"]

    if isinstance(var, StringVar):
        # testlib takes a pattern, so the alphabet and the length are checked
        # together in one call.
        pattern = f'format("[{var.alphabet}]{{%d}}", {_bound(var.length)})'
        return [f'{pad}inf.readToken({pattern}, "{var.name}");']

    if isinstance(var, ArrayVar):
        # Elements are separated by single spaces, with no trailing space
        # before the newline, so the separator goes between elements only.
        loop = f"i_{var.name}"
        length = _bound(var.length)
        return [
            f"{pad}for (int {loop} = 0; {loop} < {length}; {loop}++) {{",
            f"{pad}{INDENT}inf.readInt({_bound(var.elem.lo)}, "
            f'{_bound(var.elem.hi)}, format("{var.name}[%d]", {loop}));',
            f"{pad}{INDENT}if ({loop} + 1 < {length}) inf.readSpace();",
            f"{pad}}}",
        ]

    raise NotImplementedError(f"cannot read variable of kind {var.kind!r}")


def _emit_lines(problem: Problem, stored: set[str], depth: int) -> list[str]:
    """Emit every line of the repeated body."""
    out: list[str] = []
    pad = INDENT * depth

    for line in problem.body.lines:
        for position, token in enumerate(line.tokens):
            var = problem.body.variable(token)
            if var is None:
                raise ValueError(f"input format names unknown variable {token!r}")
            out.extend(_emit_token(var, token in stored, depth))
            if position + 1 < len(line.tokens):
                out.append(f"{pad}inf.readSpace();")
        out.append(f"{pad}inf.readEoln();")

    return out


def _accumulator(var: str) -> str:
    return f"sum_{var}"


def emit_validator(problem: Problem) -> str:
    """Return the C++ source of a validator for this problem."""
    stored = _referenced_names(problem)
    source = f" ({problem.source})" if problem.source else ""

    out = [
        "// Generated from the constraint IR. Do not edit by hand.",
        f"// Problem: {problem.name}{source}",
        '#include "testlib.h"',
        "",
        "int main(int argc, char* argv[]) {",
        f"{INDENT}registerValidation(argc, argv);",
        "",
    ]

    if not problem.multitest:
        out.extend(_emit_lines(problem, stored, depth=1))
    else:
        count = problem.test_count
        out += [
            f"{INDENT}int {count.name} = inf.readInt("
            f'{_bound(count.domain.lo)}, {_bound(count.domain.hi)}, "{count.name}");',
            f"{INDENT}inf.readEoln();",
        ]

        # A global constraint is a property of the whole file, not of any one
        # test case, so it is accumulated here and checked once the loop ends.
        # The total can reach t * max, which overflows a 32 bit int, and an
        # overflowed total can wrap negative and pass the check.
        if problem.global_constraints:
            out.append("")
            for constraint in problem.global_constraints:
                out.append(
                    f"{INDENT}long long {_accumulator(constraint.var)} = 0;"
                )

        out += [
            "",
            f"{INDENT}for (int tc = 0; tc < {count.name}; tc++) {{",
        ]
        out.extend(_emit_lines(problem, stored, depth=2))
        for constraint in problem.global_constraints:
            out.append(
                f"{INDENT * 2}{_accumulator(constraint.var)} += {constraint.var};"
            )
        out.append(f"{INDENT}}}")

        for constraint in problem.global_constraints:
            total = _accumulator(constraint.var)
            out += [
                "",
                f"{INDENT}ensuref({total} {constraint.op} {constraint.value},",
                f'{INDENT * 2}"sum of {constraint.var} is %lld, '
                f'which breaks the limit of {constraint.value}", {total});',
            ]

    out += [
        "",
        f"{INDENT}inf.readEof();",
        f"{INDENT}return 0;",
        "}",
        "",
    ]
    return "\n".join(out)


def main() -> None:
    import sys

    from testgen.ir.problems import load

    if len(sys.argv) != 2:
        print("usage: python -m testgen.emit.validator <problem>", file=sys.stderr)
        raise SystemExit(2)

    print(emit_validator(load(sys.argv[1])), end="")


if __name__ == "__main__":
    main()
