"""Get the problem's own samples out of the extracted JSON and onto disk.

The samples are the only free ground truth in this whole pipeline. They shipped
with the statement, they are known to be legal input, and a validator that
rejects one is wrong -- no labelling, no judgement, no human. That makes them
the gate that decides whether an extracted IR is any good.

They arrive as strings inside `problem.json`, and the validator is a compiled
program that reads a file, so something has to write them out. That is all this
does, plus the one piece of tidying the next paragraph explains.

testlib is deliberately strict about whitespace: `readEoln()` means exactly one
newline and `readEof()` means nothing at all afterwards. A sample transcribed
into JSON has usually lost its trailing newline, or picked up carriage returns,
or gained indentation from whoever formatted the file. None of that is a
property of the problem, so it is normalised away here rather than being
reported as a validator failure -- otherwise every extraction would look broken
for a reason that has nothing to do with the IR.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Sample:
    """One sample test case, as text and as the files it was written to."""

    index: int
    text: str
    answer: str
    path: Path | None = None

    @property
    def name(self) -> str:
        return f"sample_{self.index:03d}"


def normalise(text: str) -> str:
    """Put a sample into the exact shape a testlib validator will accept.

    Carriage returns removed, trailing whitespace on every line removed, blank
    lines at the end removed, then exactly one newline at the end.
    """
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def read(data: dict[str, Any]) -> list[Sample]:
    """Pull the samples out of an extracted problem, normalised.

    Samples with an empty input are dropped: a statement whose sample block was
    illegible produces those, and an empty file tells the gate nothing.
    """
    raw = data.get("samples") or []
    samples: list[Sample] = []

    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        text = normalise(str(entry.get("input", "")))
        if not text:
            continue
        samples.append(
            Sample(
                index=index,
                text=text,
                answer=normalise(str(entry.get("output", ""))),
            )
        )

    return samples


def write(data: dict[str, Any], outdir: Path) -> list[Sample]:
    """Write every usable sample to `outdir` and return them with their paths."""
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Sample] = []

    for sample in read(data):
        path = outdir / f"{sample.name}.in"
        path.write_text(sample.text)
        if sample.answer:
            (outdir / f"{sample.name}.ans").write_text(sample.answer)
        written.append(
            Sample(sample.index, sample.text, sample.answer, path)
        )

    return written
