"""Tests for the extraction loop.

Every test here uses a canned model: a function that returns whatever reply the
test wants to study. That keeps the suite fast, offline and deterministic, and
it means the thing under test is the part that was actually written here -- the
gates and the repair loop -- rather than the quality of somebody's weights.

The interesting cases are the failures. An extraction that works first time
proves very little; what matters is that a wrong IR is caught, that the reason
gets fed back, and that a second attempt is allowed to fix it.
"""

from __future__ import annotations

import json

import pytest

from testgen.blockers import Blocker, Severity, Verdict, verdict
from testgen.extract import (
    Echoed,
    Extraction,
    chosen_bounds,
    extract,
    json_object,
    slug,
    unwrap,
)
from testgen.ir.problems import load
from testgen.ir.schema import Problem
from testgen.samples import normalise, read as read_samples

# A statement with real samples, so the sample gate has something to run on.
WATERMELON = {
    "title": "Watermelon",
    "story": "w kilos, split into two even parts",
    "input_format": "The first line contains integer w (1 <= w <= 100)",
    "output_format": "YES or NO",
    "constraints": "1 <= w <= 100",
    "samples": [{"input": "8\n", "output": "YES", "explanation": ""}],
    "time_limit": "1 second",
    "memory_limit": "256 megabytes",
    "notes": "",
}

GOOD_IR = {
    "name": "Watermelon",
    "source": "CF 4A",
    "multitest": False,
    "body": {
        "variables": [
            {
                "kind": "int",
                "name": "w",
                "domain": {"lo": 1, "hi": 100},
                "source": "The first line contains integer w (1 <= w <= 100)",
            }
        ],
        "lines": [{"tokens": ["w"]}],
    },
    "global_constraints": [],
    "output": {"unique_answer": True, "case_insensitive": False},
}


def envelope(ir, unsupported=(), missing=()):
    return json.dumps(
        {"ir": ir, "unsupported": list(unsupported), "missing": list(missing)}
    )


def replies(*texts):
    """A model that returns each of these in turn, then repeats the last."""
    sequence = list(texts)
    calls: list[str] = []

    def model(prompt: str) -> str:
        calls.append(prompt)
        return sequence[min(len(calls) - 1, len(sequence) - 1)]

    model.calls = calls  # type: ignore[attr-defined]
    return model


# --- reading the reply ---------------------------------------------------


def test_json_object_reads_a_bare_object():
    assert json_object('{"a": 1}') == {"a": 1}


def test_json_object_survives_a_markdown_fence():
    assert json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_object_survives_prose_around_it():
    text = 'Here is the IR you asked for:\n{"a": 1}\nHope that helps.'
    assert json_object(text) == {"a": 1}


def test_json_object_ignores_braces_inside_strings():
    """A source quote containing a brace must not end the object early."""
    text = '{"source": "the set {1, 2}", "n": 3}'
    assert json_object(text)["n"] == 3


def test_json_object_reports_a_reply_with_no_object():
    with pytest.raises(ValueError, match="no JSON object"):
        json_object("I could not do that.")


def test_json_object_reports_an_unclosed_object():
    with pytest.raises(ValueError, match="never closes"):
        json_object('{"a": 1')


# --- the envelope --------------------------------------------------------


def test_unwrap_turns_reported_gaps_into_blockers():
    ir, blockers = unwrap(
        {"ir": GOOD_IR, "unsupported": ["m edges"], "missing": ["the bound on n"]}
    )
    assert ir == GOOD_IR
    assert {b.severity for b in blockers} == {
        Severity.INEXPRESSIBLE,
        Severity.MISSING,
    }


def test_unwrap_accepts_a_bare_problem_without_the_envelope():
    """A model that forgets the wrapper has still done the useful part."""
    ir, blockers = unwrap(GOOD_IR)
    assert ir == GOOD_IR
    assert blockers == []


def test_the_statement_handed_back_is_recognised_as_such():
    """The failure a 4B model really produced against the first prompt.

    An echoed statement has no "name" and no "body", so pydantic calls it a
    missing field -- and the repair loop then spends every attempt adding
    fields to the wrong object instead of saying "that is the input".
    """
    with pytest.raises(Echoed, match="handed back"):
        unwrap(WATERMELON)


def test_a_real_ir_is_never_mistaken_for_an_echo():
    ir, _ = unwrap(GOOD_IR)
    assert ir["name"] == "Watermelon"


