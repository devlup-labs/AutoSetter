# Test Generation — Generator, Validator, Checker

Stage 6 of the AutoSetter pipeline: turning a parsed problem statement into a
Codeforces/Polygon test package.

Three deliverables, one idea. A problem statement is a set of **constraints**, and
each tool does something different with the same set:

| Tool | What it does with the constraints |
|---|---|
| **Generator** | *solves* them — find an input that satisfies them |
| **Validator** | *evaluates* them — check whether a given input satisfies them |
| **Checker** | uses the input format to parse the input, then judges the output |

Because all three read from the same source, they are generated from a single
**constraint IR** rather than written by hand three times. Hand-written tools drift
apart when a statement changes, and a validator that disagrees with the generator
is worse than no validator at all — it gives false confidence.

```
problem statement
       |
       v   (extraction — LLM, Stage 3)
  constraint IR
       |
   +---+--------------+----------------+
   v                  v                v
 solve(IR)     evaluate(IR, x)     format(IR)
   |                  |                |
   v                  v                v
GENERATOR         VALIDATOR      CHECKER's parser
```

## Build order

The validator comes first. It is the most mechanical of the three, and it is the
only one with free ground truth: every problem ships official samples, and a
correct validator must accept all of them. That makes it the cheapest way to find
out whether the IR is right before anything expensive depends on it.

1. Constraint IR schema
2. Hand-written IR for three reference problems
3. Validator emitter
4. Test plan derivation
5. Generator emitter
6. Checker selection
7. Statement to IR extraction

## Reference problems

Three problems, chosen so that between them they cover every kind of constraint.
Each is the simplest problem that demonstrates its lesson.

| Problem | Constraints | What it adds |
|---|---|---|
| Watermelon (CF 4A) | `1 <= w <= 100` | a single scalar range, nothing else |
| Theatre Square (CF 1A) | `1 <= n, m, a <= 10^9` | several scalars on one line |
| Max of array | `t <= 10^4`, `sum of n <= 2*10^5` | multitest, arrays, global sum |

The third is the interesting one. `sum of n over all test cases <= 2*10^5` is not a
property of any single variable, so it cannot be checked while reading one test
case. It needs an accumulator and a check after the loop, and it is the constraint
most validators get wrong.

## Layout

```
testgen/
  __main__.py       command line entry point
  build.py          emit and compile a validator
  selftest.py       accept and reject checks for every emitter path
  testlib.h         vendored, so the emitted C++ compiles out of the box
  emit/
    validator.py    IR -> testlib validator
  ir/
    schema.py       constraint IR definitions
    problems/       hand-written IR for the reference problems
```

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Adding a problem

```
python -m testgen new next_round
```

That writes a blank IR to `testgen/ir/problems/next_round.json`. Fill it in from
the statement, quoting the sentence each constraint came from in its `source`
field so the extraction step has something to be checked against later. Then:

```
python -m testgen emit next_round              # read the validator it produces
python -m testgen check next_round samples/*   # compile it and run it on files
```

Point `check` at the samples that shipped with the problem. A correct validator
has to accept every one of them, which is a ground truth that costs nothing to
obtain. Then feed it deliberately broken files and confirm each is refused.

## Commands

| Command | What it does |
|---|---|
| `list` | show every problem that has an IR |
| `new <problem>` | scaffold a blank IR to fill in |
| `emit <problem>` | print the validator source |
| `build <problem>` | compile the validator into `build/` |
| `check <problem> <files...>` | compile, then run it against real input files |
| `selftest` | compile every validator and run the accept and reject suite |

## What the IR can express

| | Example |
|---|---|
| scalar range | `1 <= w <= 100` |
| dependent range | `1 <= k <= n`, written as `"hi": "n"` |
| array with element range | `n` integers, each `0 <= a_i <= 100` |
| array order | `"monotone": "non_increasing"` |
| distinct values | `"distinct": true` |
| string over an alphabet | `"alphabet": "01"` with a length |
| sum across test cases | `"kind": "sum_over_tests"` |

Anything not in this table is not checked, and the emitter is deliberately not
clever about it: a variable kind it does not understand raises rather than
quietly emitting a validator that skips the constraint. A validator that misses
a rule is worse than no validator, because it looks like it passed.
