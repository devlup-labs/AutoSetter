"""Build the prompt that turns an extracted statement into a constraint IR.

The important decision here is that the prompt is **generated from the schema**
rather than written out by hand. `Problem.model_json_schema()` already knows
every variable kind, every field and every description, so describing the IR in
prose as well would give the model two sources that drift apart the moment the
schema changes. Widen the IR and this prompt widens with it.

The examples are the hand-written IR files in `ir/problems/`. They were written
as the expected answers for exactly this step, so using them as the worked
examples costs nothing and keeps one set of files doing both jobs.
"""

from __future__ import annotations

import json
from typing import Any

from testgen.ir.problems import PROBLEM_DIR
from testgen.ir.schema import Problem

# Chosen to cover the whole IR between them in as few tokens as possible:
#
#   watermelon   one scalar, nothing else
#   next_round   a bound that is another variable ("hi": "n"), an array with an
#                order guarantee, two values sharing a line
#   best_card    multitest, a sum across test cases, a case-insensitive answer
#   two_sum      a statement with no input format and no bounds, so both had to
#                be designed -- the case that decides whether this runs
#                unattended on the OA problems AutoSetter exists to port
#
# The last one earns its place twice over. Without an example of a designed
# format the model does not attempt one at all: handed a LeetCode statement it
# returns the statement, five times out of five. And it has to be a problem
# that is NOT in the eval set, or the score for that whole class of statement
# is just the model copying an answer it was shown.
EXAMPLES = ("watermelon", "next_round", "best_card", "two_sum")

# Keys pydantic emits that carry no information for a model reading the schema.
NOISE = ("title", "additionalProperties", "$schema")


def _prune(node: Any) -> Any:
    """Drop the parts of a generated JSON Schema a model does not need.

    pydantic emits a `title` for every field, which roughly doubles the size of
    the schema and says nothing the field name did not already say. The
    descriptions are kept, because those are the parts that were written for a
    reader.
    """
    if isinstance(node, dict):
        return {k: _prune(v) for k, v in node.items() if k not in NOISE}
    if isinstance(node, list):
        return [_prune(v) for v in node]
    return node


def schema_text() -> str:
    """The IR's schema, generated from the code so it cannot go stale."""
    return json.dumps(_prune(Problem.model_json_schema()), indent=2)


def supported_kinds() -> list[str]:
    """Every variable kind the IR can express, read out of the schema."""
    defs = Problem.model_json_schema().get("$defs", {})
    kinds = []
    for name, body in defs.items():
        const = body.get("properties", {}).get("kind", {})
        value = const.get("const") or (const.get("enum") or [None])[0]
        if value:
            kinds.append(value)
    return sorted(set(kinds))


def examples_text(names: tuple[str, ...] = EXAMPLES) -> str:
    """The worked examples, straight from the hand-written IR files."""
    blocks = []
    for name in names:
        path = PROBLEM_DIR / f"{name}.json"
        if not path.exists():
            continue
        blocks.append(f"--- example: {name} ---\n{path.read_text().strip()}")
    return "\n\n".join(blocks)