def test_an_echo_inside_a_correct_wrapper_is_caught_too():
    """Getting the envelope right does not make the contents an IR."""
    with pytest.raises(Echoed):
        unwrap({"ir": WATERMELON, "unsupported": [], "missing": []})


def test_an_echo_is_retried_with_the_whole_prompt_not_a_note(tmp_path):
    """A short corrective made this worse, so the retry re-sends everything.

    Naming the fields an IR needs, with no schema and no examples beside them,
    gave the model a template to fill from the statement: "multitest" came back
    as a list of test cases. The retry has to carry the examples.
    """
    model = replies(json.dumps(WATERMELON), envelope(GOOD_IR))
    result = extract(WATERMELON, model=model, workdir=tmp_path)

    assert result.ok
    assert result.attempts[0].gate == "parse"

    retry = model.calls[1]
    assert "repeated the statement back" in retry
    assert "WORKED EXAMPLES" in retry
    assert "SCHEMA" in retry


# --- chosen bounds are detected, not taken on trust ----------------------


def test_chosen_bounds_are_found_in_the_ir():
    ir = json.loads(json.dumps(GOOD_IR))
    ir["body"]["variables"][0]["domain"]["origin"] = "chosen"
    found = chosen_bounds(Problem.model_validate(ir))
    assert len(found) == 1
    assert found[0].severity is Severity.DECIDED
    assert found[0].field == "w"


def test_stated_bounds_produce_no_blocker():
    assert chosen_bounds(Problem.model_validate(GOOD_IR)) == []


def test_hand_written_problems_are_all_stated_by_default():
    """The new field must not change what the existing IR files mean."""
    assert chosen_bounds(load("watermelon")) == []


# --- the loop ------------------------------------------------------------


def test_a_correct_reply_passes_both_gates(tmp_path):
    result = extract(
        WATERMELON, model=replies(envelope(GOOD_IR)), workdir=tmp_path
    )
    assert result.ok
    assert result.verdict is Verdict.READY
    assert result.samples_passed == 1
    assert len(result.attempts) == 2  # schema, then samples


def test_a_bad_bound_is_caught_by_the_samples_and_repaired(tmp_path):
    """The sample gate's whole reason for existing.

    This IR is schema-valid: w is an int with a range, everything resolves. It
    is also wrong, because the problem's own sample has w = 8 and the range
    stops at 5. Nothing but running the validator on real input can see that.
    """
    too_tight = json.loads(json.dumps(GOOD_IR))
    too_tight["body"]["variables"][0]["domain"]["hi"] = 5

    model = replies(envelope(too_tight), envelope(GOOD_IR))
    result = extract(WATERMELON, model=model, workdir=tmp_path)

    assert result.ok
    assert result.samples_passed == 1
    gates = [(a.gate, a.ok) for a in result.attempts]
    assert ("samples", False) in gates
    # The second prompt has to carry the rejection, or the model is guessing.
    assert "rejected" in model.calls[1] or "sample" in model.calls[1].lower()


def test_an_invented_variable_is_caught_by_the_schema(tmp_path):
    """Pydantic already refuses a bound naming a variable nobody declared."""
    invented = json.loads(json.dumps(GOOD_IR))
    invented["body"]["variables"][0]["domain"]["hi"] = "n"

    model = replies(envelope(invented), envelope(GOOD_IR))
    result = extract(WATERMELON, model=model, workdir=tmp_path)

    assert result.ok
    assert ("schema", False) in [(a.gate, a.ok) for a in result.attempts]


def test_unparseable_replies_are_retried(tmp_path):
    model = replies("I am afraid I cannot help with that.", envelope(GOOD_IR))
    result = extract(WATERMELON, model=model, workdir=tmp_path)
    assert result.ok
    assert result.attempts[0].gate == "parse"


def test_giving_up_produces_a_blocker_not_an_exception(tmp_path):
    result = extract(
        WATERMELON, model=replies("nonsense"), max_attempts=2, workdir=tmp_path
    )
    assert not result.ok
    assert result.verdict is Verdict.FALLBACK
    assert len(result.attempts) == 2


def test_an_unsupported_construct_sends_the_problem_to_fallback(tmp_path):
    model = replies(envelope(GOOD_IR, unsupported=["the input is a graph"]))
    result = extract(WATERMELON, model=model, workdir=tmp_path)
    assert result.ok  # an IR did come out
    assert result.verdict is Verdict.FALLBACK  # but it must not be used


