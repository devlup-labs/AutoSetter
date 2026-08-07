"""Command line entry point for the test generation tools.

The workflow for a new problem is:

    python -m testgen new next_round        write a blank IR to fill in
    ...edit testgen/ir/problems/next_round.json...
    python -m testgen emit next_round       look at the validator it produces
    python -m testgen build next_round      compile it
    python -m testgen check next_round f    run it against real input files

`check` is the one that matters. Point it at the samples that came with the
problem: a correct validator has to accept every one of them, and that is a
ground truth you get for free without labelling anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from testgen.build import accepts, build_generator, build_validator, generate
from testgen.emit.checker import CheckerNeedsHuman, describe as describe_checker, emit_checker
from testgen.emit.generator import emit_generator
from testgen.emit.io_contract import emit_io_contract
from testgen.emit.validator import emit_validator
from testgen.ir.problems import PROBLEM_DIR, load, load_all
from testgen.plan import describe, plan_tests

TEMPLATE = """{
  "name": "%(name)s",
  "source": "TODO where the problem came from, e.g. CF 158A",
  "multitest": false,
  "body": {
    "variables": [
      {
        "kind": "int",
        "name": "n",
        "domain": { "lo": 1, "hi": 100 },
        "source": "TODO quote the sentence this came from"
      }
    ],
    "lines": [
      { "tokens": ["n"] }
    ]
  },
  "global_constraints": [],
  "output": {
    "unique_answer": true,
    "case_insensitive": false,
    "float_eps": null,
    "tokens_per_test": 1
  }
}
"""


def cmd_list(args: argparse.Namespace) -> int:
    for stem, problem in load_all().items():
        shape = "multitest" if problem.multitest else "single"
        globals_ = len(problem.global_constraints)
        print(
            f"{stem:18} {problem.name:30} {shape:10} "
            f"{len(problem.body.variables)} vars, {globals_} global"
        )
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    path = PROBLEM_DIR / f"{args.problem}.json"
    if path.exists() and not args.force:
        print(f"{path} already exists, pass --force to overwrite", file=sys.stderr)
        return 1
    path.write_text(TEMPLATE % {"name": args.problem.replace("_", " ").title()})
    print(f"wrote {path}")
    print("fill in the variables, lines and constraints, then run:")
    print(f"    python -m testgen emit {args.problem}")
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    print(emit_validator(load(args.problem)), end="")
    return 0


def cmd_contract(args: argparse.Namespace) -> int:
    print(emit_io_contract(load(args.problem)))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    print(describe(load(args.problem)))
    return 0


def cmd_gen(args: argparse.Namespace) -> int:
    problem = load(args.problem)
    binary = build_generator(problem, args.problem)
    print(generate(binary, [args.mode, str(args.seed)]), end="")
    return 0


def cmd_gentests(args: argparse.Namespace) -> int:
    """Produce every planned test and confirm the validator accepts it."""
    problem = load(args.problem)
    generator = build_generator(problem, args.problem)
    validator = build_validator(problem, args.problem)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    failures = 0

    for test in plan_tests(problem):
        produced = generate(generator, test.args)
        ok, reason = accepts(validator, produced)
        path = outdir / f"{test.name}.txt"
        path.write_text(produced)
        if ok:
            print(f"  ok       {test.name:<18} -> {path}")
        else:
            failures += 1
            print(f"  INVALID  {test.name:<18} {reason}")

    print()
    print(f"{len(plan_tests(problem)) - failures} test(s) written to {outdir}")
    return 1 if failures else 0


def cmd_checker(args: argparse.Namespace) -> int:
    problem = load(args.problem)
    if args.decide:
        print(describe_checker(problem))
        return 0
    try:
        print(emit_checker(problem), end="")
    except (CheckerNeedsHuman, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    binary = build_validator(load(args.problem), args.problem)
    print(f"built {binary}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    binary = build_validator(load(args.problem), args.problem)
    failures = 0

    for name in args.files:
        path = Path(name)
        if not path.exists():
            print(f"  missing  {path}")
            failures += 1
            continue

        ok, reason = accepts(binary, path.read_text())
        if ok:
            print(f"  valid    {path.name}")
        else:
            failures += 1
            print(f"  INVALID  {path.name}: {reason}")

    print()
    print(f"{len(args.files) - failures}/{len(args.files)} accepted")
    return 1 if failures else 0


def cmd_selftest(args: argparse.Namespace) -> int:
    from testgen.selftest import run

    return run()


def main() -> int:
    parser = argparse.ArgumentParser(prog="testgen", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show the problems that have an IR").set_defaults(
        func=cmd_list
    )

    new = sub.add_parser("new", help="scaffold a blank IR for a new problem")
    new.add_argument("problem")
    new.add_argument("--force", action="store_true", help="overwrite an existing file")
    new.set_defaults(func=cmd_new)

    emit = sub.add_parser("emit", help="print the validator source")
    emit.add_argument("problem")
    emit.set_defaults(func=cmd_emit)

    contract = sub.add_parser(
        "contract", help="print the input format description for a prompt"
    )
    contract.add_argument("problem")
    contract.set_defaults(func=cmd_contract)

    plan = sub.add_parser("plan", help="show which tests the constraints call for")
    plan.add_argument("problem")
    plan.set_defaults(func=cmd_plan)

    gen = sub.add_parser("gen", help="run the generator once and print the test")
    gen.add_argument("problem")
    gen.add_argument("mode", nargs="?", default="random")
    gen.add_argument("seed", nargs="?", type=int, default=1)
    gen.set_defaults(func=cmd_gen)

    gentests = sub.add_parser(
        "gentests", help="write every planned test, checking each with the validator"
    )
    gentests.add_argument("problem")
    gentests.add_argument("--outdir", default="build/tests")
    gentests.set_defaults(func=cmd_gentests)

    checker = sub.add_parser("checker", help="emit or choose the checker")
    checker.add_argument("problem")
    checker.add_argument(
        "--decide", action="store_true", help="say which checker is needed and why"
    )
    checker.set_defaults(func=cmd_checker)

    build = sub.add_parser("build", help="compile the validator")
    build.add_argument("problem")
    build.set_defaults(func=cmd_build)

    check = sub.add_parser("check", help="run the validator against input files")
    check.add_argument("problem")
    check.add_argument("files", nargs="+", help="test input files, e.g. the samples")
    check.set_defaults(func=cmd_check)

    sub.add_parser("selftest", help="run the mutation suite").set_defaults(
        func=cmd_selftest
    )

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
