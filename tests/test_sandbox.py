"""
Unit tests for autosetter.sandbox (C++ compilation and local execution).
"""

from __future__ import annotations

from pathlib import Path
import pytest

from autosetter.sandbox import (
    SandboxError,
    SandboxLocalClient,
    ensure_testlib,
)
from tests.conftest import needs_gpp


def test_ensure_testlib_copies_to_directory(tmp_path: Path):
    dest = tmp_path / "custom_dir"
    header = ensure_testlib(dest)
    assert header.exists()
    assert header.name == "testlib.h"
    assert header.parent == dest


@needs_gpp
def test_local_compile_and_run_simple(tmp_path: Path):
    src = tmp_path / "hello.cpp"
    src.write_text(
        "#include <iostream>\n"
        "int main() { std::cout << \"HELLO_AUTOSERVER\"; return 0; }\n"
    )

    client = SandboxLocalClient()
    result = client.compile_and_run(src)

    assert result.status == "success"
    assert result.exit_code == 0
    assert "HELLO_AUTOSERVER" in result.stdout


@needs_gpp
def test_local_compile_syntax_error_raises_sandbox_error(tmp_path: Path):
    src = tmp_path / "broken.cpp"
    src.write_text("int main() { invalid_syntax_here; }")

    client = SandboxLocalClient()
    with pytest.raises(SandboxError) as excinfo:
        client.compile_file(src)
    assert "Compilation failed" in str(excinfo.value)


@needs_gpp
def test_local_execution_timeout(tmp_path: Path):
    src = tmp_path / "infinite.cpp"
    src.write_text(
        "#include <thread>\n"
        "#include <chrono>\n"
        "int main() {\n"
        "    while(true) { std::this_thread::sleep_for(std::chrono::milliseconds(100)); }\n"
        "    return 0;\n"
        "}\n"
    )

    client = SandboxLocalClient()
    binary = client.compile_file(src)
    result = client.run_binary(binary, timeout=1)

    assert result.status == "timeout"
    assert "Time Limit Exceeded" in result.stderr
