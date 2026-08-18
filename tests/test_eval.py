"""Tests for the scoring, and for the prompt the model actually receives.

The prompt tests are worth having because the prompt is generated rather than
written out. A description of the IR that has quietly stopped matching the IR
is the exact failure this design was meant to rule out, so it is checked rather
than assumed.
"""

from __future__ import annotations

import json

import pytest

from testgen import prompt as prompts
from testgen.eval import Agreement, compare, pairs
from testgen.ir.problems import load
from testgen.ir.schema import Problem

# --- the generated prompt ------------------------------------------------


def test_the_prompt_carries_the_real_schema():
    """Every variable kind in the code has to reach the model."""
    text = prompts.build({"title": "x"})
    for kind in ("int", "array", "string", "sum_over_tests"):
        assert f'"{kind}"' in text


def test_supported_kinds_comes_from_the_schema_not_a_list():
    """Kinds of input value only. See the separate constraints test below."""
    assert prompts.supported_kinds() == ["array", "int", "string"]


def test_the_prompt_names_the_origin_field():
    """Marking a chosen bound is only possible if the model is told to."""
    text = prompts.build({"title": "x"})
    assert "origin" in text and "chosen" in text


def test_the_prompt_includes_worked_examples_from_the_gold_files():
    text = prompts.build({"title": "x"})
    assert "Watermelon" in text
    assert "The Best Card" in text


def test_pruning_drops_titles_but_keeps_descriptions():
    schema = prompts.schema_text()
    assert '"title"' not in schema
    assert '"description"' in schema


def test_the_statement_is_in_the_prompt():
    text = prompts.build({"title": "Some Problem", "constraints": "1 <= n <= 5"})
    assert "Some Problem" in text
    assert "1 <= n <= 5" in text


def test_the_statement_is_rendered_as_prose_not_json():
    """A JSON statement in the prompt is an object shaped like the answer.

    The 4B model copied it back every single attempt, whatever the wording of
    the instructions, until the statement stopped being JSON. This is the fix,
    so it is pinned.
    """
    rendered = prompts.statement_text(
        {"title": "T", "story": "S", "constraints": "1 <= n <= 5"}
    )
    assert "{" not in rendered and "}" not in rendered
    assert "TITLE" in rendered and "T" in rendered
    assert "1 <= n <= 5" in rendered


def test_a_field_the_statement_omits_is_shown_as_absent():
    """Silence and emptiness must not look the same to the model."""
    rendered = prompts.statement_text({"title": "T"})
    assert "(the statement does not say)" in rendered


def test_samples_are_rendered_without_json_either():
    rendered = prompts.statement_text(
        {"samples": [{"input": "5\n2", "output": "YES"}]}
    )
    assert "SAMPLE 1 INPUT" in rendered
    assert "{" not in rendered


def test_repair_prompts_quote_the_reason():
    schema_repair = prompts.repair_schema('{"a":1}', "n: field required")
    assert "n: field required" in schema_repair

    sample_repair = prompts.repair_samples('{"a":1}', "8\n", "w is out of range")
    assert "w is out of range" in sample_repair
    assert "8" in sample_repair


def test_an_unknown_name_error_is_told_how_to_be_fixed():
    """The failure seen in the first real run: the error names the symptom.

    Handed only "length of a refers to unknown variable 'n'", the model sent
    the same object back three times, because from its point of view nothing
    was wrong with saying the array has length n. The repair has to say which
    two edits are acceptable.
    """
    text = prompts.repair_schema(
        "{}", "Value error, length of a refers to unknown variable 'n'"
    )
    assert "HOW TO FIX THIS" in text
    assert "ADD the missing variable" in text


def test_multitest_errors_carry_advice_too():
    assert "HOW TO FIX" in prompts.repair_schema(
        "{}", "a multitest problem must declare test_count"
    )
    assert "HOW TO FIX" in prompts.repair_schema(
        "{}", "test_count is only meaningful when multitest is set"
    )


