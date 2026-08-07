"""Emit a testlib generator from a constraint IR.

A generator writes a test input and nothing else. It never writes the answer,
because the answer comes from running the model solution: if the generator
produced both, a mistake in it would produce an input and an answer that agree
with each other and nothing would ever catch it.

The one property that matters is that the same arguments always produce the
same file. A problem package stores the generator command, not the megabytes it
produces, and regenerates on demand. testlib gives this for free, because
registerGen seeds its random source from the command line arguments, so
`gen random 3` is the same test today and next year.

The mode argument selects a shape rather than a size: min and max push every
value to a bound, flat makes everything equal, and the budget modes spend a sum
across test cases in the several different ways it can be spent.
"""

from __future__ import annotations

from testgen.ir.schema import ArrayVar, Bound, IntVar, Problem, StringVar

INDENT = "    "

# Helpers the generated file always carries. They are written once here rather
# than assembled per problem, because they do not vary and are easier to read
# as ordinary C++ than as a pile of f-strings.
HELPERS = '''
static int pick(int lo, int hi, const std::string& mode) {
    if (mode == "min") return lo;
    if (mode == "max") return hi;
    return rnd.next(lo, hi);
}

// Distinct values need care: with "min" the smallest legal set is the run
// starting at lo, and a random set has to reject repeats rather than hope.
static std::vector<int> make_values(int n, int lo, int hi,
                                    const std::string& mode, bool distinct) {
    std::vector<int> v(n);

    if (distinct) {
        ensuref((long long)hi - lo + 1 >= n,
            "cannot pick %d distinct values from [%d, %d]", n, lo, hi);
        if (mode == "min") {
            for (int i = 0; i < n; i++) v[i] = lo + i;
        } else if (mode == "max") {
            for (int i = 0; i < n; i++) v[i] = hi - n + 1 + i;
        } else {
            std::set<int> used;
            for (int i = 0; i < n; i++) {
                int x;
                do { x = rnd.next(lo, hi); } while (used.count(x));
                used.insert(x);
                v[i] = x;
            }
        }
        shuffle(v.begin(), v.end());
        return v;
    }

    if (mode == "flat") {
        int c = rnd.next(lo, hi);
        for (int i = 0; i < n; i++) v[i] = c;
        return v;
    }
    for (int i = 0; i < n; i++) v[i] = pick(lo, hi, mode);
    return v;
}

// A strict order needs distinct values, so the caller asks for distinct when
// the order is strict. Here we only have to put them the right way round.
static void apply_order(std::vector<int>& v, const std::string& order) {
    if (order == "increasing" || order == "non_decreasing")
        std::sort(v.begin(), v.end());
    else if (order == "decreasing" || order == "non_increasing")
        std::sort(v.rbegin(), v.rend());
}

static std::string make_string(int n, const std::string& alphabet,
                               const std::string& mode) {
    std::string s;
    s.reserve(n);
    for (int i = 0; i < n; i++) {
        if (mode == "min" || mode == "flat") s += alphabet.front();
        else if (mode == "max") s += alphabet.back();
        else s += alphabet[rnd.next(0, (int)alphabet.size() - 1)];
    }
    return s;
}

static void print_values(const std::vector<int>& v) {
    if (v.empty()) { printf("\\n"); return; }
    for (size_t i = 0; i < v.size(); i++)
        printf("%d%c", v[i], i + 1 == v.size() ? '\\n' : ' ');
}
'''


def _expand_alphabet(alphabet: str) -> str:
    """Turn a character class body into the literal characters it allows.

    The validator hands the alphabet to testlib as part of a pattern, where
    "a-z" is a range. A generator needs the actual characters to choose from.
    """
    out: list[str] = []
    i = 0
    while i < len(alphabet):
        if i + 2 < len(alphabet) and alphabet[i + 1] == "-":
            out.extend(
                chr(c) for c in range(ord(alphabet[i]), ord(alphabet[i + 2]) + 1)
            )
            i += 3
        else:
            out.append(alphabet[i])
            i += 1
    return "".join(out)