RULES = """
RULES

1. Reply with ONE JSON object and nothing else. No prose, no markdown fences,
   no explanation before or after it.

2. Every bound is either a whole number, or the NAME of another variable read
   earlier in the input. "1 <= k <= n <= 50" makes k's domain {"lo": 1,
   "hi": "n"} and n's domain {"lo": 1, "hi": 50}. Never write an expression
   like "n-1" or "2*n": if a bound is not a plain number or a plain name, put
   the variable in "unsupported" instead.

3. Write out powers of ten. "2 * 10^5" is 200000. Never leave "10^5" as text.

4. "source" must quote the sentence the constraint came from, as close to
   word for word as you can manage. It is how a person checks your work.

5. n integers "a_1, a_2, ..., a_n (1 <= a_i <= 10^9)" is ONE array variable
   named "a", with "length": "n" and "elem": {"lo": 1, "hi": 1000000000}.
   It is NOT a variable called "a_i" and NOT n separate variables. The
   subscript means every element, so the bound belongs in "elem".

6. "lines" describes the layout of the input file, in order, one entry per
   line of a single test case. Values sharing a line go in the same "tokens"
   list: "the first line contains n and k" is {"tokens": ["n", "k"]}. An
   array name in a "tokens" list means all of its elements on that line,
   separated by single spaces. Getting this wrong makes the validator reject
   the problem's own samples, so read the input format carefully.

7. If the statement gives no bound for a value, CHOOSE one that makes the
   problem sensible, set that domain's "origin" to "chosen", and use "source"
   to explain why you picked it. Do not leave the variable out and do not
   pretend the statement said it. A chosen bound is honest; a bound presented
   as read when it was not is not.

8. If the input contains anything the schema above cannot describe -- a graph
   given as edges, a grid of characters, points in the plane, a guarantee
   relating several values such as "at least one pair sums to k" -- name it in
   "unsupported", in plain words. Still fill in everything you CAN express.
   Listing it is the right answer. Approximating it is not.

9. If something needed is absent from the statement and cannot sensibly be
   chosen, name it in "missing".

10. Only use variables that the statement actually mentions. Do not invent a
    name to make the format work.

11. multitest is true only when the input begins with a count of test cases.
    When it is true, "test_count" describes that count and "body" describes
    ONE test case. When it is false, "test_count" must be absent.

12. This rule applies ONLY when the statement shows an EXAMPLE instead of an
    input format -- "Input = [2, 7, 4, 0, 9, 5, 1, 3] & Sum = 20", or a
    function signature, with no mention of lines and no bounds anywhere.

    If the statement describes its input in words -- "the first line contains
    an integer t (1 <= t <= 10^4)" -- then it HAS a format and it HAS bounds.
    Follow them exactly and leave every "origin" as "stated". Do not mark a
    bound as chosen when the statement gave it to you. Skip the rest of this
    rule entirely.

    Otherwise the problem has no input format yet, and you must DESIGN one
    rather than report that it is missing. Design it like this, every time:

      * Every array needs a length read from the input BEFORE it. An array of
        numbers with no stated size means you declare an int variable for the
        size -- call it n -- give it a chosen domain, and put it on an earlier
        line. An array can never have a length that is not a declared variable.
      * Read the sizes and the scalars first, then the arrays, one line each.
      * Give every variable a domain, with "origin": "chosen", and use "source"
        to say why you picked it. A bound that admits the intended solution and
        excludes the brute force one is the useful choice.

    This is a design decision and it is allowed to be. What is not allowed is
    an array whose length names a variable you never declared.

13. "output" decides which checker the problem gets, so read the output format
    for these three things specifically:

      * "case_insensitive": true when the statement says the answer may be
        printed in any case -- "each letter may be printed in either case",
        "in any case", "uppercase or lowercase". A YES/NO problem usually says
        this. Missing it means correct submissions get rejected.
      * "unique_answer": false when several different answers would be
        accepted -- "print any of them", "if there are several solutions,
        output any", or an answer that is a set of indices or elements that
        could be given in more than one order. True only when exactly one
        output is correct.
      * "float_eps": set it when the answer is a real number judged to a
        tolerance, and leave it null otherwise.
""".strip()


ENVELOPE = """
REPLY FORMAT

{
  "ir": { ... a Problem object matching the schema above ... },
  "unsupported": [ "plain words, one per thing the IR cannot express" ],
  "missing":     [ "plain words, one per thing the statement never says" ]
}

Both lists are usually empty. Include them anyway.
""".strip()


# The order statement fields are shown in, and what to call them.
STATEMENT_FIELDS = (
    ("title", "TITLE"),
    ("story", "DESCRIPTION"),
    ("input_format", "INPUT FORMAT"),
    ("output_format", "OUTPUT FORMAT"),
    ("constraints", "CONSTRAINTS"),
    ("notes", "NOTES"),
)


