"""
autosetter.testgen.spec
=======================
The test spec: a declarative description of a problem's input.

The text model writes the spec (``test_spec.json``) from ``problem.json``;
this module loads it and rejects anything malformed with messages specific
enough to feed back to the model for a retry.

Shape::

    {
      "multi_test": {"count": "t", "min": 1, "max": 10000},    # or null
      "variables": [                                            # one test case
        {"name": "n", "type": "int", "min": 1, "max": 200000},
        {"name": "k", "type": "int", "min": 1, "max": "n"},
        {"name": "a", "type": "array", "length": "n", "min": 1, "max": 1000000000}
      ],
      "constraints": ["k <= n"],                                # per test case
      "global_constraints": ["sum(n) <= 200000"],               # across the file
      "layout": [["n", "k"], ["a"]]                             # lines of one test case
    }

Integer variables are solved by Z3; every other type is built directly from
the solved integers (see `builders`).
"""

from __future__ import annotations

import ast
import json
import keyword
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from autosetter.testgen.expr import FUNCTIONS, ExprError, names_in, parse_expr, sum_args

TYPES = {"int", "array", "permutation", "string", "matrix", "rows", "tree", "graph"}

# Types rendered over several lines; they must sit alone on a layout line.
BLOCK_TYPES = {"matrix", "rows", "tree", "graph"}

ORDERS = {"ascending", "descending", "non_decreasing", "non_increasing"}

# Size fields per type: what must be known before the value can be read.
SIZE_FIELDS = {
    "array": ("length",),
    "permutation": ("length",),
    "string": ("length",),
    "matrix": ("rows", "cols"),
    "rows": ("count",),
    "tree": ("nodes",),
    "graph": ("nodes", "edges"),
}

DEFAULT_ALPHABET = "a-z"


class SpecError(Exception):
    """Raised when a test spec is malformed. The message lists every problem found."""


@dataclass
class RowField:
    name: str
    min: ast.expr
    max: ast.expr


