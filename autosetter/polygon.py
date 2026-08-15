"""
autosetter.polygon
==================
Codeforces Polygon API v2 integration client and package uploader.

Automates:
- HMAC-SHA512 authenticated API communication with Codeforces Polygon.
- Uploading generated problem packages (limits, checker, validator, generator,
  reference solutions with verdicts, LaTeX/Markdown statements, test cases, and tags).
- Committing problem revisions and requesting package builds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import string
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

from autosetter.config import (
    POLYGON_API_KEY,
    POLYGON_API_URL,
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


def upload_problem_package(
    package_dir: str | Path,
    problem_id: int,
    client: Optional[PolygonClient] = None,
    cpp_type: str = "cpp.g++17",
    progress_callback: Optional[Any] = None,
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
    """
    _log = progress_callback or (lambda msg: print(msg, flush=True))
    pkg = Path(package_dir)
    api = client or PolygonClient()

    _log(f"Starting Polygon upload for problem ID {problem_id} from {pkg}...")

    # 1. Update Limits
    _log("▶ Setting time and memory limits...")
    api.problem_call(
        "problem.updateInfo",
        problem_id,
        {
            "inputFile": "",
            "outputFile": "",
            "interactive": "false",
            "timeLimit": 2000,
            "memoryLimit": 262144,
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
    generator_path = pkg / "files" / "generator.cpp"
    if generator_path.exists():
        _log("▶ Uploading generator.cpp...")
        api.problem_call(
            "problem.saveFile",
            problem_id,
            {
                "type": "source",
                "name": "generator.cpp",
                "file": generator_path.read_text(encoding="utf-8"),
                "sourceType": cpp_type,
            },
        )

    # 6. Upload Solutions
    solutions_dir = pkg / "solutions"
    if solutions_dir.exists():
        _log("▶ Uploading solutions...")
        tag_map = {
            "solution_ac.cpp": "PA",
            "solution_brute.cpp": "OK",
            "solution.cpp": "OK",
            "solution_wrong.cpp": "WA",
            "solution_tle.cpp": "TL",
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

    # 7. Upload Statement
    tex_path = pkg / "statement" / "problem.tex"
    statement_path = pkg / "statement.md"
    legend = ""
    if tex_path.exists():
        legend = tex_path.read_text(encoding="utf-8")
    elif statement_path.exists():
        legend = statement_path.read_text(encoding="utf-8")

    if legend:
        _log("▶ Uploading statement...")
        api.problem_call(
            "problem.saveStatement",
            problem_id,
            {
                "lang": "english",
                "encoding": "utf-8",
                "name": "Problem",
                "legend": legend,
            },
        )

    # 8. Upload Tests
    tests_dir = pkg / "tests"
    if tests_dir.exists():
        test_inputs = sorted(tests_dir.glob("*.in"))
        _log(f"▶ Uploading {len(test_inputs)} tests...")
        for idx, in_path in enumerate(test_inputs, start=1):
            ans_path = in_path.with_suffix(".ans")
            if not ans_path.exists():
                continue
            is_sample = idx == 1
            api.problem_call(
                "problem.saveTest",
                problem_id,
                {
                    "testset": "tests",
                    "testIndex": idx,
                    "testInput": in_path.read_text(encoding="utf-8"),
                    "testAnswer": ans_path.read_text(encoding="utf-8"),
                    "testUseInStatements": "true" if is_sample else "false",
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

    _log("✅ Polygon upload completed successfully.")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for Polygon uploader."""
    parser = argparse.ArgumentParser(
        description="Upload an AutoSetter problem package to Codeforces Polygon."
    )
    parser.add_argument("package_dir", help="Path to package directory")
    parser.add_argument("problem_id", type=int, help="Target Polygon problem ID")
    parser.add_argument("--key", help="Polygon API key (optional if env var set)")
    parser.add_argument("--secret", help="Polygon secret (optional if env var set)")

    args = parser.parse_args(argv)

    try:
        client = PolygonClient(api_key=args.key, secret=args.secret)
        upload_problem_package(args.package_dir, args.problem_id, client=client)
        return 0
    except PolygonAPIError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
