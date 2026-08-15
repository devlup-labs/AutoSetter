"""
autosetter.sandbox
==================
Sandboxed compilation and execution of C++ source files.

Supports two execution backends:
1. `SandboxLocalClient`: Compiles and executes code directly on the host using `g++`
   and `subprocess`.
2. `SandboxHTTPClient`: Submits code over HTTP to the isolated Docker + NsJail sandbox
   server (`POST /api/execute`).

Also manages vendored `testlib.h` distribution across compilation targets.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from autosetter.config import (
    CPP_OPTIMIZATION,
    CPP_STANDARD,
    DEFAULT_COMPILER,
    TESTLIB_URL,
    VENDORED_TESTLIB,
)


class SandboxError(Exception):
    """Raised when compilation or execution inside the sandbox fails."""


@dataclass
class ExecutionResult:
    """Outcome of a single compilation or binary execution."""

    status: str = "success"  # success | compile_error | runtime_error | timeout
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    compile_time_ms: int = 0
    execute_time_ms: int = 0
    total_time_ms: int = 0


# ---------------------------------------------------------------------------
# testlib.h Distribution & Management
# ---------------------------------------------------------------------------

def ensure_testlib(dest_dir: str | Path) -> Path:
    """
    Ensure `testlib.h` is present in `dest_dir`, copying it from `autosetter/include/` if absent.

    Parameters
    ----------
    dest_dir : str | Path
        Destination directory.

    Returns
    -------
    Path
        Path to the local `testlib.h` file.
    """
    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    testlib_path = destination / "testlib.h"

    if testlib_path.exists():
        return testlib_path

    if not VENDORED_TESTLIB.exists():
        raise SandboxError(
            f"Vendored testlib.h missing from expected location: {VENDORED_TESTLIB}. "
            "Restore it or run refresh_vendored_testlib() to download a fresh copy."
        )

    try:
        shutil.copy2(VENDORED_TESTLIB, testlib_path)
    except OSError as exc:
        raise SandboxError(f"Failed to copy testlib.h into {destination}: {exc}") from exc

    return testlib_path


def refresh_vendored_testlib() -> Path:
    """
    Re-download `testlib.h` from upstream Mike Mirzayanov repository.
    Used for explicit maintenance only.
    """
    VENDORED_TESTLIB.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(TESTLIB_URL, str(VENDORED_TESTLIB))
    except Exception as exc:
        raise SandboxError(f"Failed to download testlib.h from {TESTLIB_URL}: {exc}") from exc
    return VENDORED_TESTLIB


# ---------------------------------------------------------------------------
# HTTP Sandbox Client (Docker + NsJail Server)
# ---------------------------------------------------------------------------

class SandboxHTTPClient:
    """
    Communicates with the sandbox Express server running Docker + NsJail workers.
    """

    def __init__(self, base_url: str = "http://localhost:3000") -> None:
        self.base_url = base_url.rstrip("/")

    def is_available(self) -> bool:
        """Check if the sandbox HTTP server is running and healthy."""
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
        """Submit code for compilation and sandboxed execution."""
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
# Local Subprocess Sandbox Client (Host g++)
# ---------------------------------------------------------------------------

class SandboxLocalClient:
    """
    Compiles and executes C++ source files directly on the host using `g++` and `subprocess`.
    """

    def __init__(
        self,
        testlib_dir: str | Path | None = None,
        compiler: str = DEFAULT_COMPILER,
        cpp_standard: str = CPP_STANDARD,
        optimization: str = CPP_OPTIMIZATION,
    ) -> None:
        self.compiler = compiler
        self.cpp_standard = cpp_standard
        self.optimization = optimization
        self.testlib_dir = Path(testlib_dir) if testlib_dir else None

    def compile_file(
        self,
        source_path: str | Path,
        output_path: str | Path | None = None,
        needs_testlib: bool = False,
        extra_flags: Optional[List[str]] = None,
        timeout: int = 60,
    ) -> Path:
        """
        Compile a C++ source file to a binary executable.

        Parameters
        ----------
        source_path : str | Path
            Path to the .cpp source file.
        output_path : Optional[str | Path]
            Target path for the compiled binary. Defaults to source path without extension.
        needs_testlib : bool
            Whether to include `-I` directory containing `testlib.h`.
        extra_flags : Optional[List[str]]
            Additional compiler flags.
        timeout : int
            Compilation timeout in seconds.

        Returns
        -------
        Path
            Path to the compiled binary executable.
        """
        source = Path(source_path)
        if not source.exists():
            raise SandboxError(f"Source file not found: {source}")

        if output_path is None:
            output_path = source.with_suffix("")
        out_binary = Path(output_path)

        cmd = [
            self.compiler,
            f"-std={self.cpp_standard}",
            self.optimization,
        ]

        if needs_testlib:
            target_dir = self.testlib_dir or source.parent
            ensure_testlib(target_dir)
            cmd.extend(["-I", str(target_dir)])

        if extra_flags:
            cmd.extend(extra_flags)

        cmd.extend(["-o", str(out_binary), str(source)])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(
                f"Compilation timed out after {timeout}s for {source.name}"
            ) from exc
        except FileNotFoundError as exc:
            raise SandboxError(
                f"Compiler '{self.compiler}' not found. Ensure g++ is installed and on PATH."
            ) from exc

        if result.returncode != 0:
            raise SandboxError(
                f"Compilation failed for {source.name}:\n{result.stderr}"
            )

        return out_binary

    def run_binary(
        self,
        binary_path: str | Path,
        stdin: str = "",
        timeout: int = 10,
        args: Optional[List[str]] = None,
    ) -> ExecutionResult:
        """Execute a compiled binary with optional stdin, args, and timeout."""
        binary = Path(binary_path)
        if not binary.exists():
            raise SandboxError(f"Binary not found: {binary}")

        cmd = [str(binary)]
        if args:
            cmd.extend(args)

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
        args: Optional[List[str]] = None,
    ) -> ExecutionResult:
        """Compile and immediately execute a C++ source file."""
        total_start = time.monotonic()
        compile_start = time.monotonic()

        binary = self.compile_file(source_path, needs_testlib=needs_testlib)
        compile_ms = int((time.monotonic() - compile_start) * 1000)

        result = self.run_binary(binary, stdin=stdin, timeout=timeout, args=args)
        result.compile_time_ms = compile_ms
        result.total_time_ms = int((time.monotonic() - total_start) * 1000)
        return result
