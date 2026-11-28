"""run_remote must survive remote output that is not valid UTF-8 (e.g. a truncated agent log)."""

import sys

import pytest

from cli.remote_dev import RemoteDevEnvironment

BAD = b"session id: 0b6f3c1e-1111-2222-3333-444455556666 \x9c tail"


@pytest.fixture
def env(monkeypatch):
    env = RemoteDevEnvironment(name="demo")
    # Stand in for ssh with a local process that prints the invalid byte.
    monkeypatch.setattr(
        env,
        "_ssh_command",
        lambda *a, **k: [sys.executable, "-c", f"import sys; sys.stdout.buffer.write({BAD!r})"],
    )
    return env


def test_run_remote_replaces_undecodable_bytes(env):
    result = env.run_remote("cat log")
    assert result.returncode == 0
    assert "�" in result.stdout
    assert result.stdout.startswith("session id: 0b6f3c1e")


def test_recover_session_id_survives_undecodable_log(env, monkeypatch):
    monkeypatch.setattr("cli.last_session.parse_session_id", lambda text: text.split()[2])
    assert env.recover_session_id("claude") == "0b6f3c1e-1111-2222-3333-444455556666"
