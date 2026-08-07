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

The validator came first. It is the most mechanical of the three, and it is the
only one with free ground truth: every problem ships official samples, and a
correct validator must accept all of them. That made it the cheapest way to find
out whether the IR was right before anything expensive depended on it.

1. Constraint IR schema — done
2. Hand-written IR for the reference problems — done
3. Validator emitter — done
4. Test plan derivation — done
5. Generator emitter — done
6. Checker selection and emitter — done
7. Statement to IR extraction — not started

## How the three tools check each other

None of the three is trusted on its own.

**The generator is checked by the validator.** They are built from the same
constraints but run in opposite directions: the generator finds an input that
satisfies them, the validator judges whether an input does. If either has
misread the IR, they disagree, and neither could have discovered that alone.
Every test the generator produces is fed to the validator in the self test.

**The validator is checked by mutation.** A valid input is broken on purpose in
one specific way — a value pushed past its bound, a token removed, a tab put
where a space belongs, an order reversed — and the validator has to reject each
one. A validator that only ever sees correct input proves nothing.

**The checker is checked by verdict.** It is compiled and run against a right
answer, a wrong answer, an empty file and an output with extra tokens, and each
has to produce the expected verdict rather than merely not crashing.

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
  build.py          emit and compile the three tools, and run them
  plan.py           which tests the constraints call for
  selftest.py       the whole suite: validators, generators, checkers
  inspect_statement.py   report whether an extracted statement is usable
  testlib.h         vendored, so the emitted C++ compiles out of the box
  emit/
    validator.py    IR -> testlib validator
    generator.py    IR -> testlib generator
    checker.py      IR -> checker, or the name of the stock one to use
    io_contract.py  IR -> input format description for a solution prompt
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
| `plan <problem>` | show which tests the constraints call for, and why each exists |
| `gen <problem> [mode] [seed]` | run the generator once and print the test |
| `gentests <problem>` | write every planned test, validating each one |
| `checker <problem> [--decide]` | emit the checker, or say which one is needed |
| `contract <problem>` | print the input format description for a solution prompt |
| `selftest` | compile everything and run the whole suite |

### Generator modes

`gen` and `gentests` take a mode, which selects a shape rather than a size.

| Mode | Produces |
|---|---|
| `min` | every value at its lower bound |
| `max` | every value at its upper bound |
| `flat` | every element of an array the same |
| `sorted` / `reversed` | an array put in order, when the statement did not fix one |
| `budget_one_big` | a sum across test cases spent in one large case |
| `budget_many_small` | the same sum spent across as many tiny cases as allowed |
| `budget_skewed` | one large case and the rest minimal |
| `random` | uniform random, varied by seed |

The three budget modes exist because a sum across test cases has no single
largest test. The same total can be one enormous case or ten thousand tiny ones,
and those break different things: the first stresses the algorithm, the second
stresses whatever the solution does once per test case, such as clearing a
global array.

### Choosing a checker

`checker --decide` reports which checker the problem needs.

| Output | Checker |
|---|---|
| one exact answer | testlib's stock `wcmp.cpp`, nothing generated |
| letter case irrelevant | generated, since no stock checker does it |
| real numbers with a tolerance | generated, using the tolerance from the IR |
| several correct answers | **refused** |

The last row is deliberate. If more than one answer is correct, judging one
needs the rules of the problem rather than the shape of its output, and that
cannot be read off the IR. Generating something anyway would produce a checker
that rejects correct submissions, so it raises instead and says to write it by
hand.

### The input format contract

`contract` prints the input format in words, for pasting into the prompt that
asks a model for a solution. A prose statement does not say how input arrives,
so a model tends to produce a function taking arguments with a hardcoded example
in `main`, which cannot be run against a test file at all. Because the contract
comes from the same IR as the validator and the generator, all three agree on
the format without anyone keeping copies of it in step.

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
