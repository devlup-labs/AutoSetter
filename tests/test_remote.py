"""
Unit tests for autosetter.remote (SSH tunnel to the Ollama GPU server).
No ssh process is started: the tests cover the command line and port reuse.
"""

from __future__ import annotations

import socket

from autosetter import cli
from autosetter.remote import SSHTunnel


def test_command_minimal():
    tunnel = SSHTunnel("gpu", local_port=11434, remote_port=11434, key_file="", port=22)
    assert tunnel.command() == [
        "ssh", "-N", "-L", "11434:localhost:11434",
        "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
        "gpu",
    ]
    assert tunnel.local_url == "http://localhost:11434"


def test_command_with_port_and_key():
    tunnel = SSHTunnel("me@10.0.0.5", port=2222, key_file="~/.ssh/id_gpu", local_port=12000)
    cmd = tunnel.command()
    assert cmd[-1] == "me@10.0.0.5"
    assert cmd[cmd.index("-p") + 1] == "2222"
    assert cmd[cmd.index("-i") + 1] == "~/.ssh/id_gpu"
    assert "12000:localhost:11434" in cmd


def test_existing_listener_is_reused(monkeypatch):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]

        def no_popen(*args, **kwargs):
            raise AssertionError("ssh must not be started when the port is already open")

        monkeypatch.setattr("autosetter.remote.subprocess.Popen", no_popen)
        with SSHTunnel("gpu", local_port=port) as tunnel:
            assert tunnel.local_url == f"http://localhost:{port}"


def test_cli_ssh_flags():
    args = cli.build_arg_parser().parse_args(
        ["p.png", "--ssh", "me@gpu", "--ssh-port", "2222", "--ssh-local-port", "12000"]
    )
    assert (args.ssh_host, args.ssh_port, args.ssh_local_port) == ("me@gpu", 2222, 12000)


def test_cli_uses_tunnel_url_for_ollama(tmp_path, monkeypatch):
    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    seen = {}

    class FakeTunnel:
        local_url = "http://localhost:12000"

        def __init__(self, **kwargs):
            seen["host"] = kwargs["host"]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    def fake_generate(**kwargs):
        seen["ollama_host"] = kwargs["ollama_host"]
        raise cli.AutoSetterError("stop here")

    monkeypatch.setattr(cli, "SSHTunnel", FakeTunnel)
    monkeypatch.setattr(cli, "generate_from_image", fake_generate)
    assert cli.main([str(image), "--ssh", "me@gpu", "--no-polygon"]) == cli.EXIT_FAILED
    assert seen == {"host": "me@gpu", "ollama_host": "http://localhost:12000"}
