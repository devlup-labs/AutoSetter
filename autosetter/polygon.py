"""
autosetter.polygon
==================
Codeforces Polygon API v2 integration client and package uploader.

Automates:
- HMAC-SHA512 authenticated API communication with Codeforces Polygon.
- Creating the Polygon problem (or updating an existing one by ID).
- Uploading generated problem packages (limits, checker, validator, generator,
  reference solutions with verdicts, statement, samples, test cases, and tags).
- Committing problem revisions and requesting package builds.

Run standalone on an existing package:

    python -m autosetter.polygon out/package              # creates a new problem
    python -m autosetter.polygon out/package --problem-id 123456

Credentials come from --key/--secret, POLYGON_API_KEY/POLYGON_SECRET, or an
interactive prompt (the secret is read without echo). Create them on Polygon
under Settings -> API Keys.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import random
import re
import string
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

from autosetter.config import (
    POLYGON_API_KEY,
    POLYGON_API_URL,
    POLYGON_DEFAULT_MEMORY_LIMIT_MB,
    POLYGON_DEFAULT_TIME_LIMIT_MS,
    POLYGON_SECRET,
)


class PolygonAPIError(Exception):
    """Raised when a Codeforces Polygon API call fails."""


class PolygonClient:
    """
    Client for interacting with Codeforces Polygon API.

    Parameters
    ----------
    api_key : Optional[str]
        Polygon API key (defaults to POLYGON_API_KEY environment variable).
    secret : Optional[str]
        Polygon secret (defaults to POLYGON_SECRET environment variable).
    base_url : str
        Base URL for Polygon API endpoints.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        base_url: str = POLYGON_API_URL,
    ) -> None:
        self.api_key = api_key or POLYGON_API_KEY
        self.secret = secret or POLYGON_SECRET
        self.base_url = base_url.rstrip("/") + "/"

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute an authenticated POST request against the Polygon API.
        Computes the required apiSig using HMAC-SHA512.
        """
        if requests is None:
            raise PolygonAPIError(
                "The 'requests' package is required for Polygon API operations. "
                "Run: pip install requests"
            )

        if not self.api_key or not self.secret:
            raise PolygonAPIError(
                "Polygon API key and secret are required. "
                "Set POLYGON_API_KEY and POLYGON_SECRET environment variables."
            )

        p = dict(params or {})
        p["apiKey"] = self.api_key
        p["time"] = int(time.time())

        # Generate 6 random alphanumeric prefix characters
        prefix = "".join(random.choices(string.ascii_letters + string.digits, k=6))
        sorted_pairs = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
        raw_signature_payload = f"{prefix}/{method}?{sorted_pairs}#{self.secret}"
        digest = hashlib.sha512(raw_signature_payload.encode("utf-8")).hexdigest()
        p["apiSig"] = prefix + digest

        endpoint = self.base_url + method
        try:
            response = requests.post(endpoint, data=p, timeout=30)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise PolygonAPIError(f"Polygon HTTP request failed for {method}: {exc}") from exc

        if body.get("status") != "OK":
            comment = body.get("comment", str(body))
            raise PolygonAPIError(f"Polygon API rejected {method}: {comment}")

        return body.get("result", {})

    def problem_call(
        self,
        method: str,
        problem_id: int,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convenience method injecting problemId into params."""
        params = {"problemId": problem_id}
        if extra_params:
            params.update(extra_params)
        return self.call(method, params)

    def verify_credentials(self) -> None:
        """Make a cheap authenticated call; raises PolygonAPIError if the key/secret are wrong."""
        self.call("problems.list", {"showDeleted": "false"})

    def create_problem(self, name: str) -> Dict[str, Any]:
        """Create an empty problem and return Polygon's Problem object (id, name, owner, ...)."""
        return self.call("problem.create", {"name": name})


# ---------------------------------------------------------------------------
# problem.json -> Polygon fields
# ---------------------------------------------------------------------------

