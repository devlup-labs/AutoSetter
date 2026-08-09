"""
sandbox_client.py
==================
Python interface for compiling and executing C++ code in the AutoSetter
pipeline.  Supports two execution backends:

1. **HTTP mode** — sends code to the sandbox Express server (Docker + NsJail)
   at ``POST /api/execute``.  Best for production / isolated execution.
2. **Local mode** — compiles and runs directly on the host via ``g++`` and
   ``subprocess``.  Useful during development when Docker isn't running.

The module also provides helpers for downloading ``testlib.h`` (required by
validator, generator, and checker) and for compiling files that depend on it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# URL to the canonical testlib.h from the MikeMirzayanov/testlib repo.
TESTLIB_URL = "https://raw.githubusercontent.com/MikeMirzayanov/testlib/master/testlib.h"


class SandboxError(Exception):
    """Raised when compilation or execution inside the sandbox fails."""


@dataclass
class ExecutionResult:
    """Outcome of a single compile-and-run invocation."""

    status: str = "success"  # success | compile_error | runtime_error | timeout
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    compile_time_ms: int = 0
    execute_time_ms: int = 0
    total_time_ms: int = 0


# ---------------------------------------------------------------------------
# testlib.h management
# ---------------------------------------------------------------------------

def ensure_testlib(dest_dir: str | Path) -> Path:
    """
    Make sure ``testlib.h`` exists in *dest_dir*.  If it's missing, download
    it from the official GitHub repo.  Returns the path to ``testlib.h``.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    testlib_path = dest_dir / "testlib.h"

    if testlib_path.exists():
        return testlib_path

    try:
        urllib.request.urlretrieve(TESTLIB_URL, str(testlib_path))
    except Exception as exc:
        raise SandboxError(
            f"Failed to download testlib.h from {TESTLIB_URL}: {exc}"
        ) from exc

    return testlib_path


# ---------------------------------------------------------------------------
# HTTP-based sandbox client  (talks to sandbox/server Express API)
# ---------------------------------------------------------------------------

class SandboxHTTPClient:
    """
    Sends code to the sandbox Express server for execution.

    The server is expected to be running at *base_url* and to expose:
    - ``POST /api/execute`` — compile & run C++ code.
    - ``GET  /api/health``  — health check.
    """

    def __init__(self, base_url: str = "http://localhost:3000") -> None:
        self.base_url = base_url.rstrip("/")

    def is_available(self) -> bool:
        """Return True if the sandbox server is reachable."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def execute(
        self,
        code: str,
        stdin: str = "",
        time_limit: int = 5,
    ) -> ExecutionResult:
        """
        Send *code* to the sandbox server for compilation & execution.
        """
        payload = json.dumps({
            "code": code,
            "language": "cpp",
            "stdin": stdin,
            "timeLimit": time_limit,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/api/execute",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=time_limit + 30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise SandboxError(f"Sandbox HTTP call failed: {exc}") from exc

        return ExecutionResult(
            status=data.get("status", "error"),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            exit_code=data.get("exitCode", -1),
            compile_time_ms=data.get("compileTimeMs", 0),
            execute_time_ms=data.get("executeTimeMs", 0),
            total_time_ms=data.get("totalTimeMs", 0),
        )


# ---------------------------------------------------------------------------
# Local subprocess-based execution
# ---------------------------------------------------------------------------

class SandboxLocalClient:
    """
    Compiles and runs C++ code directly on the host machine using ``g++``
    and ``subprocess``.  No Docker required.

    For files that use ``testlib.h``, pass ``needs_testlib=True`` and this
    client will ensure ``testlib.h`` is present in the compilation directory.
    """

    def __init__(
        self,
        testlib_dir: str | Path | None = None,
        compiler: str = "g++",
        cpp_standard: str = "c++17",
        optimization: str = "-O2",
    ) -> None:
        self.compiler = compiler
        self.cpp_standard = cpp_standard
        self.optimization = optimization
        # Directory where testlib.h lives (or will be downloaded to).
        self.testlib_dir = Path(testlib_dir) if testlib_dir else None

    def compile_file(
        self,
        source_path: str | Path,
        output_path: str | Path | None = None,
        needs_testlib: bool = False,
        extra_flags: list[str] | None = None,
        timeout: int = 60,
    ) -> Path:
        """
        Compile a C++ source file.  Returns the path to the executable.

        Raises ``SandboxError`` on compilation failure.
        """
        source_path = Path(source_path)
        if not source_path.exists():
            raise SandboxError(f"Source file not found: {source_path}")

        if output_path is None:
            output_path = source_path.with_suffix("")
        output_path = Path(output_path)

        cmd = [
            self.compiler,
            f"-std={self.cpp_standard}",
            self.optimization,
        ]

        # If the source needs testlib.h, add the include directory.
        if needs_testlib:
            testlib_dir = self.testlib_dir or source_path.parent
            ensure_testlib(testlib_dir)
            cmd.extend(["-I", str(testlib_dir)])

        if extra_flags:
            cmd.extend(extra_flags)

        cmd.extend(["-o", str(output_path), str(source_path)])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(
                f"Compilation timed out after {timeout}s for {source_path}"
            ) from exc
        except FileNotFoundError as exc:
            raise SandboxError(
                f"Compiler '{self.compiler}' not found. "
                "Ensure g++ is installed and on PATH."
            ) from exc

        if result.returncode != 0:
            raise SandboxError(
                f"Compilation failed for {source_path.name}:\n{result.stderr}"
            )

        return output_path

    def run_binary(
        self,
        binary_path: str | Path,
        stdin: str = "",
        timeout: int = 10,
        args: list[str] | None = None,
    ) -> ExecutionResult:
        """
        Execute a compiled binary, feeding *stdin* as input and capturing
        stdout/stderr.
        """
        binary_path = Path(binary_path)
        if not binary_path.exists():
            raise SandboxError(f"Binary not found: {binary_path}")

        cmd = [str(binary_path)]
        if args:
            cmd.extend(args)

        import time

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            elapsed = int((time.monotonic() - start) * 1000)
            return ExecutionResult(
                status="timeout",
                stderr=f"Time Limit Exceeded ({timeout}s)",
                execute_time_ms=elapsed,
                total_time_ms=elapsed,
            )

        elapsed = int((time.monotonic() - start) * 1000)

        status = "success" if proc.returncode == 0 else "runtime_error"
        return ExecutionResult(
            status=status,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            execute_time_ms=elapsed,
            total_time_ms=elapsed,
        )

    def compile_and_run(
        self,
        source_path: str | Path,
        stdin: str = "",
        needs_testlib: bool = False,
        timeout: int = 10,
        args: list[str] | None = None,
    ) -> ExecutionResult:
        """
        Convenience: compile a source file, then run the resulting binary.
        """
        import time

        total_start = time.monotonic()

        compile_start = time.monotonic()
        binary = self.compile_file(source_path, needs_testlib=needs_testlib)
        compile_ms = int((time.monotonic() - compile_start) * 1000)

        result = self.run_binary(binary, stdin=stdin, timeout=timeout, args=args)
        result.compile_time_ms = compile_ms
        result.total_time_ms = int((time.monotonic() - total_start) * 1000)

        return result
