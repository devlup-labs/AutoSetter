"""
autosetter.testgen.expr
=======================
Safe arithmetic/boolean expressions for test specs.

Spec bounds and constraints are short Python-like expressions such as
``"n - 1"``, ``"k <= n"`` or ``"sum(n) <= 200000"``. They are parsed with
`ast` and evaluated by a small interpreter that accepts only arithmetic,
comparisons, boolean logic and a few functions -- never `eval`, since specs
are written by a model.

The same interpreter runs over two kinds of values:
- plain Python ints, when checking a concrete test or computing a length;
- Z3 integer expressions, when building the constraint model to solve.
"""

from __future__ import annotations

import ast
from typing import Any, Callable, Dict, Mapping, Optional, Set

import z3


class ExprError(Exception):
    """Raised when an expression is malformed, unsupported, or cannot be evaluated."""


FUNCTIONS = {"min", "max", "abs", "sum"}

_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp,
    ast.BoolOp, ast.Compare, ast.Call,
    ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Div, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

# Keeps `10**9`-style constants cheap while refusing absurd powers.
_MAX_EXPONENT = 64


def parse_expr(source: Any) -> ast.expr:
    """Parse a bound or constraint (a number or an expression string) into an AST."""
    if isinstance(source, bool):
        raise ExprError(f"expected a number or expression, got boolean {source!r}")
    if isinstance(source, (int, float)):
        source = repr(_as_int(source))
    if not isinstance(source, str) or not source.strip():
        raise ExprError(f"expected a number or expression, got {source!r}")

    text = source.strip()
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"cannot parse expression {text!r}: {exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExprError(
                f"unsupported syntax {type(node).__name__} in expression {text!r}"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise ExprError(
                    f"unsupported function call in {text!r}; allowed: {sorted(FUNCTIONS)}"
                )
            if node.keywords:
                raise ExprError(f"keyword arguments are not allowed in {text!r}")
        if isinstance(node, ast.Constant):
            _as_int(node.value)  # validates the literal
    return tree.body


def names_in(node: ast.AST, *, include_sum_args: bool = True) -> Set[str]:
    """Variable names referenced by an expression (function names excluded)."""
    found: Set[str] = set()

    def visit(n: ast.AST) -> None:
        if isinstance(n, ast.Call):
            if not include_sum_args and n.func.id == "sum":  # type: ignore[attr-defined]
                return
            for arg in n.args:
                visit(arg)
            return
        if isinstance(n, ast.Name):
            found.add(n.id)
            return
        for child in ast.iter_child_nodes(n):
            visit(child)

    visit(node)
    return found


def sum_args(node: ast.AST) -> list[ast.expr]:
    """The argument expressions of every ``sum(...)`` call inside `node`."""
    return [
        n.args[0]
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and n.func.id == "sum" and n.args  # type: ignore[attr-defined]
    ]


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ExprError(f"only integer literals are supported, got {value!r}")


def _is_z3(value: Any) -> bool:
    return isinstance(value, z3.ExprRef)


def _and(values: list) -> Any:
    if any(_is_z3(v) for v in values):
        return z3.And(*values)
    return all(values)


def _or(values: list) -> Any:
    if any(_is_z3(v) for v in values):
        return z3.Or(*values)
    return any(values)


def _min(values: list) -> Any:
    result = values[0]
    for v in values[1:]:
        result = z3.If(v < result, v, result) if _is_z3(v) or _is_z3(result) else min(result, v)
    return result


def _max(values: list) -> Any:
    result = values[0]
    for v in values[1:]:
        result = z3.If(v > result, v, result) if _is_z3(v) or _is_z3(result) else max(result, v)
    return result


_COMPARE: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}


def evaluate(
    node: ast.AST,
    env: Mapping[str, Any],
    sum_fn: Optional[Callable[[ast.expr], Any]] = None,
) -> Any:
    """
    Evaluate an expression AST.

    `env` maps names to ints or Z3 expressions. `sum_fn` implements
    ``sum(expr)`` (a sum over all test cases in the file); without it,
    ``sum`` is an error.
    """
    if isinstance(node, ast.Constant):
        return _as_int(node.value)

    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ExprError(f"unknown variable '{node.id}'")
        return env[node.id]

    if isinstance(node, ast.UnaryOp):
        operand = evaluate(node.operand, env, sum_fn)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        return z3.Not(operand) if _is_z3(operand) else not operand

    if isinstance(node, ast.BinOp):
        left = evaluate(node.left, env, sum_fn)
        right = evaluate(node.right, env, sum_fn)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, (ast.FloorDiv, ast.Div)):
            if _is_z3(left) or _is_z3(right):
                return left / right  # integer division for Z3 Int sorts
            if right == 0:
                raise ExprError("division by zero")
            return left // right
        if isinstance(op, ast.Mod):
            if not (_is_z3(left) or _is_z3(right)) and right == 0:
                raise ExprError("modulo by zero")
            return left % right
        if isinstance(op, ast.Pow):
            if _is_z3(left) or _is_z3(right):
                raise ExprError("'**' is only supported between constants (e.g. 10**9)")
            if right < 0 or right > _MAX_EXPONENT:
                raise ExprError(f"exponent {right} is out of the supported range")
            return left ** right
        raise ExprError(f"unsupported operator {type(op).__name__}")

    if isinstance(node, ast.BoolOp):
        values = [evaluate(v, env, sum_fn) for v in node.values]
        return _and(values) if isinstance(node.op, ast.And) else _or(values)

    if isinstance(node, ast.Compare):
        left = evaluate(node.left, env, sum_fn)
        parts = []
        for op, comparator in zip(node.ops, node.comparators):
            right = evaluate(comparator, env, sum_fn)
            parts.append(_COMPARE[type(op)](left, right))
            left = right
        return parts[0] if len(parts) == 1 else _and(parts)

    if isinstance(node, ast.Call):
        name = node.func.id  # type: ignore[attr-defined]
        if name == "sum":
            if sum_fn is None:
                raise ExprError("sum(...) is only allowed in global_constraints")
            if len(node.args) != 1:
                raise ExprError("sum(...) takes exactly one argument")
            return sum_fn(node.args[0])
        args = [evaluate(a, env, sum_fn) for a in node.args]
        if not args:
            raise ExprError(f"{name}() needs at least one argument")
        if name == "abs":
            if len(args) != 1:
                raise ExprError("abs() takes exactly one argument")
            x = args[0]
            return z3.If(x < 0, -x, x) if _is_z3(x) else abs(x)
        return _min(args) if name == "min" else _max(args)

    raise ExprError(f"unsupported syntax {type(node).__name__}")


def eval_int(node: ast.AST, env: Mapping[str, Any], what: str = "expression") -> int:
    """Evaluate to a concrete Python int, with a readable error otherwise."""
    value = evaluate(node, env)
    if _is_z3(value) or isinstance(value, bool) or not isinstance(value, int):
        raise ExprError(f"{what} did not evaluate to an integer")
    return value


def source_of(node: ast.AST) -> str:
    """Round-trip an AST back to readable source for messages."""
    return ast.unparse(node)
