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
  ir/
    schema.py         constraint IR definitions
    problems/         hand-written IR for the reference problems
```

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
