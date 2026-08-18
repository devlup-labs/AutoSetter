"""Measure how good the extraction actually is.

"It worked when I tried it" is not a claim you can put in front of anyone. The
difference between a demo and something that runs unattended is a number that
goes up, so this produces one.

Two measures, because they answer different questions and need different things:

    agreement   compare the extracted IR field by field against the IR a person
                wrote for the same problem. Needs a hand-written answer to
                compare against, and says how close the model got to it.

    samples     emit a validator from the extracted IR and run it on the
                problem's own samples. Needs nothing but the statement, so it
                works on every problem, including ones nobody has written an IR
                for. It cannot tell you the bounds are right -- only that they
                are not provably wrong.

Agreement is the stricter measure and the scarcer one. Samples is the measure
that scales, and it is the one the pipeline itself gates on.

A disagreement is not automatically the model's fault, and the report says
which side is which rather than assuming. A hand-written IR can be the one that
is wrong, and finding that out is worth the run on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from testgen import prompt as prompts
from testgen.blockers import Verdict
from testgen.extract import Extraction, extract
from testgen.ir.problems import PROBLEM_DIR, load
from testgen.ir.schema import ArrayVar, IntVar, Problem, StringVar
from testgen.llm import Completer

# Extracted statements are named after the problem they came from, with this
# on the end, so a gold IR can be found for one without a second index.
SUFFIX = "_extracted.json"
DEFAULT_STATEMENTS = Path(__file__).parent.parent / "samples"


@dataclass(frozen=True)
class Pair:
    """An extracted statement, and the hand-written IR for it if there is one."""

    stem: str
    statement: Path
    gold: Path | None

    @property
    def has_gold(self) -> bool:
        return self.gold is not None


def pairs(
    directory: Path = DEFAULT_STATEMENTS, problems: Path = PROBLEM_DIR
) -> list[Pair]:
    """Every extracted statement on disk, matched to its gold IR by name."""
    found: list[Pair] = []
    for path in sorted(directory.glob(f"*{SUFFIX}")):
        stem = path.name[: -len(SUFFIX)]
        gold = problems / f"{stem}.json"
        found.append(Pair(stem, path, gold if gold.exists() else None))
    return found


@dataclass
class Agreement:
    """Field by field comparison of an extracted IR against a written one."""

    matched: int = 0
    total: int = 0
    mismatches: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.matched / self.total if self.total else 0.0

    def check(self, label: str, got: Any, want: Any) -> None:
        self.total += 1
        if got == want:
            self.matched += 1
        else:
            self.mismatches.append(f"{label}: got {got!r}, written {want!r}")


def _domain(domain: Any) -> tuple[Any, Any]:
    """A domain reduced to what a comparison cares about.

    `origin` is deliberately excluded: whether a bound was read or chosen is a
    statement about provenance, and the hand-written files predate the field.
    """
    return (domain.lo, domain.hi)


def _variables(problem: Problem) -> dict[str, Any]:
    return {var.name: var for var in problem.body.variables}


def compare(got: Problem, want: Problem) -> Agreement:
    """How much of a written IR did the extraction reproduce?"""
    agreement = Agreement()

    agreement.check("multitest", got.multitest, want.multitest)

    if want.test_count or got.test_count:
        agreement.check(
            "test_count",
            _domain(got.test_count.domain) if got.test_count else None,
            _domain(want.test_count.domain) if want.test_count else None,
        )

    mine, theirs = _variables(got), _variables(want)
    agreement.check("variable names", sorted(mine), sorted(theirs))

    for name in sorted(set(mine) & set(theirs)):
        a, b = mine[name], theirs[name]
        agreement.check(f"{name}.kind", a.kind, b.kind)
        if a.kind != b.kind:
            continue
        if isinstance(b, IntVar):
            agreement.check(f"{name}.domain", _domain(a.domain), _domain(b.domain))
        elif isinstance(b, ArrayVar):
            agreement.check(f"{name}.length", a.length, b.length)
            agreement.check(f"{name}.elem", _domain(a.elem), _domain(b.elem))
            agreement.check(f"{name}.monotone", a.monotone, b.monotone)
            agreement.check(f"{name}.distinct", a.distinct, b.distinct)
        elif isinstance(b, StringVar):
            agreement.check(f"{name}.length", a.length, b.length)
            agreement.check(f"{name}.alphabet", a.alphabet, b.alphabet)

    agreement.check(
        "lines",
        [line.tokens for line in got.body.lines],
        [line.tokens for line in want.body.lines],
    )
    agreement.check(
        "global constraints",
        sorted((c.var, c.op, c.value) for c in got.global_constraints),
        sorted((c.var, c.op, c.value) for c in want.global_constraints),
    )
    agreement.check("output.unique", got.output.unique_answer, want.output.unique_answer)
    agreement.check(
        "output.case_insensitive",
        got.output.case_insensitive,
        want.output.case_insensitive,
    )

    return agreement


@dataclass
class Row:
    """What happened for one problem."""

    stem: str
    extraction: Extraction
    agreement: Agreement | None = None
    #: Whether a hand-written IR existed at all. Kept separately from
    #: `agreement`, because "there was nothing to compare against" and "there
    #: was, but no IR came out to compare" are different results and reporting
    #: them the same way hides a failure behind a missing file.
    had_gold: bool = False

    @property
    def verdict(self) -> str:
        return self.extraction.verdict.value

    @property
    def samples(self) -> str:
        total = self.extraction.samples_total
        if not total:
            return "none"
        return f"{self.extraction.samples_passed}/{total}"

    @property
    def agreement_text(self) -> str:
        if self.agreement is not None:
            return f"{self.agreement.matched}/{self.agreement.total}"
        return "not scored" if self.had_gold else "no gold"


def run(
    model: Completer | None = None,
    directory: Path = DEFAULT_STATEMENTS,
    max_attempts: int = 4,
    only: str | None = None,
) -> list[Row]:
    """Extract every statement on disk and score what came out."""
    rows: list[Row] = []

    for pair in pairs(directory):
        if only and pair.stem != only:
            continue

        data = json.loads(pair.statement.read_text())

        # Leave this problem out of its own prompt. Four of the hand-written
        # IR files are the worked examples, and two of them are also scored
        # here -- so without this the model is shown the answer and the score
        # measures copying rather than extraction. Every problem is scored
        # against a prompt that has never seen it.
        examples = tuple(name for name in prompts.EXAMPLES if name != pair.stem)

        extraction = extract(
            data, model=model, max_attempts=max_attempts, examples=examples
        )

        agreement = None
        if pair.has_gold and extraction.problem is not None:
            agreement = compare(extraction.problem, load(pair.stem))

        rows.append(Row(pair.stem, extraction, agreement, had_gold=pair.has_gold))

    return rows


def report(rows: list[Row]) -> Iterator[str]:
    """A table, then the detail for anything that did not line up."""
    if not rows:
        yield "  no extracted statements found"
        return

    width = max(len(row.stem) for row in rows)

    yield ""
    yield (
        f"  {'PROBLEM':<{width}}  {'VERDICT':<12}  {'SAMPLES':<8}  "
        f"{'AGREEMENT':<10}  ATTEMPTS"
    )
    yield f"  {'-' * width}  {'-' * 12}  {'-' * 8}  {'-' * 10}  {'-' * 8}"

    for row in rows:
        yield (
            f"  {row.stem:<{width}}  {row.verdict:<12}  {row.samples:<8}  "
            f"{row.agreement_text:<10}  {len(row.extraction.attempts)}"
        )

    yield ""

    for row in rows:
        detail = list(_detail(row))
        if detail:
            yield f"  --- {row.stem} ---"
            yield from detail
            yield ""


def _detail(row: Row) -> Iterator[str]:
    for attempt in row.extraction.attempts:
        if not attempt.ok:
            yield f"    {attempt}"
    if row.agreement and row.agreement.mismatches:
        yield "    disagrees with the hand-written IR:"
        for mismatch in row.agreement.mismatches:
            yield f"      - {mismatch}"
    for blocker in row.extraction.blockers:
        yield f"    {blocker}"


def totals(rows: list[Row]) -> str:
    """One line worth quoting: how many made it, and how close they were."""
    usable = sum(1 for r in rows if r.extraction.verdict is not Verdict.FALLBACK)
    scored = [r.agreement for r in rows if r.agreement is not None]
    matched = sum(a.matched for a in scored)
    total = sum(a.total for a in scored)

    parts = [f"{usable}/{len(rows)} produced a usable IR"]
    if total:
        parts.append(f"{matched}/{total} fields agree with the hand-written IR")
    return "  " + ", ".join(parts)