def statement_text(data: dict[str, Any]) -> str:
    """Render the statement as prose rather than as JSON.

    It used to go in as `json.dumps(data)`, which put an object in the prompt
    shaped exactly like the thing being asked for -- and a 4B model answered by
    copying it, every attempt, however the instructions were worded. Handing it
    the same content as labelled text leaves nothing to copy: the only JSON
    anywhere in the prompt is now IR shaped.
    """
    out: list[str] = []

    for key, label in STATEMENT_FIELDS:
        value = str(data.get(key) or "").strip()
        out.append(f"{label}:")
        out.append(f"  {value}" if value else "  (the statement does not say)")
        out.append("")

    samples = data.get("samples") or []
    if not samples:
        out.append("SAMPLES:")
        out.append("  (none given)")
        return "\n".join(out)

    for index, sample in enumerate(samples, start=1):
        if not isinstance(sample, dict):
            continue
        out.append(f"SAMPLE {index} INPUT:")
        out.append("  " + str(sample.get("input", "")).replace("\n", "\n  "))
        out.append(f"SAMPLE {index} OUTPUT:")
        out.append("  " + str(sample.get("output", "")).replace("\n", "\n  "))
        out.append("")

    return "\n".join(out).rstrip()


def build(data: dict[str, Any], examples: tuple[str, ...] = EXAMPLES) -> str:
    """The full extraction prompt for one extracted statement.

    The order of the sections is not cosmetic. The statement goes near the
    front and the required reply shape goes last, because the first version of
    this prompt put the statement at the end, directly above "reply with the
    JSON object only" -- and a 4B model answered by copying the statement
    straight back. It was the nearest JSON object to the instruction. Nothing
    in the reply was wrong except that it was the input.

    Worse, it failed as `name: Field required`, which reads like a small
    omission and sent the repair loop chasing a field instead of the mistake.
    """
    kinds = ", ".join(supported_kinds())
    return f"""You are converting a competitive programming problem statement into a
machine readable description of its INPUT, called the IR.

Everything downstream is built from what you produce: the validator that
decides whether a test file is legal, the generator that produces test files,
and the checker that judges answers. A bound you get wrong becomes a validator
that enforces the wrong rule, and nothing later in the pipeline can catch it.
When you are unsure, say so in "unsupported" or "missing" rather than guessing
quietly.

THE STATEMENT TO CONVERT

{statement_text(data)}

That is the INPUT to this task. What you produce is something else entirely,
described below.

The IR can describe these kinds of input value, and nothing else: {kinds}.

SCHEMA

{schema_text()}

{RULES}

WORKED EXAMPLES

Each of these is the kind of object you must produce.

{examples_text(examples)}

{ENVELOPE}

Now convert the statement above. Reply with one JSON object having exactly the
three keys "ir", "unsupported" and "missing", and nothing else.
"""


# A schema error names a symptom. These say what to do about it.
#
# The first one is the failure that actually happens: a model writes an array
# with "length": "n" and never declares n, because in the statement n is
# implied by the array rather than read separately. Pydantic catches it every
# time, but the error says what is wrong and not how to fix it, and a model
# handed only the symptom tends to send the same reply back unchanged.
ADVICE = (
    (
        "unknown variable",
        """A name was used as a bound, a length or in the input format, and no
variable of that name exists. There are exactly two correct fixes, and you must
pick one:

  * ADD the missing variable to "body"."variables" -- this is right when the
    value really is read from the input, which is usual for an array's length.
    An array of n numbers almost always has n read first, on its own line, and
    that n must appear as an "int" variable with a domain of its own before the
    array can refer to it.
  * CHANGE the bound to a plain number, if nothing in the input carries it.

Do not simply resend the same object.""",
    ),
    (
        "must declare test_count",
        """"multitest" is true, so the input begins with a count of test cases
and "test_count" must describe it. Either add "test_count", or set "multitest"
to false if the input has no such count.""",
    ),
    (
        "test_count is only meaningful",
        """"test_count" was given but "multitest" is false. Either set
"multitest" to true, if the input really does begin with a count of test cases,
or remove "test_count".""",
    ),
    (
        "global constraints sum across test cases",
        """A constraint summing a value over all test cases only makes sense
when there are test cases. Set "multitest" to true and add "test_count", or
remove the global constraint.""",
    ),
    (
        "field required",
        """A field the schema requires is absent. The error names it and the
path to it. Add it and keep everything else as it was.

The one most often left out is the top level "name": every IR needs a short
name for the problem, as a string, next to "multitest" and "body".""",
    ),
    (
        "input format names unknown variable",
        """A name in "lines"."tokens" is not one of the declared variables.
Every token must be the name of a variable in "body"."variables", or the test
count. Fix the layout or declare the variable.""",
    ),
)


