"""Fill remaining gaps in smaller cli modules."""

import logging
import os
import os as stdlib_os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_agents_helpers():
    from cli import agents

    with pytest.raises(ValueError, match="Unsupported"):
        agents.normalize_agent_type("nope")
    assert agents.agent_config_dir_name("opencode") == ".opencode"
    assert agents.agent_config_dir_name("cursor") == ".cursor"
    assert agents.agent_config_dir_name("claude") == ".claude"
    assert "opencode.json" in agents.agent_remote_paths("opencode")["config_file"]
    assert "cli-config.json" in agents.agent_remote_paths("cursor")["config_file"]
    assert "settings.json" in agents.agent_remote_paths("claude")["config_file"]
    assert agents.prompt_coding_agent(default="bogus", input_fn=lambda _: "") == "opencode"
    assert agents.prompt_coding_agent(default="claude", input_fn=lambda _: "cursor") == "cursor"


def test_log_child_logger():
    from cli import log as logmod

    child = logmod.get_logger("unit")
    assert child.name == "rig.unit"
    assert logmod.get_logger("rig.already") is logging.getLogger("rig.already")
    # Name already under ``rig.`` — no double prefix
    assert logmod.get_logger("rig.child").name == "rig.child"


def test_vpn_config_path_and_explicit_save(tmp_path, monkeypatch):
    from cli.vpn import VPNManager, WireGuardConfig

    cfg = WireGuardConfig(
        private_key="p",
        public_key="k",
        endpoint="e:1",
        ip_address="10.0.0.2/24",
    )
    mgr = VPNManager()
    monkeypatch.setattr("cli.vpn.os.geteuid", lambda: 0)

    # ``cli.vpn.os`` is the stdlib ``os`` module; patch only after saving the real
    # ``makedirs`` or nested paths recurse into our stub.
    real_makedirs = stdlib_os.makedirs

    def fake_makedirs(path, mode=0o777, exist_ok=False):
        if path == "/etc/wireguard":
            return None
        return real_makedirs(path, mode=mode, exist_ok=exist_ok)

    monkeypatch.setattr("cli.vpn.os.makedirs", fake_makedirs)
    assert mgr._config_path("iface") == "/etc/wireguard/wg-iface.conf"
    out = mgr.save_wireguard_config("x", cfg, path=str(tmp_path / "nested" / "wg.conf"))
    assert Path(out).is_file()


def test_last_session_helpers(tmp_path):
    from cli import last_session

    assert last_session.strip_ansi("\x1b[31mhi\x1b[0m") == "hi"
    assert last_session.parse_session_id("") is None
    assert last_session.agent_args_request_session(["--continue"]) is True
    assert last_session.resume_args_for_agent("opencode", "abc") == ["--session", "abc"]
    assert last_session.resume_args_for_agent("claude", "abc") == ["--resume", "abc"]
    assert last_session.extract_session_from_agent_args(["-s=ses_abcd"]) == "ses_abcd"

    directory = str(tmp_path)
    last_session.remember_last_session("e", "sid", agent_type="opencode", directory=directory)
    assert last_session.read_last_session("e", directory=directory)[0] == "sid"
    last_session.clear_last_session(env_name="e", directory=directory)
    assert last_session.read_last_session("e", directory=directory) is None


def test_last_session_parse_and_flags(tmp_path):
    from cli import last_session

    assert last_session.parse_session_id("\x1b[32m\x1b[0m\r") is None
    assert last_session.extract_session_from_agent_args(["-s=", "x"]) is None
    assert last_session.agent_args_request_session(["--", "--continue"]) is True

    directory = tmp_path / "sess-dir"
    directory.mkdir()
    (directory / "a").write_text("x")
    with patch("cli.last_session.os.listdir", side_effect=OSError("denied")):
        last_session.clear_last_session(env_name=None, directory=str(directory))


def test_last_session_read_variants(tmp_path):
    from cli import last_session

    p = tmp_path / "env1"
    p.write_text("id=sid-1\n")
    assert last_session.read_last_session("env1", directory=str(tmp_path)) == ("sid-1", None)

    p.write_text("plain-only-id\n")
    assert last_session.read_last_session("env1", directory=str(tmp_path))[0] == "plain-only-id"


