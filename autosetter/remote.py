"""
autosetter.remote
=================
SSH tunnel to the GPU server that runs Ollama.

The vision and text models (qwen3-vl:32b, Qwen3-Coder-Next) are too large to
run on a laptop, so they are served by Ollama on a remote GPU server whose
port 11434 is not exposed publicly. `SSHTunnel` runs

    ssh -N -L <local_port>:localhost:<remote_port> [-p PORT] [-i KEY] <host>

for the duration of a pipeline run, so the Ollama client can use
http://localhost:<local_port>. `host` can be `user@hostname` or an alias from
~/.ssh/config (which then supplies the user, port, key and any jump host).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from typing import Callable, List, Optional

from autosetter.config import (
    SSH_CONNECT_TIMEOUT,
    SSH_KEY,
    SSH_LOCAL_PORT,
    SSH_PORT,
    SSH_REMOTE_PORT,
)


class SSHTunnelError(Exception):
    """Raised when the SSH tunnel cannot be opened."""


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


class SSHTunnel:
    """
    Local port forward to a service on a remote host, used as a context manager.

    If something already listens on the local port (for example a tunnel the
    user opened by hand), it is reused and no ssh process is started.
    """

    def __init__(
        self,
        host: str,
        port: int = SSH_PORT,
        key_file: str = SSH_KEY,
        local_port: int = SSH_LOCAL_PORT,
        remote_port: int = SSH_REMOTE_PORT,
        timeout: int = SSH_CONNECT_TIMEOUT,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.key_file = key_file
        self.local_port = local_port
        self.remote_port = remote_port
        self.timeout = timeout
        self._log = progress_callback or (lambda msg: None)
        self._process: Optional[subprocess.Popen] = None

    @property
    def local_url(self) -> str:
        """HTTP URL of the forwarded service on this machine."""
        return f"http://localhost:{self.local_port}"

    def command(self) -> List[str]:
        """The ssh command line that opens the tunnel."""
        cmd = [
            "ssh", "-N",
            "-L", f"{self.local_port}:localhost:{self.remote_port}",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
        ]
        if self.port != 22:
            cmd += ["-p", str(self.port)]
        if self.key_file:
            cmd += ["-i", self.key_file]
        cmd.append(self.host)
        return cmd

    def open(self) -> "SSHTunnel":
        if _port_open(self.local_port):
            self._log(
                f"Port {self.local_port} is already in use; assuming an existing tunnel "
                f"to Ollama and reusing it."
            )
            return self
        if shutil.which("ssh") is None:
            raise SSHTunnelError("the `ssh` client is not installed or not on PATH")

        self._log(
            f"Opening SSH tunnel localhost:{self.local_port} -> {self.host}:{self.remote_port}..."
        )
        # stdin stays attached so ssh can ask for a password or key passphrase.
        self._process = subprocess.Popen(
            self.command(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                error = (self._process.stderr.read() or "").strip()
                self._process = None
                raise SSHTunnelError(f"ssh exited before the tunnel was ready: {error or 'no output'}")
            if _port_open(self.local_port):
                self._log("SSH tunnel is up.")
                return self
            time.sleep(0.25)
        self.close()
        raise SSHTunnelError(f"tunnel to {self.host} was not ready after {self.timeout}s")

    def close(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None

    def __enter__(self) -> "SSHTunnel":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()
