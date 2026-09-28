"""
autosetter.testgen.layout
=========================
Render values to input text, read input text back into values, and check
values against the spec.

Reading is what makes the spec trustworthy: the problem's official samples
are parsed with the spec's layout and checked against its constraints. A spec
that rejects an official sample is wrong, and is sent back to the model
before any test is generated. Generated tests go through the same
read-and-check round trip, so a builder bug can never ship an invalid test.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from autosetter.testgen.builders import MAX_ITEMS
from autosetter.testgen.expr import ExprError, eval_int, evaluate, source_of
from autosetter.testgen.spec import BLOCK_TYPES, TestSpec, Variable

Values = Dict[str, Any]

_INT_RE = re.compile(r"^[+-]?\d+$")


class LayoutError(Exception):
    """Raised when input text does not match the spec's layout."""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _inline(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(map(str, value))
    return str(value)


def _block(var: Variable, value: Any) -> List[str]:
    if var.type == "tree" and var.opt("format", "edges") == "parents":
        return [" ".join(map(str, value))]
    if var.type == "matrix" and var.alphabet:
        return list(value)
    return [" ".join(map(str, row)) for row in value]


def render(spec: TestSpec, top: Values, cases: List[Values]) -> str:
    """Format one input file."""
    lines: List[str] = []
    if spec.multi:
        lines.append(str(top[spec.multi.count]))
    for values in cases:
        for line in spec.layout:
            first = spec.by_name[line[0]]
            if len(line) == 1 and first.type in BLOCK_TYPES:
                lines.extend(_block(first, values[first.name]))
            else:
                lines.append(" ".join(_inline(values[name]) for name in line).strip())
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class _Tokens:
    def __init__(self, text: str) -> None:
        self.tokens = text.split()
        self.pos = 0

    def token(self, what: str) -> str:
        if self.pos >= len(self.tokens):
            raise LayoutError(f"input ended while reading {what}")
        self.pos += 1
        return self.tokens[self.pos - 1]

    def int(self, what: str) -> int:
        tok = self.token(what)
        if not _INT_RE.match(tok):
            raise LayoutError(f"expected an integer for {what}, got {tok!r}")
        return int(tok)

    def ints(self, count: int, what: str) -> List[int]:
        return [self.int(what) for _ in range(count)]


def _size(var: Variable, key: str, env: Values) -> int:
    try:
        value = eval_int(var.exprs[key], env, f"{var.name}.{key}")
    except ExprError as exc:
        raise LayoutError(str(exc)) from exc
    if value < 0 or value > MAX_ITEMS:
        raise LayoutError(f"{var.name}.{key} evaluated to {value}, which is not a usable size")
    return value


def _read(var: Variable, env: Values, tok: _Tokens) -> Any:
    name, vtype = var.name, var.type
    if vtype == "int":
        return tok.int(name)
    if vtype in ("array", "permutation"):
        return tok.ints(_size(var, "length", env), f"elements of {name}")
    if vtype == "string":
        return tok.token(name) if _size(var, "length", env) > 0 else ""
    if vtype == "matrix":
        rows, cols = _size(var, "rows", env), _size(var, "cols", env)
        if var.alphabet:
            return [tok.token(f"a row of {name}") for _ in range(rows)]
        return [tok.ints(cols, f"a row of {name}") for _ in range(rows)]
    if vtype == "rows":
        count, width = _size(var, "count", env), len(var.fields)
        return [tuple(tok.ints(width, f"a row of {name}")) for _ in range(count)]
    width = 3 if var.weight is not None else 2
    if vtype == "tree":
        n = _size(var, "nodes", env)
        if var.opt("format", "edges") == "parents":
            return tok.ints(max(n - 1, 0), f"parents in {name}")
        return [tuple(tok.ints(width, f"an edge of {name}")) for _ in range(max(n - 1, 0))]
    m = _size(var, "edges", env)
    return [tuple(tok.ints(width, f"an edge of {name}")) for _ in range(m)]