def advice_for(error: str) -> str:
    """What to actually do about a schema error, when we can tell."""
    lowered = error.lower()
    for marker, guidance in ADVICE:
        if marker.lower() in lowered:
            return f"\n--- HOW TO FIX THIS ---\n{guidance}\n"
    return ""


def retry_after_echo(data: dict[str, Any], examples: tuple[str, ...] = EXAMPLES) -> str:
    """Start again after the model handed the statement back.

    Not a repair. An echo means the model never engaged with the task, so
    there is nothing in the reply to fix, and the usual repair -- a short note
    saying what was wrong -- is actively harmful here. The first version of it
    named the three fields an IR needs, and the model filled them in with
    statement content: "multitest" became a list of test cases, "body" became
    the story. Field names without their meaning are a template to be filled,
    not an instruction.

    So the whole prompt goes again, schema and worked examples included, with
    the mistake named at the top where it will be read.
    """
    return f"""Your previous reply repeated the statement back instead of converting it.

Read the WORKED EXAMPLES below and produce an object of that kind. The
statement's own fields -- story, input_format, samples, time_limit -- do not
appear anywhere in an IR. An IR describes the SHAPE of the input: which values
are read, in what order, and between what bounds.

{build(data, examples)}"""


def repair_schema(previous: str, error: str) -> str:
    """Ask again after the reply failed to load as a Problem.

    The pydantic error is handed back verbatim, because it names the field and
    says what was wrong with it. On its own that turned out not to be enough:
    a model given only "length of a refers to unknown variable 'n'" resends the
    same object, because it can see nothing wrong with saying the array has
    length n. So the known error shapes carry an instruction as well.
    """
    return f"""Your previous reply did not load as a valid IR.

--- WHAT YOU SENT ---
{previous.strip()[:6000]}

--- WHY IT WAS REJECTED ---
{error.strip()[:3000]}
{advice_for(error)}
Fix exactly what the error describes and send the whole object again, in the
same reply format as before. Change nothing else.

Reply with the JSON object only.
"""


def repair_samples(previous: str, sample: str, reason: str) -> str:
    """Ask again after the emitted validator rejected the problem's own sample.

    This is the stronger of the two gates. The samples shipped with the
    statement are known to be legal input, so a validator built from the IR
    that rejects one of them proves the IR is wrong -- usually a bound that is
    too tight, or a line layout that does not match the real file.
    """
    return f"""Your IR loaded correctly, but it is wrong.

A validator was built from it and run on a sample input that came with the
problem. That sample is known to be legal, so a validator that rejects it has
been built from constraints that do not describe this problem.

--- WHAT YOU SENT ---
{previous.strip()[:6000]}

--- THE SAMPLE IT REJECTED ---
{sample.strip()[:2000]}

--- WHAT THE VALIDATOR SAID ---
{reason.strip()[:1000]}

The two usual causes, in order of likelihood:

  * "lines" does not match how the input is really laid out -- values that
    share a line put on separate lines, or the other way round.
  * a bound is too tight, and the sample legitimately goes outside it.

Work out which it is from the message above, fix it, and send the whole object
again in the same reply format.

Reply with the JSON object only.
"""
