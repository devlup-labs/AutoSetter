"""Describe a problem's input format in words, for a prompt.

The solution generator asks a model for C++ that solves a problem, and it sends
the statement as prose. Prose does not say how the input arrives, so the model
falls back on what it has seen most: a function taking arguments, with a
hardcoded example in main. That solution cannot be run against a test file.

The IR already knows the format, because the validator is built from it. This
turns the same description into text a model can follow, so the solution, the
validator and the generator all agree on the format without anyone keeping
three copies of it in step.
"""

from __future__ import annotations

from testgen.ir.schema import ArrayVar, Bound, IntVar, Problem, StringVar, Variable

ORDER_WORDS = {
    "increasing": "strictly increasing",
    "non_decreasing": "non-decreasing",
    "decreasing": "strictly decreasing",
    "non_increasing": "non-increasing",
}


def _range(lo: Bound, hi: Bound, name: str) -> str:
    return f"{lo} <= {name} <= {hi}"


def _describe(var: Variable) -> str:
    """One human sentence about what a variable is and what it may hold."""
    if isinstance(var, IntVar):
        return f"{var.name}, an integer with {_range(var.domain.lo, var.domain.hi, var.name)}"

    if isinstance(var, StringVar):
        return (
            f"{var.name}, a string of exactly {var.length} characters, "
            f"each one of [{var.alphabet}]"
        )

    if isinstance(var, ArrayVar):
        parts = [
            f"{var.name}, {var.length} integers separated by single spaces, "
            f"each with {_range(var.elem.lo, var.elem.hi, f'{var.name}[i]')}"
        ]
        if var.monotone:
            parts.append(f"given in {ORDER_WORDS[var.monotone]} order")
        if var.distinct:
            parts.append("all pairwise distinct")
        return ", ".join(parts)

    raise NotImplementedError(f"cannot describe a variable of kind {var.kind!r}")


def emit_io_contract(problem: Problem) -> str:
    """Return the input and output description to paste into a prompt."""
    out = ["INPUT FORMAT. Read all of this from standard input, in exactly this order:"]

    if problem.multitest:
        count = problem.test_count
        out.append(
            f"  line 1: {count.name}, the number of test cases, "
            f"with {_range(count.domain.lo, count.domain.hi, count.name)}"
        )
        out.append(f"  then {count.name} test cases, each one being:")
        prefix = "    "
        start = 1
    else:
        prefix = "  "
        start = 1

    for offset, line in enumerate(problem.body.lines):
        described = [
            _describe(problem.body.variable(token)) for token in line.tokens
        ]
        out.append(f"{prefix}line {start + offset}: " + "; ".join(described))

    for constraint in problem.global_constraints:
        out.append(
            f"  Across all test cases, the sum of {constraint.var} "
            f"{constraint.op} {constraint.value}."
        )

    out += [
        "",
        "OUTPUT FORMAT. Write to standard output only.",
    ]
    if problem.output.float_eps is not None:
        out.append(
            f"  Real numbers are accepted within {problem.output.float_eps} "
            "of the correct value."
        )
    if not problem.output.unique_answer:
        out.append(
            "  More than one answer may be correct. Print any valid one."
        )
    if problem.output.case_insensitive:
        out.append("  Letter case in the answer does not matter.")

    out += [
        "",
        "RULES.",
        "  Read every value from standard input with cin or scanf.",
        "  Do not hardcode any input value.",
        "  Do not write a function that takes the input as arguments; the program",
        "  is run as a whole and given its input on stdin.",
        "  Print nothing except the answer. No prompts, no labels.",
    ]
    return "\n".join(out)


def main() -> None:
    import sys

    from testgen.ir.problems import load

    if len(sys.argv) != 2:
        print("usage: python -m testgen.emit.io_contract <problem>", file=sys.stderr)
        raise SystemExit(2)

    print(emit_io_contract(load(sys.argv[1])))


if __name__ == "__main__":
    main()