def parse_input(spec: TestSpec, text: str) -> Tuple[Values, List[Values]]:
    """Read an input file into (top-level values, per-test-case values)."""
    tok = _Tokens(text)
    top: Values = {}
    num_cases = 1
    if spec.multi:
        num_cases = tok.int(spec.multi.count)
        if num_cases < 0 or num_cases > MAX_ITEMS:
            raise LayoutError(f"{spec.multi.count} = {num_cases} is not a usable test count")
        top[spec.multi.count] = num_cases

    cases: List[Values] = []
    for _ in range(num_cases):
        env = dict(top)
        for line in spec.layout:
            for name in line:
                env[name] = _read(spec.by_name[name], env, tok)
        cases.append(env)

    extra = len(tok.tokens) - tok.pos
    if extra:
        raise LayoutError(
            f"{extra} unread token(s) after the last test case, starting with {tok.tokens[tok.pos]!r}"
        )
    return top, cases


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------

def _in_range(value: int, lo: int, hi: int) -> bool:
    return lo <= value <= hi


class _DSU:
    def __init__(self) -> None:
        self.parent: Dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[ra] = rb
        return True


def _check_weights(var: Variable, env: Values, edges: List[Tuple[int, ...]]) -> List[str]:
    if var.weight is None:
        return []
    lo = eval_int(var.weight["min"], env)
    hi = eval_int(var.weight["max"], env)
    for edge in edges:
        if not _in_range(edge[2], lo, hi):
            return [f"{var.name}: edge weight {edge[2]} is outside [{lo}, {hi}]"]
    return []


def _check_var(var: Variable, env: Values) -> List[str]:
    name, value = var.name, env[var.name]

    if var.type == "int":
        lo, hi = eval_int(var.exprs["min"], env), eval_int(var.exprs["max"], env)
        return [] if _in_range(value, lo, hi) else [f"{name} = {value} is outside [{lo}, {hi}]"]

    if var.type == "array":
        n = eval_int(var.exprs["length"], env)
        lo, hi = eval_int(var.exprs["min"], env), eval_int(var.exprs["max"], env)
        if len(value) != n:
            return [f"{name} has {len(value)} elements, expected {n}"]
        for i, x in enumerate(value):
            if not _in_range(x, lo, hi):
                return [f"{name}[{i}] = {x} is outside [{lo}, {hi}]"]
        order = var.opt("order")
        if var.opt("distinct") and len(set(value)) != len(value):
            return [f"{name} must have distinct elements"]
        pairs = list(zip(value, value[1:]))
        ok = {
            "ascending": all(a < b for a, b in pairs),
            "descending": all(a > b for a, b in pairs),
            "non_decreasing": all(a <= b for a, b in pairs),
            "non_increasing": all(a >= b for a, b in pairs),
        }.get(order, True)
        return [] if ok else [f"{name} is not {order.replace('_', '-')}"]

    if var.type == "permutation":
        n = eval_int(var.exprs["length"], env)
        expected = list(range(var.indexing, var.indexing + n))
        return [] if sorted(value) == expected else [
            f"{name} is not a permutation of {var.indexing}..{var.indexing + n - 1}"
        ]

    if var.type == "string":
        n = eval_int(var.exprs["length"], env)
        if len(value) != n:
            return [f"{name} has length {len(value)}, expected {n}"]
        bad = set(value) - set(var.alphabet)
        return [f"{name} contains characters {sorted(bad)} outside its alphabet"] if bad else []

    if var.type == "matrix":
        rows, cols = eval_int(var.exprs["rows"], env), eval_int(var.exprs["cols"], env)
        if len(value) != rows or any(len(r) != cols for r in value):
            return [f"{name} is not {rows}x{cols}"]
        if var.alphabet:
            bad = set("".join(value)) - set(var.alphabet)
            return [f"{name} contains characters {sorted(bad)} outside its alphabet"] if bad else []
        lo, hi = eval_int(var.exprs["min"], env), eval_int(var.exprs["max"], env)
        for r, row in enumerate(value):
            for c, x in enumerate(row):
                if not _in_range(x, lo, hi):
                    return [f"{name}[{r}][{c}] = {x} is outside [{lo}, {hi}]"]
        return []

    if var.type == "rows":
        for r, row in enumerate(value):
            row_env = dict(env)
            for f, x in zip(var.fields, row):
                lo, hi = eval_int(f.min, row_env), eval_int(f.max, row_env)
                if not _in_range(x, lo, hi):
                    return [f"{name} row {r + 1}: {f.name} = {x} is outside [{lo}, {hi}]"]
                row_env[f.name] = x
        return []

    lo_label = var.indexing
    if var.type == "tree":
        n = eval_int(var.exprs["nodes"], env)
        hi_label = lo_label + n - 1
        if var.opt("format", "edges") == "parents":
            dsu = _DSU()
            for offset, p in enumerate(value):
                child = lo_label + 1 + offset
                if not _in_range(p, lo_label, hi_label):
                    return [f"{name}: parent {p} of node {child} is not a node"]
                if var.opt("parent_before_child", True) and p >= child:
                    return [f"{name}: parent {p} of node {child} is not smaller than the node"]
                if not dsu.union(child, p):
                    return [f"{name}: parent pointers form a cycle"]
            return []
        dsu = _DSU()
        for edge in value:
            u, v = edge[0], edge[1]
            if not (_in_range(u, lo_label, hi_label) and _in_range(v, lo_label, hi_label)):
                return [f"{name}: edge ({u}, {v}) uses a node outside {lo_label}..{hi_label}"]
            if not dsu.union(u, v):
                return [f"{name}: edge ({u}, {v}) closes a cycle, so it is not a tree"]
        return _check_weights(var, env, value)

    # graph
    n = eval_int(var.exprs["nodes"], env)
    hi_label = lo_label + n - 1
    directed = bool(var.opt("directed", False))
    seen = set()
    dsu = _DSU()
    for edge in value:
        u, v = edge[0], edge[1]
        if not (_in_range(u, lo_label, hi_label) and _in_range(v, lo_label, hi_label)):
            return [f"{name}: edge ({u}, {v}) uses a node outside {lo_label}..{hi_label}"]
        if u == v and not var.opt("self_loops", False):
            return [f"{name}: self-loop ({u}, {v}) is not allowed"]
        key = (u, v) if directed else (min(u, v), max(u, v))
        if key in seen and not var.opt("multi_edges", False):
            return [f"{name}: repeated edge ({u}, {v}) is not allowed"]
        seen.add(key)
        dsu.union(u, v)
    if var.opt("connected", False) and n > 1:
        roots = {dsu.find(x) for x in range(lo_label, hi_label + 1)}
        if len(roots) > 1:
            return [f"{name} is not connected"]
    return _check_weights(var, env, value)