def _bound(bound: Bound) -> str:
    return str(bound)


def _emit_variable(var, depth: int) -> list[str]:
    """Emit the code that produces one variable's value."""
    pad = INDENT * depth

    if isinstance(var, IntVar):
        lo, hi = _bound(var.domain.lo), _bound(var.domain.hi)
        return [f"{pad}int {var.name} = pick({lo}, {hi}, mode);"]

    if isinstance(var, StringVar):
        letters = _expand_alphabet(var.alphabet).replace("\\", "\\\\").replace('"', '\\"')
        return [
            f"{pad}std::string {var.name} = make_string("
            f'{_bound(var.length)}, "{letters}", mode);'
        ]

    if isinstance(var, ArrayVar):
        # A strictly increasing or decreasing run cannot repeat a value, so ask
        # for distinct values even when the statement only promised an order.
        strict = var.monotone in ("increasing", "decreasing")
        distinct = "true" if (var.distinct or strict) else "false"
        order = var.monotone or ""
        out = [
            f"{pad}std::vector<int> {var.name} = make_values("
            f"{_bound(var.length)}, {_bound(var.elem.lo)}, "
            f"{_bound(var.elem.hi)}, mode, {distinct});"
        ]
        if order:
            out.append(f'{pad}apply_order({var.name}, "{order}");')
        else:
            # Without a promised order these modes are still worth reaching,
            # because sorted input is a common accidental best case.
            out += [
                f'{pad}if (mode == "sorted") apply_order({var.name}, "non_decreasing");',
                f'{pad}if (mode == "reversed") apply_order({var.name}, "non_increasing");',
            ]
        return out

    raise NotImplementedError(f"cannot generate a variable of kind {var.kind!r}")


def _emit_line_print(problem: Problem, line, depth: int) -> list[str]:
    """Emit the printing of one input line."""
    pad = INDENT * depth
    scalars: list[str] = []
    out: list[str] = []

    def flush() -> None:
        if scalars:
            fmt = " ".join("%d" for _ in scalars)
            out.append(f'{pad}printf("{fmt}\\n", {", ".join(scalars)});')
            scalars.clear()

    for token in line.tokens:
        var = problem.body.variable(token)
        if isinstance(var, IntVar):
            scalars.append(var.name)
        elif isinstance(var, ArrayVar):
            flush()
            out.append(f"{pad}print_values({var.name});")
        elif isinstance(var, StringVar):
            flush()
            out.append(f'{pad}printf("%s\\n", {var.name}.c_str());')
    flush()
    return out


