"""
autosetter.testgen.builders
===========================
Constructive builders for everything that is not a scalar integer.

Z3 decides the sizes (n, m, ...); these builders fill in arrays, strings,
permutations, matrices, rows, trees and graphs of those sizes directly.
Encoding 2*10^5 array elements as solver variables would not finish, while
building them here is instant -- and every result is re-checked against the
spec afterwards anyway.

`mode` is the per-seed strategy (see `engine.PLAN`) and picks the shape:
all-minimum, all-maximum, sorted, star tree, path tree, and so on.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Tuple

from autosetter.testgen.expr import eval_int
from autosetter.testgen.spec import Variable

MODES = ("min", "max", "random", "small", "near_max", "log")

# Refuse inputs so large that building or writing them would stall the pipeline.
MAX_ITEMS = 5_000_000


class TestGenError(Exception):
    """Raised when a test cannot be generated from an otherwise valid spec."""


def pick_int(lo: int, hi: int, mode: str, rng: random.Random) -> int:
    """Choose an integer in [lo, hi] according to `mode`."""
    if lo > hi:
        raise TestGenError(f"empty range [{lo}, {hi}]")
    if mode == "min":
        return lo
    if mode == "max":
        return hi
    if mode == "small":
        return rng.randint(lo, min(hi, lo + 9))
    if mode == "near_max":
        return rng.randint(max(lo, hi - max(9, (hi - lo) // 20)), hi)
    if mode == "log":
        span = hi - lo
        offset = int(round(math.exp(rng.uniform(0, math.log(span + 1))))) - 1
        return lo + min(max(offset, 0), span)
    return rng.randint(lo, hi)


def mode_for(strategy: str, rng: random.Random) -> str:
    """Per-item mode: fixed strategies stay fixed, 'random' mixes all of them."""
    if strategy == "random":
        return rng.choice(("random", "random", "log", "small", "near_max", "min", "max"))
    return strategy


def _size(var: Variable, key: str, env: Dict[str, Any]) -> int:
    value = eval_int(var.exprs[key], env, f"{var.name}.{key}")
    if value < 0:
        raise TestGenError(f"{var.name}.{key} evaluated to {value}, which is negative")
    if value > MAX_ITEMS:
        raise TestGenError(f"{var.name}.{key} = {value} exceeds the generator limit {MAX_ITEMS}")
    return value


def _bounds(var: Variable, env: Dict[str, Any]) -> Tuple[int, int]:
    lo = eval_int(var.exprs["min"], env, f"{var.name}.min")
    hi = eval_int(var.exprs["max"], env, f"{var.name}.max")
    if lo > hi:
        raise TestGenError(f"{var.name}: min {lo} is greater than max {hi}")
    return lo, hi


# ---------------------------------------------------------------------------
# Arrays, permutations, strings, matrices, rows
# ---------------------------------------------------------------------------

def _int_list(n: int, lo: int, hi: int, distinct: bool, strategy: str, rng: random.Random) -> List[int]:
    if distinct and hi - lo + 1 < n:
        raise TestGenError(f"cannot pick {n} distinct values from [{lo}, {hi}]")

    if strategy == "min":
        pattern = "all_min"
    elif strategy == "max":
        pattern = "all_max"
    elif strategy == "near_max":
        pattern = "random_high"
    elif strategy == "small":
        pattern = "random_small"
    elif strategy == "log":
        pattern = "random"
    else:
        pattern = rng.choice((
            "random", "random", "equal", "sorted", "reversed", "extremes",
            "few_distinct", "random_high", "random_small",
        ))

    if distinct:
        if pattern == "all_min":
            return list(range(lo, lo + n))
        if pattern == "all_max":
            return list(range(hi, hi - n, -1))
        if pattern in ("random_small", "random_high"):
            width = min(hi - lo + 1, max(2 * n, n + 10))
            start = lo if pattern == "random_small" else hi - width + 1
            values = rng.sample(range(start, start + width), n)
        else:
            values = rng.sample(range(lo, hi + 1), n)
        if pattern == "sorted":
            values.sort()
        elif pattern == "reversed":
            values.sort(reverse=True)
        return values

    if pattern == "all_min":
        return [lo] * n
    if pattern == "all_max":
        return [hi] * n
    if pattern == "equal":
        return [rng.randint(lo, hi)] * n
    if pattern == "extremes":
        return [rng.choice((lo, hi)) for _ in range(n)]
    if pattern == "few_distinct":
        pool = [rng.randint(lo, hi) for _ in range(rng.randint(2, 3))]
        return [rng.choice(pool) for _ in range(n)]
    if pattern == "random_small":
        top = min(hi, lo + 9)
        return [rng.randint(lo, top) for _ in range(n)]
    if pattern == "random_high":
        bottom = max(lo, hi - max(9, (hi - lo) // 20))
        return [rng.randint(bottom, hi) for _ in range(n)]
    values = [rng.randint(lo, hi) for _ in range(n)]
    if pattern == "sorted":
        values.sort()
    elif pattern == "reversed":
        values.sort(reverse=True)
    return values


def build_array(var: Variable, env: Dict[str, Any], strategy: str, rng: random.Random) -> List[int]:
    n = _size(var, "length", env)
    lo, hi = _bounds(var, env)
    order = var.opt("order")
    distinct = bool(var.opt("distinct")) or order in ("ascending", "descending")
    values = _int_list(n, lo, hi, distinct, strategy, rng)
    if order in ("ascending", "non_decreasing"):
        values.sort()
    elif order in ("descending", "non_increasing"):
        values.sort(reverse=True)
    return values


def build_permutation(var: Variable, env: Dict[str, Any], strategy: str, rng: random.Random) -> List[int]:
    n = _size(var, "length", env)
    values = list(range(var.indexing, var.indexing + n))
    shape = {"min": "identity", "max": "reversed"}.get(strategy) or rng.choice(
        ("random", "random", "random", "identity", "reversed", "rotated")
    )
    if shape == "reversed":
        values.reverse()
    elif shape == "rotated" and n:
        k = rng.randrange(n)
        values = values[k:] + values[:k]
    elif shape == "random":
        rng.shuffle(values)
    return values


def _string(length: int, alphabet: str, strategy: str, rng: random.Random) -> str:
    if strategy == "min":
        return alphabet[0] * length
    if strategy == "max":
        return alphabet[-1] * length
    if strategy == "small":
        shape = "few"
    elif strategy in ("near_max", "log"):
        shape = "random"
    else:
        shape = rng.choice(("random", "random", "same", "alternating", "few", "palindrome", "blocks"))

    if shape == "same":
        return rng.choice(alphabet) * length
    if shape == "alternating":
        a, b = rng.choice(alphabet), rng.choice(alphabet)
        return "".join(a if i % 2 == 0 else b for i in range(length))
    if shape == "few":
        pool = rng.sample(alphabet, min(2, len(alphabet)))
        return "".join(rng.choice(pool) for _ in range(length))
    if shape == "palindrome":
        half = "".join(rng.choice(alphabet) for _ in range((length + 1) // 2))
        return (half + half[::-1][length % 2:])[:length]
    if shape == "blocks":
        out: List[str] = []
        while len(out) < length:
            out.extend(rng.choice(alphabet) * rng.randint(1, max(1, length // 4)))
        return "".join(out[:length])
    return "".join(rng.choice(alphabet) for _ in range(length))


def build_string(var: Variable, env: Dict[str, Any], strategy: str, rng: random.Random) -> str:
    return _string(_size(var, "length", env), var.alphabet, strategy, rng)


def build_matrix(var: Variable, env: Dict[str, Any], strategy: str, rng: random.Random) -> List[Any]:
    rows, cols = _size(var, "rows", env), _size(var, "cols", env)
    if rows * cols > MAX_ITEMS:
        raise TestGenError(f"{var.name}: {rows}x{cols} exceeds the generator limit {MAX_ITEMS}")
    if var.alphabet:
        return [_string(cols, var.alphabet, strategy, rng) for _ in range(rows)]
    lo, hi = _bounds(var, env)
    return [_int_list(cols, lo, hi, False, strategy, rng) for _ in range(rows)]


def build_rows(var: Variable, env: Dict[str, Any], strategy: str, rng: random.Random) -> List[Tuple[int, ...]]:
    count = _size(var, "count", env)
    rows: List[Tuple[int, ...]] = []
    for _ in range(count):
        row_env = dict(env)
        values = []
        mode = mode_for(strategy, rng)
        for f in var.fields:
            lo = eval_int(f.min, row_env, f"{var.name}.{f.name}.min")
            hi = eval_int(f.max, row_env, f"{var.name}.{f.name}.max")
            if lo > hi:
                raise TestGenError(
                    f"{var.name}: field '{f.name}' has empty range [{lo}, {hi}] "
                    f"(earlier fields in this row: {values})"
                )
            value = pick_int(lo, hi, mode, rng)
            row_env[f.name] = value
            values.append(value)
        rows.append(tuple(values))
    return rows


# ---------------------------------------------------------------------------
# Trees and graphs
# ---------------------------------------------------------------------------

TREE_SHAPES = ("random", "path", "star", "binary", "caterpillar", "deep_random")


def _tree_shape(strategy: str, rng: random.Random) -> str:
    return {"min": "path", "max": "star", "near_max": "deep_random", "small": "binary"}.get(
        strategy
    ) or rng.choice(TREE_SHAPES)


def _parents(n: int, shape: str, rng: random.Random) -> List[int]:
    """parent[i] for nodes 2..n (1-based), always with parent[i] < i."""
    parents: List[int] = []
    spine = max(1, n // 2)
    for i in range(2, n + 1):
        if shape == "path":
            p = i - 1
        elif shape == "star":
            p = 1
        elif shape == "binary":
            p = i // 2
        elif shape == "caterpillar":
            p = i - 1 if i <= spine else rng.randint(1, spine)
        elif shape == "deep_random":
            p = rng.randint(max(1, i - 3), i - 1)
        else:
            p = rng.randint(1, i - 1)
        parents.append(p)
    return parents


def _weights(var: Variable, env: Dict[str, Any], count: int, strategy: str, rng: random.Random) -> List[int]:
    if var.weight is None:
        return []
    lo = eval_int(var.weight["min"], env, f"{var.name}.weight.min")
    hi = eval_int(var.weight["max"], env, f"{var.name}.weight.max")
    return [pick_int(lo, hi, mode_for(strategy, rng), rng) for _ in range(count)]


def _attach_weights(edges: List[Tuple[int, int]], weights: List[int]) -> List[Tuple[int, ...]]:
    if not weights:
        return list(edges)
    return [edge + (w,) for edge, w in zip(edges, weights)]


def build_tree(var: Variable, env: Dict[str, Any], strategy: str, rng: random.Random) -> List[Any]:
    n = _size(var, "nodes", env)
    shape = _tree_shape(strategy, rng)
    parents = _parents(n, shape, rng)
    base = var.indexing - 1  # shift from 1-based construction labels

    if var.opt("format", "edges") == "parents":
        return [p + base for p in parents]

    labels = list(range(1, n + 1))
    rng.shuffle(labels)
    edges: List[Tuple[int, int]] = []
    for child, parent in enumerate(parents, start=2):
        u, v = labels[child - 1] + base, labels[parent - 1] + base
        edges.append((u, v) if rng.random() < 0.5 else (v, u))
    rng.shuffle(edges)
    return _attach_weights(edges, _weights(var, env, len(edges), strategy, rng))


def graph_capacity(n: int, directed: bool, self_loops: bool) -> int:
    pairs = n * (n - 1) if directed else n * (n - 1) // 2
    return pairs + (n if self_loops else 0)


def build_graph(var: Variable, env: Dict[str, Any], strategy: str, rng: random.Random) -> List[Any]:
    n, m = _size(var, "nodes", env), _size(var, "edges", env)
    directed = bool(var.opt("directed", False))
    connected = bool(var.opt("connected", False))
    loops = bool(var.opt("self_loops", False))
    multi = bool(var.opt("multi_edges", False))
    base = var.indexing - 1

    if m and n == 0:
        raise TestGenError(f"{var.name}: {m} edges requested on a graph with 0 nodes")
    capacity = graph_capacity(n, directed, loops)
    if not multi and m > capacity:
        raise TestGenError(f"{var.name}: {m} edges exceed the {capacity} possible without multi-edges")
    if not multi and not loops and n == 1 and m:
        raise TestGenError(f"{var.name}: a single node cannot have edges without self-loops")
    if connected and n > 0 and m < n - 1:
        raise TestGenError(f"{var.name}: a connected graph on {n} nodes needs at least {n - 1} edges, got {m}")

    def key(u: int, v: int) -> Tuple[int, int]:
        return (u, v) if directed else (min(u, v), max(u, v))

    edges: List[Tuple[int, int]] = []
    seen = set()

    def add(u: int, v: int) -> None:
        seen.add(key(u, v))
        if not directed and rng.random() < 0.5:
            u, v = v, u
        edges.append((u, v))

    if connected and n > 1:
        labels = list(range(1, n + 1))
        rng.shuffle(labels)
        for child, parent in enumerate(_parents(n, _tree_shape(strategy, rng), rng), start=2):
            add(labels[child - 1], labels[parent - 1])

    remaining = m - len(edges)
    if remaining > 0 and not multi and remaining > (capacity - len(seen)) // 2 and capacity <= 3_000_000:
        # Dense: enumerate the free pairs rather than rejection-sample them.
        free = [
            (u, v)
            for u in range(1, n + 1)
            for v in (range(1, n + 1) if directed else range(u, n + 1))
            if (u != v or loops) and key(u, v) not in seen
        ]
        for u, v in rng.sample(free, remaining):
            add(u, v)
    else:
        while len(edges) < m:
            u, v = rng.randint(1, n), rng.randint(1, n)
            if u == v and not loops:
                continue
            if not multi and key(u, v) in seen:
                continue
            add(u, v)

    rng.shuffle(edges)
    shifted = [(u + base, v + base) for u, v in edges]
    return _attach_weights(shifted, _weights(var, env, len(shifted), strategy, rng))


BUILDERS = {
    "array": build_array,
    "permutation": build_permutation,
    "string": build_string,
    "matrix": build_matrix,
    "rows": build_rows,
    "tree": build_tree,
    "graph": build_graph,
}
