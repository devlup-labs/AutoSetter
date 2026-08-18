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
7. Statement to IR extraction — done

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

## Getting the IR without writing it

Everything above starts from the IR, and for a long time the IR was written by
hand. `extract` is the step that removes the person: an extracted statement goes
in, a constraint IR comes out.

A model is the only thing that can read prose, so a model does that part. The
part worth describing is what happens to what it says, because nothing here
takes the reply on trust.

```
extracted problem.json
        |
        v   prompt: schema + worked examples + the statement
   candidate IR  <-------------------------------+
        |                                        |
        v  schema gate: does it load?            | the reason, written
        |  no ------------------------------->---+  back into a new prompt
        v  yes
        |  sample gate: does a validator built
        |  from it accept the problem's own samples?
        |  no ------------------------------->---+
        v  yes
      IR + blockers
```

**The schema gate is free.** `Problem` already refuses a bound naming a
variable the statement never mentioned, an array whose length is not a real
name, a multitest problem with no test count. The pydantic error names the
field, and that error is what gets sent back.

**The sample gate is the one that matters.** The samples shipped with the
statement are known to be legal, so a validator emitted from the candidate IR
that rejects one proves the IR is wrong. No labelling, no judgement, no human.
It catches what the schema cannot see: a bound that is too tight, a line layout
that does not match the real file.

Feeding a failure back is the same idea as handing compiler errors to a model
writing C++, with one difference that decides whether it is worth anything: a
compiler error says the code parses, and these say the constraints are right.

### When the statement has no input format

A LeetCode style statement does not describe its input. It shows an example
where the format should be:

```
Input = [2, 7, 4, 0, 9, 5, 1, 3] & Sum = 20
```

There is no `n` anywhere in that problem, no line structure, and no bounds. The
input format does not exist yet and has to be **designed**: read a size, then
the values, and pick bounds that admit the intended solution and exclude the
brute force one. That is a setter's decision, and the IR records it as one —
every domain comes out with `"origin": "chosen"`.

The consequence is easy to miss and it breaks the sample gate. Once the format
is designed, the samples that shipped with the statement are written in the
*old* notation. `[2, 7, 4] & Sum = 20` is not a test file for any format, so a
validator built from a perfectly correct IR rejects it. Running the gate anyway
would fail every problem of this kind for a reason that says nothing about the
IR.

So for these statements the gate is skipped — not because it is inconvenient,
but because there is nothing for it to check. Ground truth does not exist for a
format invented a moment ago. Whether this applies is read off the statement by
`looks_like_an_example`, never off anything the model claimed, so it cannot be
talked into being an escape hatch. The result comes back `review`, with a
blocker saying the IR was never checked against a real test file and why.

Converting the samples into the designed format would restore the gate, and is
the obvious next thing to build.

### The prompt is generated

`prompt.py` builds the prompt from `Problem.model_json_schema()` and from the
hand-written IR files in `ir/problems/`. Nothing describes the IR twice. Widen
the schema and the prompt widens with it, which is the only way a description
of a format and the format itself stay in step.

### What comes out, and what may be done with it

An IR alone is not an answer. Extraction also returns blockers, and their worst
severity decides what the caller is allowed to do:

| Verdict | Cause | What happens |
|---|---|---|
| `ready` | every constraint was read from the statement | run unattended |
| `review` | something was decided rather than read | run, but the package records it |
| `fallback` | the IR cannot describe this problem | use the LLM-written C++ path |

The middle row is the interesting one, and it is why `origin` exists on a
domain. A statement that says `1 <= n <= 2*10^5` states its bounds. A LeetCode
style statement states none, and somebody has to choose them before a validator
can exist — `n <= 500` and `n <= 10^5` are different problems with different
intended solutions. Both end up as numbers in the same field, so without
`origin` a chosen bound and a read one are indistinguishable, and the pipeline
would ship invented difficulty with nothing anywhere saying so.

