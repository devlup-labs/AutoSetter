"""Turn an extracted statement into a constraint IR, without a person.

`adapt.py` does this with regular expressions and gives up as soon as the
bounds are not written the way it expects. This does it with a model, and the
interesting part is not the model -- it is what happens to what the model says.

Nothing here trusts the reply. It goes through two gates:

    schema gate    the reply must load as a `Problem`. Pydantic already refuses
                   a bound that names a variable the statement never mentioned,
                   a multitest problem with no test count, an array whose
                   length is not a real name. That is free, and it catches the
                   most common way an extraction goes wrong.

    sample gate    a validator is emitted from the candidate IR, compiled, and
                   run on the samples that shipped with the problem. Those are
                   known to be legal input, so a rejection proves the IR is
                   wrong. This is the gate that catches the things the schema
                   cannot see: a bound that is too tight, a line layout that
                   does not match the real file.

A failure at either gate is not the end. The reason is written back into a new
prompt and the model tries again, which is the same idea as feeding compiler
errors back to a model writing C++ -- except these signals are about meaning,
so passing them means something.

What comes out is an IR plus a list of blockers, and the blockers decide what
the caller may do with it: run unattended, run but flag for review, or fall
back to the LLM-written C++ path entirely.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from testgen import prompt as prompts
from testgen.blockers import Blocker, Severity, Verdict, verdict
from testgen.build import CompileError, accepts, build_validator
from testgen.inspect_statement import looks_like_an_example
from testgen.ir.problems import PROBLEM_DIR
from testgen.ir.schema import ArrayVar, IntVar, Problem
from testgen.llm import Completer, Model
from testgen.samples import Sample, write as write_samples

DEFAULT_ATTEMPTS = 4

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Attempt:
    """One trip to the model, and how far the reply got."""

    number: int
    gate: str  # "parse", "schema" or "samples"
    ok: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        return f"  {mark} attempt {self.number}  {self.gate:<8} {self.detail}"


@dataclass
class Extraction:
    """The result of trying to build an IR from a statement."""

    problem: Problem | None = None
    ir: dict[str, Any] | None = None
    blockers: list[Blocker] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    samples_passed: int = 0
    samples_total: int = 0
    #: True when the statement gave no stdin format, so one had to be designed
    #: and the samples could not be used to check the result.
    designed_format: bool = False

    @property
    def verdict(self) -> Verdict:
        """What the caller should do with this, from the blockers alone."""
        if self.problem is None:
            return Verdict.FALLBACK
        return verdict(self.blockers)

    @property
    def ok(self) -> bool:
        """Did an IR come out at all, whatever still needs confirming."""
        return self.problem is not None


def json_object(text: str) -> dict[str, Any]:
    """Read the one JSON object out of a model's reply.

    json mode usually means the reply is already bare JSON, but a model that
    ignores it wraps the object in a fence or a sentence, and failing the whole
    attempt over that would waste a call. So the outermost braces are found by
    scanning, with quotes and escapes respected so a brace inside a string does
    not end the object early.
    """
    stripped = FENCE.sub("", text).strip()

    start = stripped.find("{")
    if start == -1:
        raise ValueError("the reply contains no JSON object at all")

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(stripped[start : index + 1])

    raise ValueError("the reply starts a JSON object but never closes it")


# The fields an extracted statement has and an IR never does. A reply carrying
# these is the input handed back rather than an answer.
STATEMENT_FIELDS = {"story", "input_format", "output_format", "samples", "time_limit"}


class Echoed(ValueError):
    """Raised when the model returned the statement instead of an IR.

    Worth its own error because of how it fails otherwise. An echoed statement
    has no "name" and no "body", so pydantic reports a missing field, which
    reads like a small omission -- and the repair loop then spends every
    remaining attempt adding fields to the wrong object. Naming it lets the
    retry say the one thing that helps.
    """


def unwrap(envelope: dict[str, Any]) -> tuple[dict[str, Any], list[Blocker]]:
    """Split the reply into the IR and what the model said it could not do.

    The reply is supposed to be `{"ir": ..., "unsupported": [...],
    "missing": [...]}`, but a model that forgets the wrapper and returns a bare
    Problem has still done the useful part, so that is accepted too.
    """
    reported: list[Blocker] = []

    if "ir" not in envelope:
        overlap = STATEMENT_FIELDS & set(envelope)
        if len(overlap) >= 2:
            raise Echoed(
                "the statement was handed back rather than converted; it has "
                f"{', '.join(sorted(overlap))} in it, which an IR never has"
            )
        return envelope, reported

    for text in envelope.get("unsupported") or []:
        reported.append(Blocker(Severity.INEXPRESSIBLE, str(text)))
    for text in envelope.get("missing") or []:
        reported.append(Blocker(Severity.MISSING, str(text)))

    ir = envelope["ir"]
    if not isinstance(ir, dict):
        raise ValueError('"ir" was present but was not an object')
    if len(STATEMENT_FIELDS & set(ir)) >= 2:
        # The wrapper was right and the contents were still the statement.
        raise Echoed(
            'the "ir" you sent is the statement you were given, copied back. '
            "It must be a Problem object with \"name\", \"multitest\" and "
            '"body" instead.'
        )
    return ir, reported


def chosen_bounds(problem: Problem) -> list[Blocker]:
    """Find every bound the model picked rather than read.

    Detected from the IR rather than taken from the model's own account of what
    it did, because a model that quietly invents a bound will not volunteer it.
    """
    found: list[Blocker] = []

    def note(name: str, domain: object, what: str) -> None:
        if getattr(domain, "origin", "stated") == "chosen":
            found.append(
                Blocker(
                    Severity.DECIDED,
                    f"the {what} of {name} was not stated; "
                    f"{getattr(domain, 'lo')} to {getattr(domain, 'hi')} was chosen",
                    field=name,
                )
            )

    if problem.test_count is not None:
        note(problem.test_count.name, problem.test_count.domain, "range")

    for var in problem.body.variables:
        if isinstance(var, IntVar):
            note(var.name, var.domain, "range")
        elif isinstance(var, ArrayVar):
            note(var.name, var.elem, "element range")

    return found


def _gate_samples(
    problem: Problem, samples: list[Sample], stem: str, workdir: Path
) -> tuple[int, str, str]:
    """Build a validator from the IR and run it on the problem's own samples.

    Returns how many were accepted, plus the first sample that was refused and
    the reason, so the repair prompt can quote both.
    """
    binary = build_validator(problem, stem, outdir=workdir)

    passed = 0
    for sample in samples:
        ok, reason = accepts(binary, sample.text)
        if ok:
            passed += 1
        else:
            return passed, sample.text, reason

    return passed, "", ""


def slug(data: dict[str, Any]) -> str:
    """A file stem for this problem, from its title."""
    title = (data.get("title") or "problem").lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", title).strip("_")
    return cleaned or "problem"


def extract(
    data: dict[str, Any],
    model: Completer | None = None,
    max_attempts: int = DEFAULT_ATTEMPTS,
    workdir: Path | None = None,
    check_samples: bool = True,
    examples: tuple[str, ...] = prompts.EXAMPLES,
) -> Extraction:
    """Build an IR for one extracted statement, retrying against both gates.

    `examples` exists so the eval can leave a problem out of its own prompt.
    Scoring a model on a problem whose answer is sitting in its examples
    measures copying, not extraction.
    """
    model = model or Model()
    workdir = Path(workdir or Path(__file__).parent.parent / "build" / "extract")
    workdir.mkdir(parents=True, exist_ok=True)

    stem = slug(data)

    # A statement that shows an example instead of an input format has no stdin
    # format at all, so one has to be designed -- and once it is, the samples
    # that came with the statement are written in the OLD notation. "[2, 7, 4]
    # & Sum = 20" is not a test file for any format, and a validator built from
    # a perfectly correct IR will reject it.
    #
    # So the gate is not skipped because it is inconvenient. It is skipped
    # because there is nothing for it to check: ground truth does not exist for
    # a format that was invented a moment ago. Whether this applies is read off
    # the statement, never off anything the model claimed, so it cannot be
    # talked into being an escape hatch.
    designed = looks_like_an_example(str(data.get("input_format", "")))
    samples = (
        write_samples(data, workdir / stem) if check_samples and not designed else []
    )

    result = Extraction(samples_total=len(samples), designed_format=designed)
    current = prompts.build(data, examples)
    reply = ""

    for number in range(1, max_attempts + 1):
        reply = model(current)

        # --- parse -------------------------------------------------------
        try:
            envelope = json_object(reply)
            ir, reported = unwrap(envelope)
        except Echoed as exc:
            # Nothing in the reply is worth repairing, so the whole prompt goes
            # again rather than a note about what was wrong with it.
            result.attempts.append(Attempt(number, "parse", False, str(exc)))
            current = prompts.retry_after_echo(data, examples)
            continue
        except (ValueError, json.JSONDecodeError) as exc:
            result.attempts.append(Attempt(number, "parse", False, str(exc)))
            current = prompts.repair_schema(reply, str(exc))
            continue

        # --- schema gate -------------------------------------------------
        try:
            problem = Problem.model_validate(ir)
        except ValidationError as exc:
            detail = _first_error(exc)
            result.attempts.append(Attempt(number, "schema", False, detail))
            current = prompts.repair_schema(reply, str(exc))
            continue

        result.attempts.append(Attempt(number, "schema", True, "loads as a Problem"))

        # --- sample gate -------------------------------------------------
        if not samples:
            result.attempts.append(
                Attempt(number, "samples", True, "no samples to check against")
            )
            return _finish(result, problem, ir, reported, samples)

        try:
            passed, failing, reason = _gate_samples(problem, samples, stem, workdir)
        except (ValueError, NotImplementedError, CompileError) as exc:
            # The emitter refused this IR. That is the emitter working as
            # designed -- it raises rather than quietly skipping a rule it does
            # not understand -- and the message names what it could not do, so
            # it is worth one more attempt.
            detail = str(exc).strip().splitlines()[0][:200]
            result.attempts.append(Attempt(number, "samples", False, detail))
            current = prompts.repair_schema(reply, str(exc))
            continue

        result.samples_passed = passed

        if not failing:
            result.attempts.append(
                Attempt(number, "samples", True, f"{passed}/{len(samples)} accepted")
            )
            return _finish(result, problem, ir, reported, samples)

        result.attempts.append(
            Attempt(
                number,
                "samples",
                False,
                f"{passed}/{len(samples)} accepted: {reason[:120]}",
            )
        )
        current = prompts.repair_samples(reply, failing, reason)

    # Out of attempts. Whatever the last failure was, it is the reason.
    last = result.attempts[-1].detail if result.attempts else "no reply"
    result.blockers.append(
        Blocker(
            Severity.MISSING,
            f"no IR survived {max_attempts} attempts; last failure: {last}",
        )
    )
    return result


def _finish(
    result: Extraction,
    problem: Problem,
    ir: dict[str, Any],
    reported: list[Blocker],
    samples: list[Sample],
) -> Extraction:
    """Attach the IR and everything still outstanding about it."""
    result.problem = problem
    result.ir = ir
    result.blockers = reported + chosen_bounds(problem)

    if not samples:
        # Without samples the sample gate never ran, so the only thing known
        # about this IR is that it is well formed. That is worth saying out
        # loud, because a green run with no samples looks identical to a green
        # run with ten -- and the two reasons for it are not the same problem.
        result.blockers.append(
            Blocker(
                Severity.DECIDED,
                (
                    "the statement gave no input format, so one was designed "
                    "here; its samples are written in the statement's own "
                    "notation and cannot check the designed format"
                    if result.designed_format
                    else "the statement carried no usable sample input, so "
                    "this IR was never checked against a real test file"
                ),
                field="samples",
            )
        )

    return result


def _first_error(exc: ValidationError) -> str:
    """The one line of a pydantic error worth putting in a summary table."""
    errors = exc.errors()
    if not errors:
        return "invalid"
    first = errors[0]
    where = ".".join(str(part) for part in first.get("loc", ())) or "(root)"
    return f"{where}: {first.get('msg', 'invalid')}"[:200]


def save(
    problem: Problem,
    stem: str,
    directory: Path | None = None,
    force: bool = False,
) -> Path:
    """Write an extracted IR where the rest of the tools look for problems.

    This is what makes the pipeline continuous: once the file is here,
    `emit`, `plan`, `gentests` and `selftest` all work on the extracted problem
    exactly as they do on a hand-written one, with no special case anywhere.

    Which is also the danger, and why an existing file is never overwritten
    without being asked twice. The hand-written IR in this directory is what
    the eval scores against. Quietly replacing one with an extracted IR would
    destroy the baseline and make the next run look like an improvement.
    """
    directory = directory or PROBLEM_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.json"

    if path.exists() and not force:
        raise FileExistsError(
            f"{path} already exists. If it is a hand-written IR it is the "
            f"answer the eval scores against, so overwriting it would erase "
            f"the baseline. Pass --force to replace it, or --stem to save "
            f"under another name."
        )

    path.write_text(problem.model_dump_json(indent=2, exclude_none=True) + "\n")
    return path