def test_last_env_edge_cases(tmp_path):
    from cli import last_env

    path = str(tmp_path / "last")
    last_env.remember_last_env("", path=path)
    assert not os.path.exists(path)
    last_env.remember_last_env("env1", path=path)
    with patch("os.chmod", side_effect=OSError("x")):
        last_env.remember_last_env("env1", path=path)
    last_env.clear_last_env(name="other", path=path)
    assert last_env.read_last_env(path=path) == "env1"
    last_env.clear_last_env(name="env1", path=path)
    assert last_env.read_last_env(path=path) is None
    last_env.clear_last_env(path=path)


def test_config_profile_helpers(tmp_path):
    from cli import config

    assert config._section_name("default") == "default"
    assert config._section_name("profile x") == "profile x"
    assert config._section_name("prod") == "profile prod"
    assert config._profile_from_section("default") == "default"
    assert config._profile_from_section("profile x") == "x"
    assert config._profile_from_section("bare") == "bare"

    path = str(tmp_path / "cfg")
    cfg = config.RigConfig(path=path, profile="default")
    cfg.set_profile({"hetzner_cloud_api_token": "t", "unknown": "x", "state_endpoint_url": ""})
    cfg.set_profile({"hetzner_cloud_api_token": "t2"})
    cfg.set_profile({"hetzner_cloud_api_token": ""})
    assert "hetzner_cloud_api_token" not in cfg.get_profile()
    cfg.set_profile({"hetzner_cloud_api_token": "tok"}, profile="prod")
    assert "prod" in cfg.list_profiles()
    cfg._parser.add_section("legacy")
    cfg._parser.set("legacy", "hetzner_cloud_api_token", "L")
    assert cfg.get_profile("legacy").get("hetzner_cloud_api_token") == "L"
    with patch("os.chmod", side_effect=OSError):
        cfg.save()


def test_config_identity_roundtrip(tmp_path, monkeypatch):
    from cli import config

    monkeypatch.delenv("RIG_PROFILE", raising=False)
    path = str(tmp_path / "cfg")
    cfg = config.RigConfig(path=path)
    cfg.set_identity("Alice")
    assert cfg.get_identity() == "alice"
    text = open(path, encoding="utf-8").read()
    assert "identity" in text.lower()


def test_config_prompt_secret_fallback(tmp_path, monkeypatch):
    from cli import config

    monkeypatch.delenv("HETZNER_CLOUD_API_TOKEN", raising=False)
    monkeypatch.delenv("HZ_TOKEN", raising=False)
    monkeypatch.delenv("HETZNER_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("HETZNER_S3_SECRET_KEY", raising=False)
    path = str(tmp_path / "cfg")
    cfg = config.RigConfig(path=path, profile="default")

    def bad_getpass(_msg):
        raise RuntimeError("no tty")

    def good_plain(msg):
        m = msg.lower()
        if "token" in m:
            return "tok"
        if "access" in m:
            return "ak"
        if "secret" in m:
            return "sk"
        return ""

    vals = config.ensure_credentials(
        cfg,
        input_fn=good_plain,
        secret_fn=bad_getpass,
        force_prompt=True,
    )
    assert vals["hetzner_cloud_api_token"] == "tok"
    assert vals["state_access_key"] == "ak"


def test_config_ensure_credentials_missing_raises(tmp_path, monkeypatch):
    from cli import config

    for key in (
        "HETZNER_CLOUD_API_TOKEN",
        "HZ_TOKEN",
        "HETZNER_S3_ACCESS_KEY",
        "HETZNER_S3_SECRET_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    path = str(tmp_path / "cfg")
    cfg = config.RigConfig(path=path)

    with pytest.raises(ValueError, match="token"):
        config.ensure_credentials(
            cfg,
            input_fn=lambda _m: "",
            secret_fn=lambda _m: "",
            force_prompt=True,
        )

    with pytest.raises(ValueError, match="S3"):
        config.ensure_credentials(
            cfg,
            input_fn=lambda m: "x" if "endpoint" in m.lower() else "",
            secret_fn=lambda m: "t" if "token" in m.lower() else "",
            force_prompt=True,
        )
