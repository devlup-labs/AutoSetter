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
import subprocess
import sys
from pathlib import Path

from testgen.emit.validator import emit_validator
from testgen.ir.problems import PROBLEM_DIR, load, load_all

BUILD_DIR = Path(__file__).parent.parent / "build"
TESTLIB_DIR = Path(__file__).parent

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


def cmd_build(args: argparse.Namespace) -> int:
    binary = build_validator(args.problem)
    print(f"built {binary}")
    return 0


def build_validator(problem: str) -> Path:
    """Emit and compile the validator, returning the path to the binary."""
    BUILD_DIR.mkdir(exist_ok=True)
    source = BUILD_DIR / f"{problem}_validator.cpp"
    source.write_text(emit_validator(load(problem)))

    binary = BUILD_DIR / f"{problem}_validator"
    result = subprocess.run(
        ["g++", "-O2", "-o", str(binary), str(source), "-I", str(TESTLIB_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"{problem} did not compile:\n{result.stderr}")
    return binary


def cmd_check(args: argparse.Namespace) -> int:
    binary = build_validator(args.problem)
    failures = 0

    for name in args.files:
        path = Path(name)
        if not path.exists():
            print(f"  missing  {path}")
            failures += 1
            continue

        result = subprocess.run(
            [str(binary)], input=path.read_text(), capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  valid    {path}")
        else:
            failures += 1
            message = (result.stderr or result.stdout).strip().splitlines()
            reason = message[0] if message else "rejected"
            print(f"  INVALID  {path}: {reason}")

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