def test_an_unrecognised_error_adds_no_advice():
    """Better to say nothing than to give guidance for the wrong problem."""
    assert prompts.advice_for("some error nobody has seen before") == ""


def _error_from(ir: dict) -> str:
    """The message an IR really produces, from the schema or the emitter."""
    from testgen.emit.validator import emit_validator

    try:
        problem = Problem.model_validate(ir)
    except Exception as exc:
        return str(exc)

    try:
        emit_validator(problem)
    except Exception as exc:
        return str(exc)

    raise AssertionError(f"expected {ir} to be rejected, but it was accepted")


# One genuinely broken IR per piece of advice, so the guidance is checked
# against the error the code really raises rather than against a string copied
# out of it. A marker that stops matching shows up here as a failure.
BROKEN = {
    "unknown variable": {
        "name": "x",
        "body": {
            "variables": [
                {"kind": "array", "name": "a", "length": "n",
                 "elem": {"lo": 1, "hi": 10}}
            ],
            "lines": [{"tokens": ["a"]}],
        },
    },
    "must declare test_count": {
        "name": "x",
        "multitest": True,
        "body": {"variables": [], "lines": []},
    },
    "test_count is only meaningful": {
        "name": "x",
        "multitest": False,
        "test_count": {"kind": "int", "name": "t", "domain": {"lo": 1, "hi": 5}},
        "body": {"variables": [], "lines": []},
    },
    "global constraints sum across test cases": {
        "name": "x",
        "multitest": False,
        "body": {
            "variables": [
                {"kind": "int", "name": "n", "domain": {"lo": 1, "hi": 5}}
            ],
            "lines": [{"tokens": ["n"]}],
        },
        "global_constraints": [
            {"kind": "sum_over_tests", "var": "n", "op": "<=", "value": 10}
        ],
    },
    "field required": {"multitest": False, "body": {"variables": [], "lines": []}},
    "input format names unknown variable": {
        "name": "x",
        "multitest": True,
        "test_count": {"kind": "int", "name": "t", "domain": {"lo": 1, "hi": 5}},
        "body": {"variables": [], "lines": [{"tokens": ["t"]}]},
    },
}


def test_every_piece_of_advice_fires_on_an_error_the_code_really_raises():
    """A marker that matches nothing is dead weight that fires never.

    Both sources count: the schema rejects a malformed IR, the emitters refuse
    one they cannot express, and `extract` sends both down the same repair
    path. So each one is checked by actually producing the failure.
    """
    covered = set()

    for marker, ir in BROKEN.items():
        error = _error_from(ir)
        assert marker.lower() in error.lower(), (
            f"expected an error containing {marker!r}, got: {error[:200]}"
        )
        assert prompts.advice_for(error), f"no advice fired for {marker!r}"
        covered.add(marker)

    assert covered == {marker for marker, _ in prompts.ADVICE}, (
        "every piece of advice needs a broken IR proving it fires"
    )


# --- scoring -------------------------------------------------------------


def test_a_problem_agrees_with_itself_completely():
    gold = load("next_round")
    agreement = compare(gold, gold)
    assert agreement.matched == agreement.total
    assert agreement.score == 1.0
    assert agreement.mismatches == []


def test_a_wrong_bound_is_counted_and_named():
    gold = load("watermelon")
    wrong = Problem.model_validate(
        json.loads(gold.model_dump_json()) | {"name": "Watermelon"}
    )
    wrong.body.variables[0].domain.hi = 50

    agreement = compare(wrong, gold)
    assert agreement.matched < agreement.total
    assert any("w.domain" in m for m in agreement.mismatches)


def test_origin_is_not_scored():
    """Provenance is a claim about how the bound was obtained, not the answer.

    The hand-written files predate the field, so scoring it would mark every
    correctly chosen bound as a disagreement.
    """
    gold = load("watermelon")
    marked = Problem.model_validate(json.loads(gold.model_dump_json()))
    marked.body.variables[0].domain.origin = "chosen"

    assert compare(marked, gold).mismatches == []


