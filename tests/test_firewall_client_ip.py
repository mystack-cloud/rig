"""Tests for client-IP firewall modes."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.remote_dev import (
    ProjectConfig,
    RemoteDevEnvironment,
    RemoteDevManager,
    as_cidr,
    default_firewall_rules,
    detect_client_public_ip,
    firewall_config_needs_client_ip,
    firewall_source_ips,
    normalize_firewall_ports,
    resolve_firewall_mode,
)


def test_as_cidr_normalizes_bare_ip():
    assert as_cidr("203.0.113.10") == "203.0.113.10/32"
    assert as_cidr("10.0.0.0/8") == "10.0.0.0/8"


def test_resolve_firewall_mode_defaults_to_dynamic_add():
    assert resolve_firewall_mode({}) == "dynamic_add"
    assert resolve_firewall_mode({"mode": "add"}) == "dynamic_add"
    assert resolve_firewall_mode({"mode": "replace"}) == "dynamic_replace"
    assert resolve_firewall_mode({"mode": "open"}) == "open"
    assert resolve_firewall_mode({"mode": "static"}) == "static"


def test_resolve_firewall_mode_legacy_lock_flag():
    assert resolve_firewall_mode({"lock_to_client_ip": True}) == "dynamic_add"
    assert resolve_firewall_mode({"lock_to_client_ip": False}) == "open"


def test_firewall_source_ips_dynamic_add_preserves_existing():
    sources = firewall_source_ips(
        {"mode": "dynamic_add"},
        client_ip="203.0.113.9",
        existing_ips=["198.51.100.1/32", "203.0.113.0/24"],
    )
    assert sources == [
        "198.51.100.1/32",
        "203.0.113.0/24",
        "203.0.113.9/32",
    ]


def test_firewall_source_ips_dynamic_add_drops_world_open_when_locking():
    sources = firewall_source_ips(
        {"mode": "dynamic_add"},
        client_ip="203.0.113.9",
        existing_ips=["0.0.0.0/0", "::/0"],
    )
    assert sources == ["203.0.113.9/32"]


def test_firewall_source_ips_dynamic_replace_overwrites():
    sources = firewall_source_ips(
        {"mode": "dynamic_replace", "allowed_ips": ["10.0.0.0/8"]},
        client_ip="203.0.113.9",
        existing_ips=["198.51.100.1/32"],
    )
    assert sources == ["203.0.113.9/32", "10.0.0.0/8"]
    assert "198.51.100.1/32" not in sources


def test_firewall_source_ips_static_and_open():
    assert firewall_source_ips(
        {"mode": "static", "allowed_ips": ["203.0.113.0/24"]}
    ) == ["203.0.113.0/24"]
    assert "0.0.0.0/0" in firewall_source_ips({"mode": "open"})


def test_detect_client_public_ip_parses_ipify(monkeypatch):
    class FakeResp:
        ok = True
        text = "203.0.113.55\n"

    monkeypatch.setattr(
        "cli.remote_dev.requests.get",
        lambda *a, **k: FakeResp(),
    )
    assert detect_client_public_ip() == "203.0.113.55"


def test_normalize_firewall_ports_accepts_forms():
    ports = normalize_firewall_ports(
        [8080, "3000-3001", {"port": 53, "protocol": "udp", "description": "DNS"}]
    )
    assert ports == [
        {
            "protocol": "tcp",
            "port": "8080",
            "description": "Allow TCP 8080",
            "mode": None,
            "allowed_ips": None,
        },
        {
            "protocol": "tcp",
            "port": "3000-3001",
            "description": "Allow TCP 3000-3001",
            "mode": None,
            "allowed_ips": None,
        },
        {
            "protocol": "udp",
            "port": "53",
            "description": "DNS",
            "mode": None,
            "allowed_ips": None,
        },
    ]


def test_default_firewall_rules_include_app_ports():
    rules = default_firewall_rules(
        ["203.0.113.9/32"],
        ports=[8080, {"port": 53, "protocol": "udp"}],
    )
    ports = {(r["protocol"], str(r.get("port"))) for r in rules}
    assert ("tcp", "22") in ports
    assert ("tcp", "8080") in ports
    assert ("udp", "53") in ports
    app = next(r for r in rules if r.get("port") == "8080")
    assert app["source_ips"] == ["203.0.113.9/32"]


def test_port_overrides_mode_and_allowed_ips():
    firewall = {"mode": "dynamic_add", "allowed_ips": ["203.0.113.0/24"]}
    rules = default_firewall_rules(
        ["198.51.100.1/32", "203.0.113.9/32", "203.0.113.0/24"],
        ports=[
            8080,
            {"port": 443, "mode": "open"},
            {"port": 9090, "mode": "static", "allowed_ips": ["198.51.100.10/32"]},
            {"port": 8443, "allowed_ips": ["192.0.2.10/32"]},
            {"port": 7443, "mode": "dynamic_replace", "allowed_ips": ["10.1.0.0/16"]},
            {"port": 9443, "mode": "static", "allowed_ips": []},
            {"port": 22, "mode": "open"},
        ],
        firewall_cfg=firewall,
        client_ip="203.0.113.9",
        existing_port_ips={
            ("tcp", "8443"): ["192.0.2.5/32"],
            ("tcp", "7443"): ["198.51.100.9/32"],
        },
    )
    by_port = {rule.get("port"): rule["source_ips"] for rule in rules if rule.get("port")}
    ssh = ["198.51.100.1/32", "203.0.113.9/32", "203.0.113.0/24"]
    assert by_port["22"] == ssh
    assert by_port["8080"] == ssh
    assert by_port["443"] == ["0.0.0.0/0", "::/0"]
    assert by_port["9090"] == ["198.51.100.10/32"]
    assert by_port["8443"] == ["192.0.2.5/32", "203.0.113.9/32", "192.0.2.10/32"]
    assert "203.0.113.0/24" not in by_port["8443"]
    assert by_port["7443"] == ["203.0.113.9/32", "10.1.0.0/16"]
    assert "9443" not in by_port
    icmp = next(rule for rule in rules if rule["protocol"] == "icmp")
    assert icmp["source_ips"] == ssh


def test_unknown_port_mode_inherits_and_duplicate_is_skipped(caplog):
    import logging

    caplog.set_level(logging.WARNING)
    ports = normalize_firewall_ports(
        [
            {"port": 443, "mode": "nope"},
            {"port": 443, "mode": "open"},
            {"port": 53, "protocol": "udp", "allowed_ips": []},
        ]
    )
    assert ports[0]["mode"] is None
    assert ports[0]["allowed_ips"] is None
    assert len([item for item in ports if item["port"] == "443"]) == 1
    assert ports[1]["allowed_ips"] == []
    assert any("Unknown firewall port mode" in record.message for record in caplog.records)
    assert any("duplicate" in record.message for record in caplog.records)


def test_static_firewall_still_opens_when_allowed_ips_empty():
    assert "0.0.0.0/0" in firewall_source_ips({"mode": "static", "allowed_ips": []})


def test_client_ip_required_when_only_a_port_is_dynamic():
    cfg = {"mode": "static", "allowed_ips": ["203.0.113.0/24"]}
    assert firewall_config_needs_client_ip(cfg, [{"port": 443, "mode": "open"}]) is False
    assert firewall_config_needs_client_ip(
        cfg, [{"port": 8080, "mode": "dynamic_add"}]
    ) is True


def test_set_firewall_rules_posts_per_port_sources():
    manager = RemoteDevManager(hetzner_token="tok")
    captured = {}

    def fake_request(_method, _path, data=None):
        captured["data"] = data
        return {"action": {}}

    manager._request = fake_request  # type: ignore[method-assign]
    manager.set_firewall_rules(
        1,
        ["203.0.113.9/32"],
        ports=[{"port": 443, "mode": "open"}, {"port": 9090, "allowed_ips": ["198.51.100.10/32"]}],
        firewall_cfg={"mode": "dynamic_add", "allowed_ips": ["203.0.113.0/24"]},
        client_ip="203.0.113.9",
    )
    by_port = {
        rule.get("port"): rule["source_ips"]
        for rule in captured["data"]["rules"]
        if rule.get("port")
    }
    assert by_port["22"] == ["203.0.113.9/32"]
    assert by_port["443"] == ["0.0.0.0/0", "::/0"]
    assert by_port["9090"] == ["203.0.113.9/32", "198.51.100.10/32"]


def test_ensure_firewall_access_rewrites_port_sources(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    cfg.set("firewall.ports", [{"port": 443, "mode": "open"}])
    cfg.save(write_vars=False)

    env = RemoteDevEnvironment(name="dev-port-mode")
    env.firewall_id = 15
    env.manager = MagicMock()
    env.manager.get_firewall.return_value = {
        "firewall": {
            "rules": [
                {
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "22",
                    "source_ips": ["198.51.100.1/32", "203.0.113.9/32"],
                },
                {
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "443",
                    "source_ips": ["198.51.100.1/32", "203.0.113.9/32"],
                },
            ]
        }
    }

    with patch("cli.remote_dev.detect_client_public_ip", return_value="203.0.113.9"):
        assert env.ensure_firewall_access(project_dir=str(tmp_path)) is True

    kwargs = env.manager.set_firewall_rules.call_args.kwargs
    assert kwargs["client_ip"] == "203.0.113.9"
    assert kwargs["existing_port_ips"][("tcp", "443")] == [
        "198.51.100.1/32",
        "203.0.113.9/32",
    ]
    rules = default_firewall_rules(
        env.manager.set_firewall_rules.call_args.args[1],
        ports=kwargs["ports"],
        firewall_cfg=kwargs["firewall_cfg"],
        client_ip=kwargs["client_ip"],
        existing_port_ips=kwargs["existing_port_ips"],
    )
    by_port = {rule.get("port"): rule["source_ips"] for rule in rules if rule.get("port")}
    assert by_port["22"] == ["198.51.100.1/32", "203.0.113.9/32"]
    assert by_port["443"] == ["0.0.0.0/0", "::/0"]


def test_ensure_firewall_access_skips_when_port_sources_match(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    cfg.set("firewall.ports", [{"port": 443, "mode": "open"}])
    cfg.save(write_vars=False)

    env = RemoteDevEnvironment(name="dev-port-open")
    env.firewall_id = 16
    env.manager = MagicMock()
    env.manager.get_firewall.return_value = {
        "firewall": {
            "rules": [
                {
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "22",
                    "source_ips": ["198.51.100.1/32", "203.0.113.9/32"],
                },
                {
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "443",
                    "source_ips": ["0.0.0.0/0", "::/0"],
                },
            ]
        }
    }

    with patch("cli.remote_dev.detect_client_public_ip", return_value="203.0.113.9"):
        assert env.ensure_firewall_access(project_dir=str(tmp_path)) is True

    env.manager.set_firewall_rules.assert_not_called()


def test_init_writes_firewall_mode_default(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    assert cfg.config["firewall"]["mode"] == "dynamic_add"
    assert cfg.config["firewall"]["allowed_ips"] == []
    assert cfg.config["firewall"]["ports"] == []


def test_ensure_firewall_access_adds_without_removing(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="dev-fw")
    env.firewall_id = 99
    env.manager = MagicMock()
    env.manager.get_firewall.return_value = {
        "firewall": {
            "rules": [
                {
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "22",
                    "source_ips": ["198.51.100.1/32"],
                }
            ]
        }
    }

    with patch("cli.remote_dev.detect_client_public_ip", return_value="203.0.113.9"):
        assert env.ensure_firewall_access(project_dir=str(tmp_path)) is True

    env.manager.set_firewall_rules.assert_called_once()
    args = env.manager.set_firewall_rules.call_args
    assert args.args[0] == 99
    assert args.args[1] == ["198.51.100.1/32", "203.0.113.9/32"]


def test_ensure_firewall_access_syncs_ports(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    cfg.set("firewall.ports", [8080])
    cfg.save(write_vars=False)

    env = RemoteDevEnvironment(name="dev-ports")
    env.firewall_id = 11
    env.manager = MagicMock()
    env.manager.get_firewall.return_value = {
        "firewall": {
            "rules": [
                {
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "22",
                    "source_ips": ["203.0.113.9/32"],
                }
            ]
        }
    }

    with patch("cli.remote_dev.detect_client_public_ip", return_value="203.0.113.9"):
        assert env.ensure_firewall_access(project_dir=str(tmp_path)) is True

    kwargs = env.manager.set_firewall_rules.call_args.kwargs
    assert kwargs.get("ports") == [8080]


def test_ensure_firewall_access_replace_mode(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")
    cfg.set("firewall.mode", "dynamic_replace")
    cfg.save(write_vars=False)

    env = RemoteDevEnvironment(name="dev-fw-replace")
    env.firewall_id = 7
    env.manager = MagicMock()
    env.manager.get_firewall.return_value = {
        "firewall": {
            "rules": [
                {
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "22",
                    "source_ips": ["198.51.100.1/32", "203.0.113.0/24"],
                }
            ]
        }
    }

    with patch("cli.remote_dev.detect_client_public_ip", return_value="203.0.113.9"):
        assert env.ensure_firewall_access(project_dir=str(tmp_path)) is True

    assert env.manager.set_firewall_rules.call_args.args[1] == ["203.0.113.9/32"]


def test_ensure_firewall_access_skips_when_already_present(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="ws", agent_type="opencode")

    env = RemoteDevEnvironment(name="dev-fw2")
    env.firewall_id = 42
    env.manager = MagicMock()
    env.manager.get_firewall.return_value = {
        "firewall": {
            "rules": [
                {
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "22",
                    "source_ips": ["198.51.100.1/32", "203.0.113.9/32"],
                }
            ]
        }
    }

    with patch("cli.remote_dev.detect_client_public_ip", return_value="203.0.113.9"):
        assert env.ensure_firewall_access(project_dir=str(tmp_path)) is True

    env.manager.set_firewall_rules.assert_not_called()


def test_attach_refreshes_firewall(monkeypatch):
    from cli.main import main

    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "tok")
    instance = MagicMock()
    instance.resolve_resources.return_value = True
    instance.vm_ip = "1.2.3.4"
    instance.ssh_key_name = "dev-x-key"
    instance.name = "dev-x"
    instance.attach_to_agent.return_value = 0

    with open("/tmp/dev-x-key", "w", encoding="utf-8") as handle:
        handle.write("key\n")
    try:
        with patch("cli.main.RemoteDevEnvironment", return_value=instance), patch(
            "cli.main.StorageManager", side_effect=ValueError("no")
        ), patch("cli.main.resolve_env_name", return_value="dev-x"), patch(
            "cli.main.remember_last_env"
        ):
            assert main(["attach"]) == 0
        instance.ensure_firewall_access.assert_called_once()
    finally:
        try:
            os.remove("/tmp/dev-x-key")
        except OSError:
            pass