Marking it costs no automation. It only means the package knows which of its
bounds nobody has confirmed.

### Measuring it

`eval` extracts every statement in `samples/` and scores the result two ways:

| Measure | Needs | Says |
|---|---|---|
| agreement | a hand-written IR for the same problem | how close the model got to it |
| samples | nothing but the statement | whether the IR is provably wrong |

Agreement is stricter and scarcer. Samples is the one that scales to problems
nobody has written an IR for, and it is what the pipeline itself gates on.

A disagreement is not automatically the model's fault. A hand-written IR can be
the wrong one, and the report says which side is which rather than assuming.

**Each problem is left out of its own prompt.** Four of the IR files in
`ir/problems/` are the worked examples, and some of those problems are also the
ones being scored. Without leaving them out, the model is handed the answer and
the score measures copying. The first run of this eval had exactly that flaw and
reported a perfect number because of it.

The eval is small — two matched pairs — and a number from two problems is a
direction, not a result. It grows by adding an extracted `problem.json` beside a
hand-written IR of the same name.

**Agreement means less on a designed format than on a stated one.** When the
statement gives the input format, there is one right answer and disagreeing with
it is a mistake. When the format had to be designed, the hand-written IR is one
valid design among several: calling the array `arr` instead of `a`, or putting
the values on one line instead of two, scores as a disagreement and is not
wrong. For those problems the number to watch is whether a usable IR came out at
all, and whether the bounds are sensible — not how closely it matched a choice
somebody else made.

## Reference problems

Three problems, chosen so that between them they cover every kind of constraint.
Each is the simplest problem that demonstrates its lesson.

| Problem | Constraints | What it adds |
|---|---|---|
| Watermelon (CF 4A) | `1 <= w <= 100` | a single scalar range, nothing else |
| Theatre Square (CF 1A) | `1 <= n, m, a <= 10^9` | several scalars on one line |
| Max of array | `t <= 10^4`, `sum of n <= 2*10^5` | multitest, arrays, global sum |
| Two Sum (LC 1) | none stated | a statement with no input format at all |

The third is the interesting one for the validator. `sum of n over all test cases
<= 2*10^5` is not a property of any single variable, so it cannot be checked while
reading one test case. It needs an accumulator and a check after the loop, and it
is the constraint most validators get wrong.

The fourth is the interesting one for extraction, and it is the only reference
problem that is not a competitive programming problem at all. Two Sum states no
input format and no bounds — it is a function signature and an example — so
every bound in its IR is marked `"origin": "chosen"` with the reasoning written
into `source`. It is here because a model shown only statements that describe
their input will not attempt to design one: handed a LeetCode statement with no
example of the kind, qwen3:4b returns the statement unchanged, five times out of
five. Since AutoSetter exists to port OA problems, that is the class that
matters most.

## Layout

```
testgen/
  __main__.py       command line entry point
  build.py          emit and compile the three tools, and run them
  plan.py           which tests the constraints call for
  solve.py          the solver: choose the shape of a test
  selftest.py       the whole suite: validators, generators, checkers
  inspect_statement.py   report whether an extracted statement is usable
  adapt.py          statement -> draft IR by regular expression
  extract.py        statement -> IR with a model, gated and repaired
  prompt.py         the extraction prompt, generated from the schema
  blockers.py       what stood in the way, and what to do about it
  samples.py        get the problem's own samples onto disk
  llm.py            talk to a local model
  eval.py           score extraction against the hand-written IR
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
| `extract <file>` | build an IR from an extracted statement with a model |
| `eval` | extract every statement in `samples/` and score the results |

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
| where a bound came from | `"origin": "chosen"` on a domain |

Anything not in this table is not checked, and the emitter is deliberately not
clever about it: a variable kind it does not understand raises rather than
quietly emitting a validator that skips the constraint. A validator that misses
a rule is worse than no validator, because it looks like it passed.