def polygon_problem_name(title: str) -> str:
    """Polygon problem names may only hold lowercase letters, digits and dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug[:40].strip("-") or "autosetter-problem"


def parse_time_limit_ms(value: Any) -> int:
    """'2s', '2.0 seconds', '1500 ms' -> milliseconds (default when unparsable)."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(ms|millisecond|s|sec|second)?", str(value or "").lower())
    if not match:
        return POLYGON_DEFAULT_TIME_LIMIT_MS
    amount, unit = float(match.group(1)), match.group(2) or "s"
    ms = amount if unit.startswith("m") else amount * 1000
    return int(min(max(ms, 250), 15000))  # Polygon accepts 0.25s .. 15s


def parse_memory_limit_mb(value: Any) -> int:
    """'256MB', '256 megabytes', '1 GB', '262144 KB' -> megabytes (default when unparsable)."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(k|m|g)?", str(value or "").lower())
    if not match:
        return POLYGON_DEFAULT_MEMORY_LIMIT_MB
    amount, unit = float(match.group(1)), match.group(2) or "m"
    mb = {"k": amount / 1024, "m": amount, "g": amount * 1024}[unit]
    return int(min(max(mb, 4), 1024))  # Polygon accepts 4MB .. 1024MB


def _load_problem_json(pkg: Path) -> Dict[str, Any]:
    path = pkg / "problem.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value or "").strip()


def upload_problem_package(
    package_dir: str | Path,
    problem_id: int,
    client: Optional[PolygonClient] = None,
    cpp_type: str = "cpp.g++17",
    progress_callback: Optional[Any] = None,
    commit: bool = True,
    build: bool = True,
) -> None:
    """
    Upload an assembled problem package directory to Codeforces Polygon.

    Parameters
    ----------
    package_dir : str | Path
        Path to the `04_package/` or `out/package/` directory.
    problem_id : int
        The target Polygon problem ID.
    client : Optional[PolygonClient]
        Polygon client instance.
    cpp_type : str
        Source type compiler string on Polygon.
    progress_callback : Optional[Callable[[str], None]]
        Progress logger.
    commit : bool
        Commit the uploaded changes as a new problem revision.
    build : bool
        Request a (verified) package build after committing.
    """
    _log = progress_callback or (lambda msg: print(msg, flush=True))
    pkg = Path(package_dir)
    api = client or PolygonClient()
    problem = _load_problem_json(pkg)

    _log(f"Starting Polygon upload for problem ID {problem_id} from {pkg}...")

    # 1. Update Limits (Polygon takes milliseconds and megabytes)
    time_limit = parse_time_limit_ms(problem.get("time_limit"))
    memory_limit = parse_memory_limit_mb(problem.get("memory_limit"))
    _log(f"▶ Setting limits: {time_limit} ms, {memory_limit} MB...")
    api.problem_call(
        "problem.updateInfo",
        problem_id,
        {
            "inputFile": "stdin",
            "outputFile": "stdout",
            "interactive": "false",
            "timeLimit": time_limit,
            "memoryLimit": memory_limit,
        },
    )

    # 2. Upload Checker
    checker_path = pkg / "files" / "checker.cpp"
    if checker_path.exists():
        _log("▶ Uploading checker.cpp...")
        api.problem_call(
            "problem.saveFile",
            problem_id,
            {
                "type": "source",
                "name": "checker.cpp",
                "file": checker_path.read_text(encoding="utf-8"),
                "sourceType": cpp_type,
            },
        )
        api.problem_call(
            "problem.setChecker",
            problem_id,
            {"checker": "checker.cpp", "sourceType": cpp_type},
        )

    # 3. Upload Validator
    validator_path = pkg / "files" / "validator.cpp"
    if validator_path.exists():
        _log("▶ Uploading validator.cpp...")
        api.problem_call(
            "problem.saveFile",
            problem_id,
            {
                "type": "source",
                "name": "validator.cpp",
                "file": validator_path.read_text(encoding="utf-8"),
                "sourceType": cpp_type,
            },
        )
        api.problem_call(
            "problem.setValidator",
            problem_id,
            {"validator": "validator.cpp", "sourceType": cpp_type},
        )

    # 4. Upload testlib.h resource
    testlib_path = pkg / "files" / "testlib.h"
    if testlib_path.exists():
        _log("▶ Uploading testlib.h...")
        api.problem_call(
            "problem.saveFile",
            problem_id,
            {
                "type": "resource",
                "name": "testlib.h",
                "file": testlib_path.read_text(encoding="utf-8"),
            },
        )

    # 5. Upload Generator
    generator_py = pkg / "files" / "generator.py"
    generator_cpp = pkg / "files" / "generator.cpp"
    if generator_py.exists():
        _log("▶ Uploading generator.py...")
        api.problem_call(
            "problem.saveFile",
            problem_id,
            {
                "type": "source",
                "name": "generator.py",
                "file": generator_py.read_text(encoding="utf-8"),
                "sourceType": "python.3",
            },
        )
    elif generator_cpp.exists():
        _log("▶ Uploading generator.cpp...")
        api.problem_call(
            "problem.saveFile",
            problem_id,
            {
                "type": "source",
                "name": "generator.cpp",
                "file": generator_cpp.read_text(encoding="utf-8"),
                "sourceType": cpp_type,
            },
        )

    # 6. Upload Solutions
    solutions_dir = pkg / "solutions"
    if solutions_dir.exists():
        _log("▶ Uploading solutions...")
        tag_map = {
            "solution.cpp": "MA",
            "solution.brute.cpp": "TL",
            "solution.greedy.cpp": "WA",
            "solution.heavy.cpp": "TL",
        }
        for sol_file in sorted(solutions_dir.glob("*.cpp")):
            tag = tag_map.get(sol_file.name, "OK")
            _log(f"  · {sol_file.name} [{tag}]")
            api.problem_call(
                "problem.saveSolution",
                problem_id,
                {
                    "name": sol_file.name,
                    "file": sol_file.read_text(encoding="utf-8"),
                    "sourceType": cpp_type,
                    "tag": tag,
                },
            )

    # 7. Upload Statement: the problem.json sections map onto Polygon's
    # statement fields; statement.md (or problem.tex) is the fallback legend.
    statement: Dict[str, Any] = {}
    if problem.get("story"):
        statement = {
            "legend": _text(problem.get("story")),
            "input": _text(problem.get("input_format")),
            "output": _text(problem.get("output_format")),
            "notes": _text(problem.get("notes")),
        }
    else:
        for legend_path in (pkg / "statement" / "problem.tex", pkg / "statement.md"):
            if legend_path.exists():
                statement = {"legend": legend_path.read_text(encoding="utf-8")}
                break

    if statement:
        _log("▶ Uploading statement...")
        api.problem_call(
            "problem.saveStatement",
            problem_id,
            {
                "lang": "english",
                "encoding": "utf-8",
                "name": _text(problem.get("title")) or "Problem",
                **{k: v for k, v in statement.items() if v},
            },
        )

    # 8. Upload Tests: official samples first (shown in the statement), then
    # the generated tests. Polygon computes answers with the main solution.
    test_inputs = sorted((pkg / "samples").glob("*.in"))
    sample_count = len(test_inputs)
    test_inputs += [
        p for p in sorted((pkg / "tests").glob("*.in")) if p.with_suffix(".ans").exists()
    ]
    if test_inputs:
        _log(f"▶ Uploading {len(test_inputs)} tests ({sample_count} samples)...")
    for idx, in_path in enumerate(test_inputs, start=1):
        api.problem_call(
            "problem.saveTest",
            problem_id,
            {
                "testset": "tests",
                "testIndex": idx,
                "testInput": in_path.read_text(encoding="utf-8"),
                "testUseInStatements": "true" if idx <= sample_count else "false",
                "checkExisting": "false",
            },
        )

    # 8b. Upload Script
    script_path = pkg / "script"
    if script_path.exists():
        _log("▶ Uploading generator script...")
        api.problem_call(
            "problem.saveScript",
            problem_id,
            {
                "testset": "tests",
                "source": script_path.read_text(encoding="utf-8"),
            },
        )

    # 9. Upload Tags
    tags_path = pkg / "tags.txt"
    if tags_path.exists():
        tags = [
            t.strip()
            for t in tags_path.read_text(encoding="utf-8").splitlines()
            if t.strip()
        ]
        if tags:
            _log(f"▶ Saving tags: {', '.join(tags)}...")
            api.problem_call(
                "problem.saveTags",
                problem_id,
                {"tags": ",".join(tags)},
            )

    # 10. Commit and build
    if commit:
        _log("▶ Committing changes...")
        api.problem_call(
            "problem.commitChanges",
            problem_id,
            {"minorChanges": "false", "message": "Uploaded by AutoSetter"},
        )
        if build:
            _log("▶ Requesting package build (with verification)...")
            api.problem_call(
                "problem.buildPackage", problem_id, {"full": "false", "verify": "true"}
            )

    _log("✅ Polygon upload completed successfully.")


@dataclass
class PublishResult:
    """Where a package ended up on Polygon."""

    problem_id: int
    name: str
    created: bool


def publish_package(
    package_dir: str | Path,
    client: PolygonClient,
    problem_id: Optional[int] = None,
    name: Optional[str] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> PublishResult:
    """
    Push a package to Polygon, creating the problem first unless `problem_id` is given.

    The new problem is named after the title in problem.json (or `name`); if
    that name is taken, a timestamp suffix is added.
    """
    _log = progress_callback or (lambda msg: print(msg, flush=True))
    pkg = Path(package_dir)
    created = problem_id is None
    if problem_id is None:
        base = polygon_problem_name(name or _text(_load_problem_json(pkg).get("title")))
        try:
            problem = client.create_problem(base)
        except PolygonAPIError as first_error:
            fallback = f"{base[:27]}-{time.strftime('%Y%m%d%H%M')}"
            _log(f"Could not create '{base}' ({first_error}); trying '{fallback}'...")
            problem = client.create_problem(fallback)
        problem_id = int(problem["id"])
        name = problem.get("name") or base
        _log(f"Created Polygon problem '{name}' (ID {problem_id}).")

    upload_problem_package(pkg, problem_id, client=client, progress_callback=_log)
    return PublishResult(problem_id=problem_id, name=name or "", created=created)


def prompt_credentials(
    api_key: str = "",
    secret: str = "",
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
) -> Tuple[str, str]:
    """
    Ask for the Polygon API key and secret, offering values already known.

    Pressing Enter keeps a known value. Returns ("", "") when the user leaves
    the key blank and none is known, meaning "skip the upload".
    """
    key_hint = " [press Enter to use POLYGON_API_KEY]" if api_key else " (blank to skip upload)"
    entered_key = input_fn(f"Polygon API key{key_hint}: ").strip()
    api_key = entered_key or api_key
    if not api_key:
        return "", ""
    secret_hint = " [press Enter to use POLYGON_SECRET]" if secret and not entered_key else ""
    entered_secret = secret_fn(f"Polygon API secret{secret_hint}: ").strip()
    if entered_key:
        # A newly typed key must come with its own secret.
        secret = entered_secret
    else:
        secret = entered_secret or secret
    return api_key, secret


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for Polygon uploader."""
    parser = argparse.ArgumentParser(
        description="Upload an AutoSetter problem package to Codeforces Polygon."
    )
    parser.add_argument("package_dir", help="Path to package directory")
    parser.add_argument(
        "problem_id", type=int, nargs="?", default=None,
        help="Existing Polygon problem ID (omit to create a new problem)",
    )
    parser.add_argument("--problem-id", dest="problem_id_opt", type=int, metavar="ID", help="Same as the positional problem_id")
    parser.add_argument("--name", help="Name for a newly created problem (default: from problem.json title)")
    parser.add_argument("--key", help="Polygon API key (optional if env var set)")
    parser.add_argument("--secret", help="Polygon secret (optional if env var set)")

    args = parser.parse_args(argv)
    problem_id = args.problem_id if args.problem_id is not None else args.problem_id_opt

    api_key, secret = args.key or POLYGON_API_KEY, args.secret or POLYGON_SECRET
    if (not api_key or not secret) and sys.stdin.isatty():
        api_key, secret = prompt_credentials(api_key, secret)

    try:
        client = PolygonClient(api_key=api_key, secret=secret)
        result = publish_package(args.package_dir, client, problem_id=problem_id, name=args.name)
        print(f"Polygon problem ID: {result.problem_id}")
        return 0
    except PolygonAPIError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
