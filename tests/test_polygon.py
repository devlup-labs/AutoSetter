"""
Unit tests for autosetter.polygon (package upload, problem creation, credential
prompts) and the CLI's Polygon step. No requests reach Polygon: a recording
client stands in for PolygonClient.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from autosetter import cli
from autosetter.polygon import (
    PolygonAPIError,
    PolygonClient,
    prompt_credentials,
    publish_package,
    upload_problem_package,
)
from autosetter.runner import AutoSetterError, PipelineResult


class RecordingClient:
    """Records every Polygon call; `fail` maps method names to error messages."""

    def __init__(self, fail: Optional[Dict[str, str]] = None) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.fail = dict(fail or {})

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.calls.append((method, dict(params or {})))
        if method in self.fail:
            raise PolygonAPIError(self.fail.pop(method))
        if method == "problem.create":
            return {"id": 4242, "name": params["name"], "owner": "setter"}
        return {}

    def problem_call(self, method, problem_id, extra_params=None):
        return self.call(method, {"problemId": problem_id, **(extra_params or {})})

    def verify_credentials(self) -> None:
        self.call("problems.list")

    def create_problem(self, name: str) -> Dict[str, Any]:
        return self.call("problem.create", {"name": name})

    def methods(self) -> List[str]:
        return [m for m, _ in self.calls]

    def params(self, method: str) -> List[Dict[str, Any]]:
        return [p for m, p in self.calls if m == method]


def make_package(tmp_path: Path) -> Path:
    pkg = tmp_path / "package"
    (pkg / "files").mkdir(parents=True)
    (pkg / "solutions").mkdir()
    (pkg / "samples").mkdir()
    (pkg / "tests").mkdir()
    (pkg / "problem.json").write_text(json.dumps({
        "title": "A Plus B",
        "story": "Compute a + b.",
        "input_format": "Two integers.",
        "output_format": "Their sum.",
        "notes": "",
        "time_limit": "1s",
        "memory_limit": "512MB",
    }))
    for name in ("checker.cpp", "validator.cpp", "testlib.h"):
        (pkg / "files" / name).write_text("// " + name)
    (pkg / "solutions" / "solution.cpp").write_text("int main(){}")
    (pkg / "solutions" / "solution.greedy.cpp").write_text("int main(){}")
    (pkg / "samples" / "01.in").write_text("2 3\n")
    (pkg / "samples" / "01.ans").write_text("5\n")
    for i in (1, 2):
        (pkg / "tests" / f"{i:03d}.in").write_text(f"{i} {i}\n")
        (pkg / "tests" / f"{i:03d}.ans").write_text(f"{2 * i}\n")
    return pkg


def test_upload_uses_problem_json_limits_and_statement(tmp_path: Path):
    client = RecordingClient()
    upload_problem_package(make_package(tmp_path), 7, client=client, progress_callback=lambda m: None)

    info = client.params("problem.updateInfo")[0]
    assert info["timeLimit"] == 1000
    assert info["memoryLimit"] == 512  # megabytes, as Polygon expects

    statement = client.params("problem.saveStatement")[0]
    assert statement["name"] == "A Plus B"
    assert statement["legend"] == "Compute a + b."
    assert statement["input"] == "Two integers."
    assert "notes" not in statement  # empty sections are not sent


def test_upload_puts_samples_first_and_marks_them(tmp_path: Path):
    client = RecordingClient()
    upload_problem_package(make_package(tmp_path), 7, client=client, progress_callback=lambda m: None)

    tests = client.params("problem.saveTest")
    assert [t["testIndex"] for t in tests] == [1, 2, 3]
    assert tests[0]["testInput"] == "2 3\n"
    assert [t["testUseInStatements"] for t in tests] == ["true", "false", "false"]


def test_upload_commits_and_builds_last(tmp_path: Path):
    client = RecordingClient()
    upload_problem_package(make_package(tmp_path), 7, client=client, progress_callback=lambda m: None)

    assert client.methods()[-2:] == ["problem.commitChanges", "problem.buildPackage"]
    solutions = {p["name"]: p["tag"] for p in client.params("problem.saveSolution")}
    assert solutions == {"solution.cpp": "MA", "solution.greedy.cpp": "WA"}


def test_publish_creates_problem_named_after_title(tmp_path: Path):
    client = RecordingClient()
    result = publish_package(make_package(tmp_path), client, progress_callback=lambda m: None)

    assert client.params("problem.create")[0]["name"] == "a-plus-b"
    assert (result.problem_id, result.created) == (4242, True)
    assert all(p.get("problemId") == 4242 for p in client.params("problem.saveTest"))


def test_publish_retries_with_suffix_when_name_taken(tmp_path: Path):
    client = RecordingClient(fail={"problem.create": "name already used"})
    publish_package(make_package(tmp_path), client, progress_callback=lambda m: None)

    names = [p["name"] for p in client.params("problem.create")]
    assert names[0] == "a-plus-b"
    assert names[1].startswith("a-plus-b-")


def test_publish_to_existing_problem_skips_create(tmp_path: Path):
    client = RecordingClient()
    result = publish_package(make_package(tmp_path), client, problem_id=99, progress_callback=lambda m: None)

    assert "problem.create" not in client.methods()
    assert (result.problem_id, result.created) == (99, False)


def test_client_requires_credentials():
    with pytest.raises(PolygonAPIError, match="key and secret"):
        PolygonClient(api_key="", secret="").call("problems.list")


# ---------------------------------------------------------------------------
# Credential prompts
# ---------------------------------------------------------------------------

def _answers(*values):
    it = iter(values)
    return lambda prompt: next(it)


def test_prompt_credentials_reads_key_and_secret():
    assert prompt_credentials(input_fn=_answers("key1"), secret_fn=_answers("sec1")) == ("key1", "sec1")


def test_prompt_credentials_blank_key_skips():
    assert prompt_credentials(input_fn=_answers(""), secret_fn=_answers("unused")) == ("", "")


def test_prompt_credentials_enter_keeps_env_values():
    got = prompt_credentials("envkey", "envsecret", input_fn=_answers(""), secret_fn=_answers(""))
    assert got == ("envkey", "envsecret")


def test_prompt_credentials_new_key_needs_its_own_secret():
    got = prompt_credentials("envkey", "envsecret", input_fn=_answers("newkey"), secret_fn=_answers(""))
    assert got == ("newkey", "")


# ---------------------------------------------------------------------------
# CLI Polygon step
# ---------------------------------------------------------------------------

def _args(*extra):
    return cli.build_arg_parser().parse_args(["p.png", *extra])


def test_setup_polygon_disabled():
    assert cli.setup_polygon(_args("--no-polygon"), interactive=True) is None


def test_setup_polygon_verifies_prompted_credentials(monkeypatch):
    clients = []

    def make_client(api_key, secret):
        client = RecordingClient()
        client.creds = (api_key, secret)
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "PolygonClient", make_client)
    target = cli.setup_polygon(
        _args("--polygon-problem-id", "5"),
        interactive=True,
        log=lambda m: None,
        input_fn=_answers("k"),
        secret_fn=_answers("s"),
    )
    assert target.client.creds == ("k", "s")
    assert target.problem_id == 5
    assert clients[0].methods() == ["problems.list"]


def test_setup_polygon_reprompts_after_rejection(monkeypatch):
    attempts = []

    def make_client(api_key, secret):
        attempts.append(api_key)
        return RecordingClient(fail={"problems.list": "bad key"} if api_key == "wrong" else None)

    monkeypatch.setattr(cli, "PolygonClient", make_client)
    target = cli.setup_polygon(
        _args(),
        interactive=True,
        log=lambda m: None,
        input_fn=_answers("wrong", "right"),
        secret_fn=_answers("s", "s"),
    )
    assert attempts == ["wrong", "right"]
    assert target is not None


def test_setup_polygon_non_interactive_without_env_skips(monkeypatch):
    monkeypatch.setattr(cli, "POLYGON_API_KEY", "")
    monkeypatch.setattr(cli, "POLYGON_SECRET", "")
    assert cli.setup_polygon(_args(), interactive=False, log=lambda m: None) is None


def test_setup_polygon_non_interactive_bad_env_fails(monkeypatch):
    monkeypatch.setattr(cli, "POLYGON_API_KEY", "k")
    monkeypatch.setattr(cli, "POLYGON_SECRET", "s")
    monkeypatch.setattr(cli, "PolygonClient", lambda **_: RecordingClient(fail={"problems.list": "bad"}))
    with pytest.raises(AutoSetterError):
        cli.setup_polygon(_args(), interactive=False, log=lambda m: None)


class _Report:
    def __init__(self, ok: bool) -> None:
        self.all_passed = ok
        self.passed_tests = 3
        self.validator_trusted = True
        self.diagnosis = "" if ok else "checker flawed"


def _run_main(tmp_path, monkeypatch, result: PipelineResult, client=None, *extra):
    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(cli, "generate_from_image", lambda **_: result)
    monkeypatch.setattr(
        cli, "setup_polygon",
        lambda args, interactive: cli.PolygonTarget(client) if client else None,
    )
    return cli.main([str(image), *extra])


def test_main_returns_3_and_prints_link_for_existing_problem(tmp_path, monkeypatch, capsys):
    existing = {"score": 0.91, "title": "Theatre Square", "url": "https://codeforces.com/problemset/problem/1/A"}
    result = PipelineResult(tmp_path / "g", tmp_path / "p", existing_problem=existing)
    client = RecordingClient()

    assert _run_main(tmp_path, monkeypatch, result, client) == cli.EXIT_EXISTING_PROBLEM
    assert "https://codeforces.com/problemset/problem/1/A" in capsys.readouterr().out
    assert client.calls == []


def test_main_pushes_verified_package(tmp_path, monkeypatch):
    pkg = make_package(tmp_path)
    result = PipelineResult(tmp_path / "g", pkg, report=_Report(ok=True))
    client = RecordingClient()

    assert _run_main(tmp_path, monkeypatch, result, client) == cli.EXIT_RELEASED
    assert "problem.buildPackage" in client.methods()


def test_main_does_not_push_unverified_package(tmp_path, monkeypatch):
    pkg = make_package(tmp_path)
    result = PipelineResult(tmp_path / "g", pkg, report=_Report(ok=False))
    client = RecordingClient()

    assert _run_main(tmp_path, monkeypatch, result, client) == cli.EXIT_NOT_RELEASABLE
    assert client.calls == []


def test_main_push_unverified_flag(tmp_path, monkeypatch):
    pkg = make_package(tmp_path)
    result = PipelineResult(tmp_path / "g", pkg, report=_Report(ok=False))
    client = RecordingClient()

    code = _run_main(tmp_path, monkeypatch, result, client, "--push-unverified")
    assert code == cli.EXIT_NOT_RELEASABLE
    assert "problem.create" in client.methods()


def test_main_reports_polygon_failure(tmp_path, monkeypatch):
    pkg = make_package(tmp_path)
    result = PipelineResult(tmp_path / "g", pkg, report=_Report(ok=True))
    client = RecordingClient(fail={"problem.saveTest": "server error"})

    assert _run_main(tmp_path, monkeypatch, result, client) == cli.EXIT_POLYGON_FAILED
