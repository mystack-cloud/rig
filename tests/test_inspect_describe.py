"""`rig inspect` and `rig describe` (ADR 0002)."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli import outputs as out  # noqa: E402
from cli.main import main  # noqa: E402
from cli.remote_dev import RemoteDevEnvironment  # noqa: E402

LAST = {"name": None}


@pytest.fixture
def tok_env(monkeypatch):
    """Token set; last-env is faked in memory and never touches /tmp/.rig."""
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "tok")
    LAST["name"] = None

    def fake_resolve(*candidates):
        for value in candidates:
            if value and str(value).strip():
                return str(value).strip()
        return LAST["name"]

    monkeypatch.setattr("cli.main.resolve_env_name", fake_resolve)
    monkeypatch.setattr("cli.main.remember_last_env", lambda name: LAST.update(name=name))


# ---- formatter ------------------------------------------------------------


def test_format_kv_nested_and_none():
    text = out.format_kv({"a": 1, "b": {"c": "x", "d": None}, "e": ["p", "q"]})
    assert text.splitlines() == ["a: 1", "b:", "  c: x", "  d: -", "e: p, q"]


# ---- RemoteDevEnvironment.inspect_details ----------------------------------


def _env_with(record, server=None, server_error=None):
    env = RemoteDevEnvironment(name="dev-1")
    env.manager = MagicMock()
    env.manager.find_record.return_value = record
    if server_error:
        env.manager.get_vm.side_effect = server_error
    else:
        env.manager.get_vm.return_value = server
    env.resolve_resources = MagicMock(return_value=True)
    env.vm_id = (record or {}).get("id")
    return env


RECORD = {
    "name": "dev-1",
    "id": 42,
    "ip": "1.1.1.1",
    "type": "cax21",
    "region": "hel1",
    "owner": "alice",
    "ssh_key_id": 7,
    "firewall_id": 9,
    "status": "active",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
}
SERVER = {
    "server": {
        "status": "running",
        "created": "2026-01-01T00:00:01+00:00",
        "public_net": {"ipv4": {"ip": "2.2.2.2"}},
        "server_type": {"name": "cax21"},
        "datacenter": {"name": "hel1-dc2"},
    }
}


def test_inspect_merges_record_and_live():
    d = _env_with(RECORD, SERVER).inspect_details()
    assert d["name"] == "dev-1" and d["owner"] == "alice"
    assert d["status"] == "running"  # live, not the stored literal 'active'
    assert d["ip"] == "2.2.2.2"  # live ip wins
    assert d["stale"] is False
    assert d["ssh_key_id"] == 7 and d["firewall_id"] == 9
    assert d["created_at"] == "2026-01-01T00:00:00Z"
    assert d["connection"]["user"] == "dev"
    assert d["connection"]["host"] == "2.2.2.2"
    assert d["connection"]["ssh_key_path"].startswith("/tmp/")


def test_inspect_falls_back_to_stored_when_api_fails():
    d = _env_with(RECORD, server_error=RuntimeError("down")).inspect_details()
    assert d["stale"] is True
    assert d["status"] is None
    assert d["ip"] == "1.1.1.1"


def test_inspect_unknown_environment():
    env = _env_with(None)
    env.vm_id = None
    assert env.inspect_details() is None


# ---- rig inspect ------------------------------------------------------------


def _patched(inst):
    return (
        patch("cli.main._load_credentials"),
        patch("cli.main.StorageManager"),
        patch("cli.main.RemoteDevEnvironment", return_value=inst),
    )


def run(argv, inst):
    a, b, c = _patched(inst)
    with a, b, c as cls:
        rc = main(argv)
    return rc, cls


def test_inspect_text_goes_to_stdout(tok_env, capsys):
    inst = MagicMock()
    inst.inspect_details.return_value = {"name": "dev-1", "ip": "1.1.1.1"}
    rc, cls = run(["inspect", "dev-1"], inst)
    cap = capsys.readouterr()
    assert rc == 0
    assert "name: dev-1" in cap.out and "ip: 1.1.1.1" in cap.out
    assert cls.call_args.kwargs["name"] == "dev-1"


def test_inspect_json(tok_env, capsys):
    inst = MagicMock()
    inst.inspect_details.return_value = {"name": "dev-1", "stale": False}
    rc, _ = run(["inspect", "--name", "dev-1", "--json"], inst)
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"name": "dev-1", "stale": False}


def test_inspect_stale_note_on_stderr(tok_env, capsys):
    inst = MagicMock()
    inst.inspect_details.return_value = {"name": "dev-1", "stale": True}
    rc, _ = run(["inspect", "dev-1"], inst)
    cap = capsys.readouterr()
    assert rc == 0 and "stale" in cap.err.lower()


def test_inspect_unknown_environment_fails(tok_env, capsys):
    inst = MagicMock()
    inst.inspect_details.return_value = None
    rc, _ = run(["inspect", "nope"], inst)
    assert rc == 1 and "nope" in capsys.readouterr().err


def test_inspect_falls_back_to_last_env(tok_env, capsys):
    LAST["name"] = "dev-last"
    inst = MagicMock()
    inst.inspect_details.return_value = {"name": "dev-last"}
    rc, cls = run(["inspect"], inst)
    assert rc == 0 and cls.call_args.kwargs["name"] == "dev-last"


def test_inspect_requires_a_name(tok_env, capsys):
    rc, _ = run(["inspect"], MagicMock())
    assert rc == 1 and "name is required" in capsys.readouterr().err.lower()


def test_inspect_requires_token(monkeypatch, capsys):
    monkeypatch.delenv("HETZNER_CLOUD_API_TOKEN", raising=False)
    with patch("cli.main._load_credentials"):
        assert main(["inspect", "dev-1"]) == 1


# ---- rig describe -----------------------------------------------------------

STORED = {
    "rig_outputs": {"url": "http://vm", "password": "hunter2", "custom": "c"},
    "rig_outputs_sensitive": ["custom"],
    "updated_at": "2026-01-01T00:00:00Z",
}


def _desc_inst(stored=STORED, exists=True):
    inst = MagicMock()
    inst.exists.return_value = exists
    inst.stored_outputs.return_value = stored
    return inst


def test_describe_masks_by_default(tok_env, capsys):
    rc, _ = run(["describe", "dev-1"], _desc_inst())
    cap = capsys.readouterr()
    assert rc == 0
    assert "url: http://vm" in cap.out
    assert "hunter2" not in cap.out and "password: ********" in cap.out
    assert "custom: ********" in cap.out


def test_describe_show_secrets(tok_env, capsys):
    rc, _ = run(["describe", "dev-1", "--show-secrets"], _desc_inst())
    assert "password: hunter2" in capsys.readouterr().out


def test_describe_json_masked_and_unmasked(tok_env, capsys):
    rc, _ = run(["describe", "dev-1", "--json"], _desc_inst())
    data = json.loads(capsys.readouterr().out)
    assert data == {"url": "http://vm", "password": out.MASK, "custom": out.MASK}
    rc, _ = run(["describe", "dev-1", "--json", "--show-secrets"], _desc_inst())
    assert json.loads(capsys.readouterr().out)["password"] == "hunter2"


def test_describe_no_outputs_recorded(tok_env, capsys):
    rc, _ = run(["describe", "dev-1"], _desc_inst(stored=None))
    cap = capsys.readouterr()
    assert rc == 0 and "No outputs recorded for dev-1" in cap.err and cap.out == ""
    rc, _ = run(["describe", "dev-1", "--json"], _desc_inst(stored=None))
    assert json.loads(capsys.readouterr().out) == {}


def test_describe_unknown_environment_fails(tok_env, capsys):
    rc, _ = run(["describe", "nope"], _desc_inst(exists=False))
    assert rc == 1 and "nope" in capsys.readouterr().err


def test_describe_without_storage_fails(tok_env, capsys):
    with (
        patch("cli.main._load_credentials"),
        patch("cli.main.StorageManager", side_effect=ValueError("no creds")),
        patch("cli.main.RemoteDevEnvironment"),
    ):
        assert main(["describe", "dev-1"]) == 1
    assert "storage" in capsys.readouterr().err.lower()


# ---- RemoteDevEnvironment.exists / stored_outputs ---------------------------


def test_env_exists_and_stored_outputs():
    env = RemoteDevEnvironment(name="dev-1")
    env.manager = MagicMock()
    env.manager.find_record.return_value = {"name": "dev-1"}
    env.manager.load_outputs.return_value = {"rig_outputs": {"a": 1}}
    assert env.exists() is True
    assert env.stored_outputs() == {"rig_outputs": {"a": 1}}
    env.manager.find_record.return_value = None
    assert env.exists() is False


# ---- error paths -------------------------------------------------------------


def test_inspect_unexpected_error_is_reported(tok_env, capsys):
    inst = MagicMock()
    inst.inspect_details.side_effect = RuntimeError("boom")
    rc, _ = run(["inspect", "dev-1"], inst)
    assert rc == 1 and "boom" in capsys.readouterr().err


def test_describe_unexpected_error_is_reported(tok_env, capsys):
    inst = _desc_inst()
    inst.stored_outputs.side_effect = RuntimeError("boom")
    rc, _ = run(["describe", "dev-1"], inst)
    assert rc == 1 and "boom" in capsys.readouterr().err


def test_inspect_stale_when_server_missing_or_no_id():
    env = _env_with(RECORD, server={"server": {}})
    assert env.inspect_details()["stale"] is True
    env = _env_with({**RECORD, "id": None})
    env.vm_id = None
    d = env.inspect_details()
    assert d["stale"] is True and d["ip"] == "1.1.1.1"