def test_a_missing_variable_shows_up_as_a_name_mismatch():
    gold = load("next_round")
    fewer = Problem.model_validate(json.loads(gold.model_dump_json()))
    fewer.body.variables = fewer.body.variables[:1]
    fewer.body.lines = fewer.body.lines[:1]

    agreement = compare(fewer, gold)
    assert any("variable names" in m for m in agreement.mismatches)


def test_the_wrong_line_layout_is_caught():
    """The failure the sample gate exists for, seen from the scoring side."""
    gold = load("next_round")
    flat = Problem.model_validate(json.loads(gold.model_dump_json()))
    flat.body.lines = [
        type(flat.body.lines[0])(tokens=["n"]),
        type(flat.body.lines[0])(tokens=["k"]),
        type(flat.body.lines[0])(tokens=["a"]),
    ]

    assert any("lines" in m for m in compare(flat, gold).mismatches)


def test_an_empty_agreement_scores_zero_rather_than_dividing_by_nothing():
    assert Agreement().score == 0.0


# --- finding the pairs ---------------------------------------------------


def test_pairs_matches_statements_to_gold_by_name(tmp_path):
    statements = tmp_path / "statements"
    problems = tmp_path / "problems"
    statements.mkdir()
    problems.mkdir()

    (statements / "watermelon_extracted.json").write_text("{}")
    (statements / "orphan_extracted.json").write_text("{}")
    (problems / "watermelon.json").write_text("{}")

    found = {p.stem: p.has_gold for p in pairs(statements, problems)}
    assert found == {"watermelon": True, "orphan": False}


def test_the_repo_has_at_least_one_matched_pair():
    """The eval is only meaningful if something can be compared.

    `samples/` is deliberately untracked, so a fresh clone has no statements to
    extract and nothing to score. That is not a failure, it just means the eval
    cannot run here -- so this skips rather than failing and hiding a real
    problem behind a missing directory.
    """
    if not pairs():
        pytest.skip("no extracted statements in samples/ (it is untracked)")
    assert any(p.has_gold for p in pairs())


def test_a_problem_is_left_out_of_its_own_prompt():
    """Otherwise the score measures copying rather than extraction.

    Four of the hand-written IR files are the worked examples, and some of
    those problems are also the ones being scored. A prompt that contains the
    answer produces a number that means nothing.
    """
    examples = tuple(n for n in prompts.EXAMPLES if n != "best_card")
    text = prompts.build({"title": "x"}, examples)

    assert "The Best Card" not in text
    assert "Watermelon" in text  # the others are still there


def test_scored_problems_overlap_the_examples():
    """If they did not, the leave-one-out above would be pointless.

    This is what makes the precaution necessary rather than decorative, and it
    will start failing the day the eval set grows past the examples -- which
    is the right time to be told.
    """
    scored = {p.stem for p in pairs() if p.has_gold}
    if not scored:
        pytest.skip("no extracted statements in samples/ (it is untracked)")
    assert scored & set(prompts.EXAMPLES)


def test_input_values_and_cross_test_constraints_are_listed_separately():
    """`sum_over_tests` is a constraint, not a kind of input value.

    Both come out of the schema, and merging them told the model it could
    declare a variable of kind "sum_over_tests".
    """
    assert prompts.supported_kinds() == ["array", "int", "string"]
    assert prompts.supported_constraints() == ["sum_over_tests"]


def test_a_single_member_union_still_yields_its_kind():
    """GlobalConstraint has one member today, and a Union of one collapses.

    Read naively that says the IR expresses no cross-test constraints at all,
    which is exactly the guarantee the sum accumulator exists to enforce.
    """
    assert prompts.supported_constraints()


def test_the_prompt_says_constraints_do_not_go_in_variables():
    text = prompts.build({"title": "x"})
    assert "global_constraints" in text
    assert "not input values" in text
