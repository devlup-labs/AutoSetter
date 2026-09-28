"""
Unit tests for autosetter.testgen (Z3 spec-driven test generation).
"""

from __future__ import annotations

import pytest

from autosetter.testgen import (
    SpecError,
    TestGenError,
    check_input,
    check_samples,
    generate_test,
    load_spec,
)
from autosetter.testgen.expr import ExprError, parse_expr
from autosetter.testgen.layout import parse_input

MULTI_SPEC = {
    "multi_test": {"count": "t", "min": 1, "max": 10000},
    "variables": [
        {"name": "n", "type": "int", "min": 1, "max": 200000},
        {"name": "k", "type": "int", "min": 1, "max": "n"},
        {"name": "a", "type": "array", "length": "n", "min": 1, "max": 1000000000},
    ],
    "constraints": ["k <= n"],
    "global_constraints": ["sum(n) <= 200000"],
    "layout": [["n", "k"], ["a"]],
}

STRUCTURES_SPEC = {
    "variables": [
        {"name": "n", "type": "int", "min": 2, "max": 300},
        {"name": "m", "type": "int", "min": "n - 1", "max": "min(n*(n-1)//2, 2000)"},
        {"name": "g", "type": "graph", "nodes": "n", "edges": "m", "connected": True,
         "weight": {"min": 1, "max": 100}},
        {"name": "tr", "type": "tree", "nodes": "n"},
        {"name": "par", "type": "tree", "nodes": "n", "format": "parents"},
        {"name": "p", "type": "permutation", "length": "n"},
        {"name": "s", "type": "string", "length": "n", "alphabet": "a-c"},
        {"name": "b", "type": "array", "length": "n", "min": -5, "max": 1000,
         "distinct": True, "order": "ascending"},
        {"name": "grid", "type": "matrix", "rows": 3, "cols": "n", "alphabet": ".#"},
        {"name": "q", "type": "int", "min": 1, "max": 50},
        {"name": "queries", "type": "rows", "count": "q",
         "fields": [{"name": "l", "min": 1, "max": "n"}, {"name": "r", "min": "l", "max": "n"}]},
    ],
    "layout": [["n", "m"], ["g"], ["tr"], ["par"], ["p"], ["s"], ["b"], ["grid"], ["q"], ["queries"]],
}


def test_expressions_reject_anything_but_arithmetic():
    for unsafe in ("__import__('os')", "n.real", "[n]", "open('x')", "lambda: 1"):
        with pytest.raises(ExprError):
            parse_expr(unsafe)
    parse_expr("min(n*(n-1)//2, 10**5) + abs(-k)")


def test_spec_errors_are_all_reported_together():
    with pytest.raises(SpecError) as excinfo:
        load_spec({
            "variables": [
                {"name": "a", "type": "array", "length": "n", "min": 1, "max": 5},
                {"name": "n", "type": "int", "min": 1},
                {"name": "x", "type": "blob"},
            ],
            "layout": [["a", "n"], ["ghost"]],
        })
    message = str(excinfo.value)
    assert "variable 'a'.length" in message   # n is declared after a
    assert "missing 'max'" in message
    assert "type must be one of" in message
    assert "'ghost' is not a declared variable" in message


def test_size_must_be_printed_before_the_value_it_sizes():
    with pytest.raises(SpecError, match="must be printed earlier"):
        load_spec({
            "variables": [
                {"name": "n", "type": "int", "min": 1, "max": 5},
                {"name": "a", "type": "array", "length": "n", "min": 1, "max": 5},
            ],
            "layout": [["a"], ["n"]],
        })


def test_global_constraints_need_sum():
    with pytest.raises(SpecError, match=r"wrapped in sum"):
        load_spec({**MULTI_SPEC, "global_constraints": ["n <= 200000"]})


@pytest.mark.parametrize("seed", range(1, 11))
def test_multi_test_output_satisfies_every_constraint(seed):
    spec = load_spec(MULTI_SPEC)
    test = generate_test(spec, seed)

    assert check_input(spec, test.text) == []
    top, cases = parse_input(spec, test.text)
    assert top["t"] == len(cases) == test.num_cases
    assert sum(c["n"] for c in cases) <= 200000
    assert all(1 <= c["k"] <= c["n"] for c in cases)


def test_generation_is_deterministic_per_seed_and_varies_across_seeds():
    spec = load_spec(MULTI_SPEC)
    assert generate_test(spec, 3).text == generate_test(spec, 3).text
    texts = {generate_test(spec, seed).text for seed in range(1, 13)}
    assert len(texts) == 12


def test_min_and_max_strategies_hit_the_bounds():
    spec = load_spec({
        "variables": [
            {"name": "n", "type": "int", "min": 3, "max": 1000},
            {"name": "k", "type": "int", "min": 1, "max": "n"},
        ],
        "layout": [["n", "k"]],
    })
    assert generate_test(spec, 1).text == "3 1\n"        # strategy "min"
    assert generate_test(spec, 2).text == "1000 1000\n"  # strategy "max"


def test_values_respect_constraints_with_holes():
    spec = load_spec({
        "variables": [{"name": "n", "type": "int", "min": 1, "max": 1000}],
        "constraints": ["n % 7 == 3"],
        "layout": [["n"]],
    })
    for seed in range(1, 15):
        assert int(generate_test(spec, seed).text) % 7 == 3


@pytest.mark.parametrize("seed", range(1, 11))
def test_structures_are_valid(seed):
    spec = load_spec(STRUCTURES_SPEC)
    assert check_input(spec, generate_test(spec, seed).text) == []


def test_unsatisfiable_constraints_raise():
    spec = load_spec({
        "variables": [{"name": "n", "type": "int", "min": 1, "max": 5}],
        "constraints": ["n > 10"],
        "layout": [["n"]],
    })
    with pytest.raises(TestGenError, match="unsatisfiable"):
        generate_test(spec, 1)


def test_impossible_distinct_array_raises():
    spec = load_spec({
        "variables": [
            {"name": "n", "type": "int", "min": 10, "max": 10},
            {"name": "a", "type": "array", "length": "n", "min": 1, "max": 3, "distinct": True},
        ],
        "layout": [["n"], ["a"]],
    })
    with pytest.raises(TestGenError, match="distinct"):
        generate_test(spec, 1)


def test_samples_are_checked_against_the_spec():
    spec = load_spec(MULTI_SPEC)
    good = {"input": "2\n3 2\n1 2 3\n1 1\n7\n"}
    out_of_range = {"input": "1\n3 5\n1 2 3\n"}       # k > n
    short = {"input": "1\n3 1\n1 2\n"}                 # a has 2 of 3 elements
    extra = {"input": "1\n1 1\n5 6\n"}                 # a trailing token

    assert check_samples(spec, [good]) == []
    problems = check_samples(spec, [good, out_of_range, short, extra])
    assert any(p.startswith("sample 2:") and "k = 5" in p for p in problems)
    assert any(p.startswith("sample 3:") and "input ended" in p for p in problems)
    assert any(p.startswith("sample 4:") and "unread token" in p for p in problems)
