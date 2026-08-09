# AutoSetter

Turns a problem statement image into a Codeforces/Polygon-style problem package:
statement, reference solution, validator, generator, checker, tests and a
validation report — driven by local Qwen models through Ollama.

```
statement image
      |  Qwen-VL
      v
 problem.json  ──►  statement.md, solution.cpp, validator.cpp,
      |             generator.cpp, checker.cpp     (one Ollama call each)
      v
  validate  (compile, generate, validate, solve, check)
      v
  package/  ready for human review
```

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Needs `g++` on PATH and a local Ollama server with a vision model and a text
model pulled.

## Running

```
python app.py path/to/statement.png
python app.py statement.pdf --num-tests 20 --skip-validation
```

Everything a run produces goes under `out/`, which is the only directory the
repo ignores:

```
out/
  problem.json          extracted specification
  generated/            statement.md and the four .cpp artifacts
  tests/                001.in / 001.ans, plus rejected/ for what didn't make it
  package/              the bundle, with manifest.json
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | the package is fit to release |
| 1 | the pipeline failed outright |
| 2 | artifacts were produced, but the package is **not** fit to release |

Code 2 is the interesting one. A package is only "fit to release" when every
generated test passed, the validator agrees with the problem's own samples, and
the checker demonstrably rejects wrong output. Read `manifest.json`'s
`ready_for_release` and the report's `diagnosis` field, which names what is
wrong in one sentence.

Set `AUTOSETTER_DEBUG=1` to see the raw model replies.

## How the pipeline avoids believing itself

The four generated C++ files all come from one model reading one JSON, so they
can agree with each other and all be wrong together. Three checks exist to stop
that:

- **The samples check the validator.** Every problem ships samples, and a
  correct validator must accept all of them. This is ground truth that costs
  nothing, and it is what makes a later disagreement attributable: if the
  validator accepts the samples and rejects a generated test, the generator is
  at fault, and the report says so in those words.
- **The validator checks the generator.** Every generated test is fed to the
  validator, and any test it rejects is kept out of the package rather than
  shipped without an answer file.
- **Wrong output checks the checker.** Running a checker on the reference
  answer only ever asks whether `x == x`. Each run also hands it an empty file,
  a truncated answer and a perturbed answer, and requires a rejection. A checker
  that reads both files and never compares them passes every other test in the
  pipeline and fails this one.

## Tests

```
pip install -r requirements-dev.txt
pytest
```

The Ollama call sits behind a single class, so most tests need no model
running. The pipeline tests compile a real validator, generator, solution and
checker against testlib and skip if `g++` is missing.

## Sandbox

`sandbox/` holds the Docker + nsjail execution server used to run untrusted C++.

```
bash sandbox/scripts/build.sh    # builds the autosetter-nsjail image
bash sandbox/scripts/start.sh
```

Containment and resource limits live in `sandbox/server/src/config.js`. Workers
run without `--privileged`, with memory, CPU, PID and network limits, and both
the compile and the run happen inside nsjail. If nsjail cannot create
namespaces on your host, `SANDBOX_PRIVILEGED=1` restores the old behaviour —
temporarily, while you work out which capability is missing.

`third_party/testlib.h` is the one vendored copy every stage compiles against.