def check(spec: TestSpec, top: Values, cases: List[Values]) -> List[str]:
    """Every way the values break the spec (empty when they satisfy it)."""
    problems: List[str] = []
    try:
        if spec.multi:
            count = top[spec.multi.count]
            lo, hi = eval_int(spec.multi.min, {}), eval_int(spec.multi.max, {})
            if not _in_range(count, lo, hi):
                problems.append(f"{spec.multi.count} = {count} is outside [{lo}, {hi}]")

        for index, env in enumerate(cases, start=1):
            prefix = f"test case {index}: " if spec.multi else ""
            for var in spec.variables:
                problems.extend(prefix + p for p in _check_var(var, env))
            for node in spec.constraints:
                if not evaluate(node, env):
                    problems.append(f"{prefix}constraint '{source_of(node)}' is violated")

        for node in spec.global_constraints:
            def sum_fn(arg: Any) -> int:
                return sum(evaluate(arg, env) for env in cases)

            if not evaluate(node, top, sum_fn):
                problems.append(f"global constraint '{source_of(node)}' is violated")
    except ExprError as exc:
        problems.append(str(exc))
    return problems


def check_input(spec: TestSpec, text: str) -> List[str]:
    """Parse and check an input file; returns the problems found."""
    try:
        top, cases = parse_input(spec, text)
    except LayoutError as exc:
        return [f"does not match the layout: {exc}"]
    return check(spec, top, cases)


def check_samples(spec: TestSpec, samples: List[Dict[str, Any]]) -> List[str]:
    """Check the problem's official sample inputs against the spec."""
    problems: List[str] = []
    for index, sample in enumerate(samples or [], start=1):
        text = str((sample or {}).get("input") or "")
        if not text.strip():
            continue
        for problem in check_input(spec, text)[:5]:
            problems.append(f"sample {index}: {problem}")
    return problems
