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
import os
import sys
from pathlib import Path

from testgen.build import accepts, build_generator, build_validator, generate
from testgen.emit.checker import CheckerNeedsHuman, describe as describe_checker, emit_checker
from testgen.emit.generator import emit_generator
from testgen.emit.io_contract import emit_io_contract
from testgen.emit.validator import emit_validator
from testgen.extract import DEFAULT_ATTEMPTS
from testgen.llm import DEFAULT_MODEL
from testgen.ir.problems import PROBLEM_DIR, load, load_all
from testgen.plan import describe, plan_tests

def banner(command: str, subject: str = "") -> None:
    """Print a heading so back to back commands stay readable in a terminal."""
    title = f"{command}  {subject}".strip()
    print()
    print(f"=== {title} " + "=" * max(0, 68 - len(title)))
    print()


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
    banner("problems")
    rows = load_all()
    width = max(len(s) for s in rows)
    print(f"  {'FILE':<{width}}  {'NAME':<24}  SHAPE       CONTENTS")
    print(f"  {'-' * width}  {'-' * 24}  {'-' * 10}  {'-' * 26}")
    for stem, problem in rows.items():
        shape = "multitest" if problem.multitest else "single"
        extra = len(problem.global_constraints)
        print(
            f"  {stem:<{width}}  {problem.name:<24}  {shape:<10}  "
            f"{len(problem.body.variables)} vars, {extra} global"
        )
    print()
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
    banner("validator", args.problem)
    print(emit_validator(load(args.problem)), end="")
    return 0


def cmd_contract(args: argparse.Namespace) -> int:
    banner("input format contract", args.problem)
    print(emit_io_contract(load(args.problem)))
    return 0


def cmd_adapt(args: argparse.Namespace) -> int:
    from testgen.adapt import report

    banner("adapting extracted problem", Path(args.file).name)
    return report(Path(args.file))


def cmd_extract(args: argparse.Namespace) -> int:
    """Build an IR from an extracted statement with a model, and report it."""
    import json as _json

    from testgen.blockers import Verdict, describe as describe_blockers
    from testgen.extract import extract, save, slug
    from testgen.llm import LLMError, Model, available

    path = Path(args.file)
    banner("extracting an IR", path.name)

    ok, detail = available()
    if not ok:
        print(f"  {detail}", file=sys.stderr)
        print("  start one with `ollama serve`, then pull a model.", file=sys.stderr)
        return 2
    print(f"  models available: {detail}")
    print(f"  using: {args.model}")
    print()

    data = _json.loads(path.read_text())
    try:
        result = extract(
            data,
            model=Model(name=args.model),
            max_attempts=args.attempts,
            check_samples=not args.no_samples,
        )
    except LLMError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    print("  ATTEMPTS")
    for attempt in result.attempts:
        print(f"  {attempt}")
    print()

    if result.problem is None:
        print("  no IR could be built.")
        print()
        print(describe_blockers(result.blockers))
        return 1

    print("  IR")
    print(result.problem.model_dump_json(indent=2, exclude_none=True))
    print()
    print(describe_blockers(result.blockers))
    print()
    print(f"  VERDICT: {result.verdict.value}")

    if result.verdict is Verdict.FALLBACK:
        print("  this problem should go to the LLM-written C++ path instead.")
        return 1

    if args.save:
        stem = args.stem or slug(data)
        try:
            written = save(result.problem, stem, force=args.force)
        except FileExistsError as exc:
            print(f"  {exc}", file=sys.stderr)
            return 1
        print(f"  saved {written}")
        print(f"  now try: python -m testgen plan {stem}")

    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Score extraction against every statement on disk that has an IR."""
    from testgen.eval import report, run, totals
    from testgen.llm import LLMError, Model, available

    banner("extraction eval")

    ok, detail = available()
    if not ok:
        print(f"  {detail}", file=sys.stderr)
        return 2
    print(f"  using: {args.model}")

    try:
        rows = run(
            model=Model(name=args.model),
            max_attempts=args.attempts,
            only=args.only,
        )
    except LLMError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    for line in report(rows):
        print(line)
    print(totals(rows))
    print()
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    banner("test plan", args.problem)
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
    banner("generated tests", args.problem)
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
    banner("checker" if not args.decide else "checker decision", args.problem)
    if args.decide:
        print(describe_checker(problem))
        print()
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
    banner("validating files", args.problem)
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

    adapt = sub.add_parser(
        "adapt", help="turn an extracted problem.json into a draft IR"
    )
    adapt.add_argument("file", help="the problem.json the extraction step wrote")
    adapt.set_defaults(func=cmd_adapt)

    extract = sub.add_parser(
        "extract", help="build an IR from an extracted statement with a model"
    )
    extract.add_argument("file", help="the problem.json the extraction step wrote")
    extract.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    extract.add_argument(
        "--attempts",
        type=int,
        default=DEFAULT_ATTEMPTS,
        help="how many times to retry against the gates",
    )
    extract.add_argument(
        "--save", action="store_true", help="write the IR into ir/problems/"
    )
    extract.add_argument("--stem", help="file name to save under, if not the title")
    extract.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing IR file (this can erase a hand-written one)",
    )
    extract.add_argument(
        "--no-samples",
        action="store_true",
        help="skip the sample gate (schema check only)",
    )
    extract.set_defaults(func=cmd_extract)

    ev = sub.add_parser("eval", help="score extraction against the hand-written IR")
    ev.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    ev.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    ev.add_argument("--only", help="score just this problem")
    ev.set_defaults(func=cmd_eval)

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
    try:
        return args.func(args)
    except BrokenPipeError:
        # Whatever was reading us closed early, which is what happens with
        # `| head`. Point stdout at nowhere so the interpreter does not try to
        # flush it again on the way out and report the same thing twice.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