def test_a_chosen_bound_asks_for_review_rather_than_blocking(tmp_path):
    """Full automation means a decision is recorded, not that it stops work."""
    chosen = json.loads(json.dumps(GOOD_IR))
    chosen["body"]["variables"][0]["domain"]["origin"] = "chosen"

    result = extract(WATERMELON, model=replies(envelope(chosen)), workdir=tmp_path)
    assert result.verdict is Verdict.REVIEW


# --- statements that give no input format at all -------------------------

# A LeetCode style statement: an example where the input format should be, no
# constraints, and a "sample" written in a notation that is not a test file.
LEETCODE = {
    "title": "3 Sum Problem",
    "story": "Find a triplet summing to the target.",
    "input_format": "Input = [2, 7, 4, 0, 9, 5, 1, 3] & Sum = 20",
    "output_format": "Output = true [7, 4, 9]",
    "constraints": "",
    "samples": [{"input": "[2, 7, 4, 0, 9, 5, 1, 3] & Sum = 20", "output": "true"}],
    "time_limit": "1 second",
    "memory_limit": "256 megabytes",
    "notes": "",
}

DESIGNED_IR = {
    "name": "3 Sum",
    "multitest": False,
    "body": {
        "variables": [
            {
                "kind": "int",
                "name": "n",
                "domain": {"lo": 3, "hi": 3000, "origin": "chosen"},
                "source": "CHOSEN. The statement gives no size.",
            },
            {
                "kind": "array",
                "name": "a",
                "length": "n",
                "elem": {"lo": -100000, "hi": 100000, "origin": "chosen"},
                "source": "CHOSEN. Usual bound for this problem.",
            },
        ],
        "lines": [{"tokens": ["n"]}, {"tokens": ["a"]}],
    },
    "global_constraints": [],
    "output": {"unique_answer": False, "case_insensitive": True},
}


def test_an_example_is_told_apart_from_a_format():
    from testgen.inspect_statement import looks_like_an_example

    assert looks_like_an_example("Input = [2, 7, 4] & Sum = 20")
    assert looks_like_an_example("def threeSum(nums: List[int]) -> bool")
    # A real format describes lines and carries bounds.
    assert not looks_like_an_example(
        "The first line contains an integer t (1 <= t <= 10^4)"
    )
    assert not looks_like_an_example("")


def test_a_designed_format_skips_the_sample_gate(tmp_path):
    """The samples are in the statement's notation, not the designed one.

    "[2, 7, 4] & Sum = 20" is not a test file for any format. A validator built
    from a perfectly correct IR rejects it, so running the gate would fail
    every LeetCode style problem for a reason that says nothing about the IR.
    """
    result = extract(LEETCODE, model=replies(envelope(DESIGNED_IR)), workdir=tmp_path)

    assert result.ok
    assert result.designed_format
    assert result.samples_total == 0
    assert result.verdict is Verdict.REVIEW
    assert any("designed" in b.detail for b in result.blockers)


def test_skipping_the_gate_is_decided_by_the_statement_not_the_model(tmp_path):
    """Otherwise it would be an escape hatch a model could talk its way into.

    The same reply, against a statement that DOES describe its input, must
    still be gated on the samples.
    """
    result = extract(
        WATERMELON, model=replies(envelope(GOOD_IR)), workdir=tmp_path
    )
    assert not result.designed_format
    assert result.samples_total == 1


def test_a_statement_with_no_samples_says_it_was_never_checked(tmp_path):
    """A green run with no samples must not look like a green run with ten."""
    bare = dict(WATERMELON, samples=[])
    result = extract(bare, model=replies(envelope(GOOD_IR)), workdir=tmp_path)
    assert result.ok
    assert result.verdict is Verdict.REVIEW
    assert any("never checked" in b.detail for b in result.blockers)


def test_the_emitter_refusing_an_ir_is_treated_as_a_repairable_failure(tmp_path):
    """The emitter raises rather than skipping a rule it cannot express.

    An IR whose input format names a variable that is not in the body gets
    past pydantic's name check (the tokens are checked, but a token naming the
    test count is legal) yet cannot be emitted. That must come back as a
    failed attempt, not an unhandled exception out of the extractor.
    """
    broken = json.loads(json.dumps(GOOD_IR))
    broken["multitest"] = True
    broken["test_count"] = {
        "kind": "int",
        "name": "t",
        "domain": {"lo": 1, "hi": 10},
        "source": "t test cases",
    }
    broken["body"]["lines"] = [{"tokens": ["t"]}]

    model = replies(envelope(broken), envelope(GOOD_IR))
    result = extract(WATERMELON, model=model, workdir=tmp_path)
    assert result.ok
    assert any(not a.ok for a in result.attempts)