@dataclass
class Variable:
    name: str
    type: str
    raw: Dict[str, Any]
    exprs: Dict[str, ast.expr] = field(default_factory=dict)
    fields: List[RowField] = field(default_factory=list)
    alphabet: str = ""
    weight: Optional[Dict[str, ast.expr]] = None

    def opt(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def indexing(self) -> int:
        return int(self.raw.get("indexing", 1))


@dataclass
class MultiTest:
    count: str
    min: ast.expr
    max: ast.expr


@dataclass
class TestSpec:
    raw: Dict[str, Any]
    multi: Optional[MultiTest]
    variables: List[Variable]
    constraints: List[ast.expr]
    global_constraints: List[ast.expr]
    layout: List[List[str]]

    def __post_init__(self) -> None:
        self.by_name: Dict[str, Variable] = {v.name: v for v in self.variables}

    @property
    def int_names(self) -> List[str]:
        return [v.name for v in self.variables if v.type == "int"]

    def to_json(self) -> str:
        return json.dumps(self.raw, indent=2, ensure_ascii=False) + "\n"


def expand_alphabet(spec: str) -> str:
    """Expand ``"a-z0-9"``-style character sets; a literal '-' may lead or trail."""
    chars: List[str] = []
    i = 0
    while i < len(spec):
        if i + 2 < len(spec) and spec[i + 1] == "-":
            lo, hi = spec[i], spec[i + 2]
            if ord(lo) > ord(hi):
                raise SpecError(f"invalid alphabet range '{lo}-{hi}'")
            chars.extend(chr(c) for c in range(ord(lo), ord(hi) + 1))
            i += 3
        else:
            chars.append(spec[i])
            i += 1
    unique = "".join(dict.fromkeys(chars))
    if not unique or any(c.isspace() for c in unique):
        raise SpecError(f"alphabet {spec!r} must be non-empty and contain no whitespace")
    return unique


class _Collector:
    """Accumulates validation errors so the model sees all of them at once."""

    def __init__(self) -> None:
        self.errors: List[str] = []

    def add(self, message: str) -> None:
        self.errors.append(message)

    def expr(self, where: str, source: Any, allowed: Set[str]) -> Optional[ast.expr]:
        try:
            node = parse_expr(source)
        except ExprError as exc:
            self.add(f"{where}: {exc}")
            return None
        if sum_args(node):
            self.add(f"{where}: sum(...) is only allowed in global_constraints")
            return None
        unknown = names_in(node) - allowed
        if unknown:
            self.add(
                f"{where}: refers to {sorted(unknown)}, which are not integer "
                f"variables defined before it (available: {sorted(allowed) or 'none'})"
            )
            return None
        return node


def _check_name(c: _Collector, name: Any, where: str, taken: Set[str]) -> bool:
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
        c.add(f"{where}: name {name!r} must be a plain identifier")
        return False
    if name in FUNCTIONS:
        c.add(f"{where}: name '{name}' is reserved")
        return False
    if name in taken:
        c.add(f"{where}: name '{name}' is defined more than once")
        return False
    taken.add(name)
    return True


def _bool_opt(c: _Collector, var: Dict[str, Any], key: str, where: str) -> None:
    if key in var and not isinstance(var[key], bool):
        c.add(f"{where}: '{key}' must be true or false")


def load_spec(source: str | Dict[str, Any]) -> TestSpec:
    """Parse and validate a spec given as a dict or JSON text. Raises SpecError."""
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except json.JSONDecodeError as exc:
            raise SpecError(f"spec is not valid JSON: {exc}") from exc
    if not isinstance(source, dict):
        raise SpecError("spec must be a JSON object")

    c = _Collector()
    taken: Set[str] = set()
    known_unknown = set(source) - {
        "multi_test", "variables", "constraints", "global_constraints", "layout", "notes"
    }
    if known_unknown:
        c.add(f"unknown top-level keys {sorted(known_unknown)}")

    # ── multi_test ──
    multi: Optional[MultiTest] = None
    raw_multi = source.get("multi_test")
    if raw_multi is not None:
        if not isinstance(raw_multi, dict):
            c.add("multi_test must be an object or null")
        elif _check_name(c, raw_multi.get("count"), "multi_test.count", taken):
            lo = c.expr("multi_test.min", raw_multi.get("min"), set())
            hi = c.expr("multi_test.max", raw_multi.get("max"), set())
            if lo is not None and hi is not None:
                multi = MultiTest(raw_multi["count"], lo, hi)

    scope: Set[str] = {multi.count} if multi else set()
    base_scope = set(scope)

    # ── variables ──
    variables: List[Variable] = []
    raw_vars = source.get("variables")
    if not isinstance(raw_vars, list) or not raw_vars:
        c.add("variables must be a non-empty list")
        raw_vars = []

    for index, raw in enumerate(raw_vars):
        where = f"variables[{index}]"
        if not isinstance(raw, dict):
            c.add(f"{where} must be an object")
            continue
        name, vtype = raw.get("name"), raw.get("type")
        where = f"variable '{name}'"
        if not _check_name(c, name, f"variables[{index}]", taken):
            continue
        if vtype not in TYPES:
            c.add(f"{where}: type must be one of {sorted(TYPES)}, got {vtype!r}")
            continue

        var = Variable(name=name, type=vtype, raw=raw)

        def need(key: str, allowed: Set[str] = scope) -> None:
            if key not in raw:
                c.add(f"{where}: missing '{key}'")
                return
            node = c.expr(f"{where}.{key}", raw[key], set(allowed))
            if node is not None:
                var.exprs[key] = node

        if vtype == "int":
            need("min")
            need("max")
        elif vtype == "array":
            need("length")
            need("min")
            need("max")
            _bool_opt(c, raw, "distinct", where)
            if "order" in raw and raw["order"] not in ORDERS:
                c.add(f"{where}: order must be one of {sorted(ORDERS)}")
        elif vtype == "permutation":
            need("length")
        elif vtype == "string":
            need("length")
        elif vtype == "matrix":
            need("rows")
            need("cols")
            if "alphabet" not in raw:
                need("min")
                need("max")
        elif vtype == "rows":
            need("count")
            raw_fields = raw.get("fields")
            if not isinstance(raw_fields, list) or not raw_fields:
                c.add(f"{where}: 'fields' must be a non-empty list of {{name, min, max}}")
            else:
                row_scope = set(scope)
                for f_index, rf in enumerate(raw_fields):
                    f_where = f"{where}.fields[{f_index}]"
                    if not isinstance(rf, dict):
                        c.add(f"{f_where} must be an object")
                        continue
                    if not _check_name(c, rf.get("name"), f_where, taken):
                        continue
                    lo = c.expr(f"{f_where}.min", rf.get("min"), row_scope)
                    hi = c.expr(f"{f_where}.max", rf.get("max"), row_scope)
                    if lo is not None and hi is not None:
                        var.fields.append(RowField(rf["name"], lo, hi))
                    row_scope.add(rf["name"])
        elif vtype == "tree":
            need("nodes")
            if raw.get("format", "edges") not in ("edges", "parents"):
                c.add(f"{where}: format must be 'edges' or 'parents'")
        elif vtype == "graph":
            need("nodes")
            need("edges")
            for key in ("directed", "connected", "self_loops", "multi_edges"):
                _bool_opt(c, raw, key, where)

        if vtype in ("string", "matrix") and ("alphabet" in raw or vtype == "string"):
            alphabet = raw.get("alphabet", DEFAULT_ALPHABET)
            if not isinstance(alphabet, str):
                c.add(f"{where}: alphabet must be a string such as \"a-z\" or \"01\"")
            else:
                try:
                    var.alphabet = expand_alphabet(alphabet)
                except SpecError as exc:
                    c.add(f"{where}: {exc}")

        if vtype in ("permutation", "tree", "graph") and raw.get("indexing", 1) not in (0, 1):
            c.add(f"{where}: indexing must be 0 or 1")

        if vtype in ("tree", "graph") and raw.get("weight") is not None:
            weight = raw["weight"]
            if not isinstance(weight, dict):
                c.add(f"{where}: weight must be an object {{min, max}} or null")
            else:
                lo = c.expr(f"{where}.weight.min", weight.get("min"), scope)
                hi = c.expr(f"{where}.weight.max", weight.get("max"), scope)
                if lo is not None and hi is not None:
                    var.weight = {"min": lo, "max": hi}

        variables.append(var)
        if vtype == "int":
            scope.add(name)

    int_scope = set(scope)

    # ── constraints ──
    constraints: List[ast.expr] = []
    for index, text in enumerate(source.get("constraints") or []):
        node = c.expr(f"constraints[{index}]", text, int_scope)
        if node is not None:
            constraints.append(node)

    global_constraints: List[ast.expr] = []
    per_test_ints = int_scope - base_scope
    for index, text in enumerate(source.get("global_constraints") or []):
        where = f"global_constraints[{index}]"
        try:
            node = parse_expr(text)
        except ExprError as exc:
            c.add(f"{where}: {exc}")
            continue
        outside = names_in(node, include_sum_args=False) - base_scope
        if outside:
            c.add(
                f"{where}: {sorted(outside)} must be wrapped in sum(...), "
                "e.g. \"sum(n) <= 200000\""
            )
            continue
        bad = set().union(*(names_in(a) for a in sum_args(node))) - per_test_ints
        if bad:
            c.add(f"{where}: sum(...) may only use per-test integer variables, not {sorted(bad)}")
            continue
        global_constraints.append(node)

    # ── layout ──
    layout: List[List[str]] = []
    raw_layout = source.get("layout")
    if not isinstance(raw_layout, list) or not raw_layout:
        c.add("layout must be a non-empty list of lines, each a list of variable names")
        raw_layout = []
    by_name = {v.name: v for v in variables}
    seen: List[str] = []
    for index, line in enumerate(raw_layout):
        if isinstance(line, str):
            line = [line]
        if not isinstance(line, list) or not line:
            c.add(f"layout[{index}] must be a non-empty list of variable names")
            continue
        for name in line:
            var = by_name.get(name) if isinstance(name, str) else None
            if var is None:
                hint = " (the multi_test count is printed automatically)" if multi and name == multi.count else ""
                c.add(f"layout[{index}]: '{name}' is not a declared variable{hint}")
                continue
            if var.type in BLOCK_TYPES and len(line) > 1:
                c.add(f"layout[{index}]: '{name}' is a multi-line {var.type} and must be alone on its line")
            needed = set().union(*(names_in(var.exprs[k]) for k in SIZE_FIELDS.get(var.type, ()) if k in var.exprs)) - base_scope
            missing = needed - set(seen)
            if missing:
                c.add(
                    f"layout[{index}]: the size of '{name}' depends on {sorted(missing)}, "
                    "which must be printed earlier in the layout"
                )
            if name in seen:
                c.add(f"layout: '{name}' appears more than once")
            seen.append(name)
        layout.append(list(line))
    for var in variables:
        if var.name not in seen:
            c.add(f"layout: variable '{var.name}' is declared but never printed")

    if c.errors:
        raise SpecError("\n".join(f"- {e}" for e in c.errors))

    return TestSpec(
        raw=source,
        multi=multi,
        variables=variables,
        constraints=constraints,
        global_constraints=global_constraints,
        layout=layout,
    )


def load_spec_file(path: str | Path) -> TestSpec:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"cannot read test spec {path}: {exc}") from exc
    return load_spec(text)
