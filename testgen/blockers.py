"""What stood between an extracted statement and a usable IR.

`adapt.py` reported its gaps as a flat list of sentences, which was honest but
not actionable: the caller could see that something was wrong and had no way to
tell what to do about it. These two are both "gaps", and they have opposite
consequences:

    the line layout is a guess          a person fixes it in thirty seconds
    the input is a graph                nobody can fix it without a new schema

The first should not stop a pipeline. The second must, because everything
downstream would be built from an IR that does not describe the problem.

So a gap carries a severity, and the severity decides what happens next.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    """What the caller is supposed to do about a blocker.

    Ordered by how much they stop: DECIDED lets the pipeline run, MISSING and
    INEXPRESSIBLE do not.
    """

    #: Something the statement did not say, which we picked so work could
    #: continue. Bounds are the usual one. The pipeline runs; the package is
    #: marked as containing a decision nobody has confirmed.
    DECIDED = "decided"

    #: Something needed that is absent from the statement and cannot be chosen
    #: sensibly. Extraction stops rather than inventing it.
    MISSING = "missing"

    #: The problem is outside what the IR can describe: a graph, a grid, a
    #: relational guarantee like "at least one pair sums to k". No amount of
    #: prompting fixes this, and no human can fix it in the JSON either. The
    #: caller falls back to the LLM-written C++ path.
    INEXPRESSIBLE = "inexpressible"


@dataclass(frozen=True)
class Blocker:
    """One thing that stood in the way, and what to do about it.

    `detail` is meant to be read by a person, so it says which part of the
    statement the trouble was in rather than naming an internal field.
    """

    severity: Severity
    detail: str
    #: The IR field this is about, when there is one. Lets a caller point a
    #: reviewer straight at what to look at.
    field: str | None = None

    def __str__(self) -> str:
        where = f" [{self.field}]" if self.field else ""
        return f"{self.severity.value.upper()}{where}: {self.detail}"


class Verdict(str, Enum):
    """What the caller should do with an extraction result."""

    #: Every constraint was read from the statement. Nothing to confirm.
    READY = "ready"

    #: Usable, but something in it was decided rather than read. The pipeline
    #: may run; the manifest has to say so.
    REVIEW = "review"

    #: Not usable. Fall back to the LLM-written C++ path for this problem.
    FALLBACK = "fallback"


def verdict(blockers: list[Blocker]) -> Verdict:
    """Reduce a list of blockers to the one decision a caller has to make.

    The worst blocker wins, because a single inexpressible construct makes the
    whole IR wrong however much of the rest was read correctly.
    """
    severities = {b.severity for b in blockers}
    if Severity.INEXPRESSIBLE in severities or Severity.MISSING in severities:
        return Verdict.FALLBACK
    if Severity.DECIDED in severities:
        return Verdict.REVIEW
    return Verdict.READY


def describe(blockers: list[Blocker]) -> str:
    """A readable report, grouped so the stopping ones are read first."""
    if not blockers:
        return "  nothing outstanding: every constraint was read from the statement"

    order = [Severity.INEXPRESSIBLE, Severity.MISSING, Severity.DECIDED]
    headings = {
        Severity.INEXPRESSIBLE: "OUTSIDE THE IR  (fall back to the LLM path)",
        Severity.MISSING: "MISSING          (not in the statement, not guessable)",
        Severity.DECIDED: "DECIDED          (we chose this; confirm before release)",
    }

    out: list[str] = []
    for severity in order:
        group = [b for b in blockers if b.severity is severity]
        if not group:
            continue
        out.append(f"  {headings[severity]}")
        for blocker in group:
            where = f" [{blocker.field}]" if blocker.field else ""
            out.append(f"    - {blocker.detail}{where}")
        out.append("")

    return "\n".join(out).rstrip()