# --- naming --------------------------------------------------------------


def test_save_refuses_to_overwrite_a_hand_written_ir(tmp_path):
    """The IR files are the eval's baseline. Losing one silently is worse
    than any extraction failure, because nothing afterwards would notice."""
    from testgen.extract import save

    problem = Problem.model_validate(GOOD_IR)
    save(problem, "thing", directory=tmp_path)

    with pytest.raises(FileExistsError, match="baseline"):
        save(problem, "thing", directory=tmp_path)

    assert save(problem, "thing", directory=tmp_path, force=True).exists()


def test_a_saved_ir_loads_back_unchanged(tmp_path):
    """Saving has to produce a file the rest of the tools can read."""
    from testgen.extract import save

    problem = Problem.model_validate(GOOD_IR)
    path = save(problem, "roundtrip", directory=tmp_path)
    assert Problem.model_validate_json(path.read_text()) == problem


def test_slug_makes_a_file_stem_from_a_title():
    assert slug({"title": "The Best Card"}) == "the_best_card"
    assert slug({"title": "3 Sum Problem!"}) == "3_sum_problem"
    assert slug({}) == "problem"


# --- samples -------------------------------------------------------------


def test_normalise_gives_exactly_one_trailing_newline():
    assert normalise("5\n2\n\n\n") == "5\n2\n"
    assert normalise("5\r\n2\r\n") == "5\n2\n"
    assert normalise("  5  \n") == "5\n"


def test_normalise_of_nothing_is_nothing():
    assert normalise("   \n\n") == ""


def test_samples_with_no_input_are_dropped():
    data = {"samples": [{"input": "", "output": "x"}, {"input": "5", "output": "y"}]}
    assert [s.text for s in read_samples(data)] == ["5\n"]


# --- the verdict ---------------------------------------------------------


def test_the_worst_blocker_decides_the_verdict():
    decided = Blocker(Severity.DECIDED, "picked a bound")
    inexpressible = Blocker(Severity.INEXPRESSIBLE, "a graph")
    assert verdict([]) is Verdict.READY
    assert verdict([decided]) is Verdict.REVIEW
    assert verdict([decided, inexpressible]) is Verdict.FALLBACK


def test_an_extraction_with_no_problem_is_always_fallback():
    assert Extraction().verdict is Verdict.FALLBACK


# --- bugs found by auditing the finished code ----------------------------


def test_normalise_strips_indentation_not_just_trailing_space():
    """A sample transcribed into JSON often picks up indentation.

    testlib reads the first token of a line immediately, so a space in front of
    it fails the validator -- and the failure gets attributed to the IR, which
    was correct all along.
    """
    assert normalise("  5\n  1 2 3\n") == "5\n1 2 3\n"
    assert normalise("\t5\n") == "5\n"
    # Separators *inside* a line are untouched; only the ends are stripped.
    assert normalise("1 2 3") == "1 2 3\n"


def test_an_indented_sample_still_passes_the_gate(tmp_path):
    """The whole point of the fix, exercised through the real loop."""
    indented = dict(WATERMELON, samples=[{"input": "   8   \n", "output": "YES"}])
    result = extract(indented, model=replies(envelope(GOOD_IR)), workdir=tmp_path)

    assert result.ok
    assert result.samples_passed == 1


def test_a_format_containing_an_equals_sign_is_not_called_an_example():
    """This one silently skipped the sample gate, the strongest check there is.

    Brackets or an equals sign are not enough on their own to call something an
    example, because ordinary input formats contain both.
    """
    from testgen.inspect_statement import looks_like_an_example

    assert not looks_like_an_example("The first line contains n, where n = the count")
    assert not looks_like_an_example("Line 1: n and k. Line 2: n integers a[i]")
    # The genuine articles still read as examples.
    assert looks_like_an_example("Input = [2, 7, 4] & Sum = 20")
    assert looks_like_an_example("def threeSum(nums: List[int]) -> bool")


def test_a_real_format_is_still_gated_on_its_samples(tmp_path):
    """The consequence of the bug above, checked end to end."""
    tricky = dict(
        WATERMELON, input_format="The first line contains w, where w = the weight"
    )
    result = extract(tricky, model=replies(envelope(GOOD_IR)), workdir=tmp_path)

    assert not result.designed_format
    assert result.samples_total == 1
