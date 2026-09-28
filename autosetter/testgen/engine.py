"""
autosetter.testgen.engine
=========================
Z3-driven test generation from a `TestSpec`.

For each seed:
1. Pick a strategy from `PLAN` (minimum values, maximum values, random, ...).
2. Build a Z3 model of every integer variable in every test case of the file:
   its bounds, the per-test constraints and the cross-test constraints such
   as ``sum(n) <= 200000``.
3. Fix the integers one at a time. For each, Z3 reports the smallest and
   largest value still feasible given the choices so far; the strategy picks
   within that range. Choosing in this order means relations like
   ``k <= n`` and file-wide sums always stay satisfiable, and different seeds
   give genuinely different values (Z3's own models tend to sit on bounds).
4. Build arrays, strings, trees, ... from the fixed integers (`builders`).
5. Render the input, parse it back and re-check it against the spec.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import z3

from autosetter.config import Z3_MAX_CASES, Z3_TIMEOUT_MS
from autosetter.testgen.builders import BUILDERS, TestGenError, mode_for, pick_int
from autosetter.testgen.expr import ExprError, eval_int, evaluate
from autosetter.testgen.layout import LayoutError, check, parse_input, render
from autosetter.testgen.spec import TestSpec

# Strategy per seed. Seed 1 is the smallest test, seed 2 the largest.
PLAN = (
    "min", "max", "random", "small", "near_max",
    "random", "log", "near_max", "random", "random",
)

# "min" and "max" are deterministic, so repeating them would duplicate a test;
# later passes through PLAN use a randomised neighbour instead.
_REPEAT = {"min": "small", "max": "near_max"}


@dataclass
class GeneratedTest:
    text: str
    strategy: str
    num_cases: int


def strategy_for(seed: int) -> str:
    index = max(abs(seed), 1) - 1
    strategy = PLAN[index % len(PLAN)]
    return _REPEAT.get(strategy, strategy) if index >= len(PLAN) else strategy


def _as_constraint(value: Any) -> Any:
    return z3.BoolVal(value) if isinstance(value, bool) else value


def _extreme(opt: z3.Optimize, var: z3.ArithRef, minimize: bool) -> int:
    opt.push()
    if minimize:
        opt.minimize(var)
    else:
        opt.maximize(var)
    result = opt.check()
    if result != z3.sat:
        opt.pop()
        raise TestGenError(
            f"Z3 could not {'minimise' if minimize else 'maximise'} {var} ({result}); "
            "the constraints may be too complex or the solver timed out"
        )
    value = opt.model().eval(var, model_completion=True).as_long()
    opt.pop()
    return value


def _choose(opt: z3.Optimize, var: z3.ArithRef, mode: str, rng: random.Random) -> int:
    if mode in ("min", "max"):
        return _extreme(opt, var, minimize=(mode == "min"))

    lo = _extreme(opt, var, minimize=True)
    hi = _extreme(opt, var, minimize=False)
    target = pick_int(lo, hi, mode, rng)
    if target in (lo, hi):
        return target

    opt.push()
    opt.add(var == target)
    feasible = opt.check() == z3.sat
    opt.pop()
    if feasible:
        return target

    # The feasible set has holes (e.g. "n is even"): take the closest value
    # below the target, which exists because `lo` is feasible.
    opt.push()
    opt.add(var <= target)
    value = _extreme(opt, var, minimize=False)
    opt.pop()
    return value


def _solve_for_count(
    spec: TestSpec,
    num_cases: int,
    strategy: str,
    rng: random.Random,
    timeout_ms: int,
) -> Optional[List[Dict[str, int]]]:
    """Fix every integer of `num_cases` test cases; None if infeasible."""
    opt = z3.Optimize()
    opt.set("timeout", timeout_ms)

    top: Dict[str, Any] = {spec.multi.count: num_cases} if spec.multi else {}
    case_envs: List[Dict[str, Any]] = []
    for index in range(num_cases):
        env = dict(top)
        for var in spec.variables:
            if var.type != "int":
                continue
            symbol = z3.Int(f"{var.name}#{index}")
            opt.add(_as_constraint(symbol >= evaluate(var.exprs["min"], env)))
            opt.add(_as_constraint(symbol <= evaluate(var.exprs["max"], env)))
            env[var.name] = symbol
        for node in spec.constraints:
            opt.add(_as_constraint(evaluate(node, env)))
        case_envs.append(env)

    def sum_fn(arg: Any) -> Any:
        return sum((evaluate(arg, env) for env in case_envs), 0)

    for node in spec.global_constraints:
        opt.add(_as_constraint(evaluate(node, top, sum_fn)))

    result = opt.check()
    if result == z3.unsat:
        return None
    if result != z3.sat:
        raise TestGenError(f"Z3 returned '{result}' (timeout {timeout_ms} ms) for {num_cases} test case(s)")

    solved: List[Dict[str, int]] = []
    for env in case_envs:
        values: Dict[str, int] = {}
        for name in spec.int_names:
            symbol = env[name]
            value = _choose(opt, symbol, mode_for(strategy, rng), rng)
            opt.add(symbol == value)
            values[name] = value
        solved.append(values)
    return solved


def solve_integers(
    spec: TestSpec,
    strategy: str,
    rng: random.Random,
    timeout_ms: int = Z3_TIMEOUT_MS,
    max_cases: int = Z3_MAX_CASES,
) -> tuple[Dict[str, int], List[Dict[str, int]]]:
    """Choose the number of test cases and every integer in each of them."""
    if not spec.multi:
        solved = _solve_for_count(spec, 1, strategy, rng, timeout_ms)
        if solved is None:
            raise TestGenError("the integer constraints are unsatisfiable")
        return {}, solved

    lo = eval_int(spec.multi.min, {}, "multi_test.min")
    hi = eval_int(spec.multi.max, {}, "multi_test.max")
    if lo > hi:
        raise TestGenError(f"multi_test.min {lo} is greater than multi_test.max {hi}")
    # Every test case adds solver variables, so cap the count per file.
    cap = min(hi, max(lo, max_cases))
    count = pick_int(lo, cap, "small" if strategy == "log" else mode_for(strategy, rng), rng)

    # Too many test cases can make a file-wide sum infeasible; back off.
    while True:
        solved = _solve_for_count(spec, count, strategy, rng, timeout_ms)
        if solved is not None:
            return {spec.multi.count: count}, solved
        if count == lo:
            raise TestGenError("the integer constraints are unsatisfiable")
        count = max(lo, count // 2)


def generate_test(
    spec: TestSpec,
    seed: int,
    timeout_ms: int = Z3_TIMEOUT_MS,
    max_cases: int = Z3_MAX_CASES,
) -> GeneratedTest:
    """Generate one input file for `seed`. Deterministic for a given spec and seed."""
    rng = random.Random(seed)
    strategy = strategy_for(seed)

    try:
        top, int_cases = solve_integers(spec, strategy, rng, timeout_ms, max_cases)
        cases: List[Dict[str, Any]] = []
        for ints in int_cases:
            env: Dict[str, Any] = dict(top)
            env.update(ints)
            for var in spec.variables:
                if var.type != "int":
                    env[var.name] = BUILDERS[var.type](var, env, strategy, rng)
            cases.append(env)
    except ExprError as exc:
        raise TestGenError(str(exc)) from exc

    text = render(spec, top, cases)

    try:
        parsed_top, parsed_cases = parse_input(spec, text)
    except LayoutError as exc:
        raise TestGenError(f"generated input does not match the layout: {exc}") from exc
    problems = check(spec, parsed_top, parsed_cases)
    if problems:
        raise TestGenError("generated input breaks the spec:\n" + "\n".join(problems[:10]))

    return GeneratedTest(text=text, strategy=strategy, num_cases=len(cases))
