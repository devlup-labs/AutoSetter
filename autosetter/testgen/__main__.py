"""
Generate or check tests from a spec without running the whole pipeline.

    python -m autosetter.testgen test_spec.json 7            # print the test for seed 7
    python -m autosetter.testgen test_spec.json 1 10 -o tests # write tests/001.in .. 010.in
    python -m autosetter.testgen test_spec.json --check-samples out/problem.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from autosetter.testgen import (
    SpecError,
    TestGenError,
    check_samples,
    generate_test,
    load_spec_file,
)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autosetter.testgen")
    parser.add_argument("spec", help="path to test_spec.json")
    parser.add_argument("seed", nargs="?", type=int, help="seed, or first seed of a range")
    parser.add_argument("last_seed", nargs="?", type=int, help="last seed of a range")
    parser.add_argument("-o", "--out-dir", help="write NNN.in files here instead of printing")
    parser.add_argument("--check-samples", metavar="PROBLEM_JSON",
                        help="check the samples in problem.json against the spec")
    args = parser.parse_args(argv)

    try:
        spec = load_spec_file(args.spec)
    except SpecError as exc:
        print(f"Invalid spec:\n{exc}", file=sys.stderr)
        return 1

    if args.check_samples:
        problem = json.loads(Path(args.check_samples).read_text(encoding="utf-8"))
        problems = check_samples(spec, problem.get("samples") or [])
        for p in problems:
            print(p, file=sys.stderr)
        print("samples OK" if not problems else f"{len(problems)} problem(s)")
        if problems or args.seed is None:
            return 1 if problems else 0

    if args.seed is None:
        parser.error("a seed is required unless --check-samples is given")

    last = args.last_seed if args.last_seed is not None else args.seed
    for seed in range(args.seed, last + 1):
        try:
            test = generate_test(spec, seed)
        except TestGenError as exc:
            print(f"seed {seed}: {exc}", file=sys.stderr)
            return 1
        if args.out_dir:
            out = Path(args.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{seed:03d}.in").write_text(test.text, encoding="utf-8")
            print(f"seed {seed}: {test.strategy}, {test.num_cases} case(s)", file=sys.stderr)
        else:
            sys.stdout.write(test.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