def _emit_budget(problem: Problem) -> list[str]:
    """Decide how many test cases there are and how the budget is split.

    A sum across test cases has no single largest test. The same total can be
    one enormous case or ten thousand tiny ones, and those break different
    things, so each shape gets its own mode.
    """
    count = problem.test_count
    constraint = problem.global_constraints[0]
    var = constraint.var
    budget = constraint.value

    sized = problem.body.variable(var)
    lo, hi = _bound(sized.domain.lo), _bound(sized.domain.hi)
    t_lo, t_hi = _bound(count.domain.lo), _bound(count.domain.hi)

    return [
        f"{INDENT}// {var} is capped at {budget} summed over every test case.",
        f"{INDENT}const int BUDGET = {budget};",
        f"{INDENT}int {count.name};",
        f"{INDENT}std::vector<int> sizes;",
        "",
        f'{INDENT}if (mode == "budget_one_big") {{',
        f"{INDENT*2}{count.name} = 1;",
        f"{INDENT*2}sizes.assign(1, std::min(BUDGET, {hi}));",
        f'{INDENT}}} else if (mode == "budget_many_small") {{',
        f"{INDENT*2}{count.name} = {t_hi};",
        f"{INDENT*2}sizes.assign({count.name}, std::max({lo}, BUDGET / {count.name}));",
        f'{INDENT}}} else if (mode == "budget_skewed") {{',
        f"{INDENT*2}{count.name} = {t_hi};",
        f"{INDENT*2}sizes.assign({count.name}, {lo});",
        f"{INDENT*2}sizes[0] = std::min({hi}, BUDGET - ({count.name} - 1) * {lo});",
        f'{INDENT}}} else if (mode == "min") {{',
        f"{INDENT*2}{count.name} = {t_lo};",
        f"{INDENT*2}sizes.assign({count.name}, {lo});",
        f'{INDENT}}} else if (mode == "max") {{',
        f"{INDENT*2}{count.name} = 1;",
        f"{INDENT*2}sizes.assign(1, std::min(BUDGET, {hi}));",
        f"{INDENT}}} else {{",
        f"{INDENT*2}{count.name} = rnd.next({t_lo}, std::min({t_hi}, BUDGET));",
        f"{INDENT*2}sizes.assign({count.name}, {lo});",
        f"{INDENT*2}int spare = BUDGET - {count.name} * {lo};",
        f"{INDENT*2}for (int i = 0; i < {count.name} && spare > 0; i++) {{",
        f"{INDENT*3}int give = rnd.next(0, std::min(spare, {hi} - sizes[i]));",
        f"{INDENT*3}sizes[i] += give;",
        f"{INDENT*3}spare -= give;",
        f"{INDENT*2}}}",
        f"{INDENT}}}",
        "",
        f"{INDENT}long long total = 0;",
        f"{INDENT}for (int x : sizes) total += x;",
        f'{INDENT}ensuref(total {constraint.op} BUDGET,',
        f'{INDENT*2}"the split spends %lld, over the budget of %d", total, BUDGET);',
        "",
        f'{INDENT}printf("%d\\n", {count.name});',
    ]


def emit_generator(problem: Problem) -> str:
    """Return the C++ source of a generator for this problem."""
    if problem.multitest and len(problem.global_constraints) > 1:
        raise NotImplementedError(
            "more than one sum across test cases is not supported yet"
        )

    source = f" ({problem.source})" if problem.source else ""
    out = [
        "// Generated from the constraint IR. Do not edit by hand.",
        f"// Problem: {problem.name}{source}",
        "//",
        "// usage: gen <mode> <seed>",
        "// The same arguments always produce the same file.",
        '#include "testlib.h"',
        HELPERS,
        "int main(int argc, char* argv[]) {",
        f"{INDENT}registerGen(argc, argv, 1);",
        f"{INDENT}std::string mode = argc > 1 ? std::string(argv[1])"
        ' : std::string("random");',
        "",
    ]

    if not problem.multitest:
        for var in problem.body.variables:
            out.extend(_emit_variable(var, depth=1))
        out.append("")
        for line in problem.body.lines:
            out.extend(_emit_line_print(problem, line, depth=1))
    else:
        sized = problem.global_constraints[0].var if problem.global_constraints else None

        if problem.global_constraints:
            out.extend(_emit_budget(problem))
        else:
            count = problem.test_count
            out += [
                f"{INDENT}int {count.name} = pick({_bound(count.domain.lo)}, "
                f"{_bound(count.domain.hi)}, mode);",
                f'{INDENT}printf("%d\\n", {count.name});',
            ]

        out += ["", f"{INDENT}for (int tc = 0; tc < {problem.test_count.name}; tc++) {{"]
        for var in problem.body.variables:
            if var.name == sized:
                # This one's value was decided by the budget split, not freely.
                out.append(f"{INDENT*2}int {var.name} = sizes[tc];")
            else:
                out.extend(_emit_variable(var, depth=2))
        out.append("")
        for line in problem.body.lines:
            out.extend(_emit_line_print(problem, line, depth=2))
        out.append(f"{INDENT}}}")

    out += ["", f"{INDENT}return 0;", "}", ""]
    return "\n".join(out)


def main() -> None:
    import sys

    from testgen.ir.problems import load

    if len(sys.argv) != 2:
        print("usage: python -m testgen.emit.generator <problem>", file=sys.stderr)
        raise SystemExit(2)

    print(emit_generator(load(sys.argv[1])), end="")


if __name__ == "__main__":
    main()
