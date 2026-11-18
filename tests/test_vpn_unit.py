"""Unit tests for cli.vpn with mocked subprocess (no wireguard-tools required)."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture
def wg_config():
    from cli.vpn import WireGuardConfig

    return WireGuardConfig(
        interface_name="wg0",
        private_key="priv",
        public_key="pub",
        endpoint="vpn.example.com:51820",
        ip_address="10.0.0.2",
        subnet_mask="24",
        allowed_ips=["10.0.0.0/8"],
    )


class TestVPNManagerUnit:
    def test_generate_keys_success(self):
        from cli.vpn import VPNManager

        priv = MagicMock(returncode=0, stdout="PRIVATE\n", stderr="")
        pub = MagicMock(returncode=0, stdout="PUBLIC\n", stderr="")
        with patch("cli.vpn.subprocess.run", side_effect=[priv, pub]) as run:
            keys = VPNManager().generate_wireguard_keys()
        assert keys == {"private_key": "PRIVATE", "public_key": "PUBLIC"}
        assert run.call_count == 2

    def test_generate_keys_private_failure(self):
        from cli.vpn import VPNManager

        bad = MagicMock(returncode=1, stdout="", stderr="nope")
        with patch("cli.vpn.subprocess.run", return_value=bad):
            with pytest.raises(RuntimeError, match="private key"):
                VPNManager().generate_wireguard_keys()

    def test_generate_keys_public_failure(self):
        from cli.vpn import VPNManager

        priv = MagicMock(returncode=0, stdout="PRIVATE\n", stderr="")
        pub = MagicMock(returncode=1, stdout="", stderr="bad")
        with patch("cli.vpn.subprocess.run", side_effect=[priv, pub]):
            with pytest.raises(RuntimeError, match="public key"):
                VPNManager().generate_wireguard_keys()

    def test_generate_keys_missing_tools(self):
        from cli.vpn import VPNManager

        with patch("cli.vpn.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="WireGuard tools"):
                VPNManager().generate_wireguard_keys()

    def test_create_wireguard_config_defaults(self):
        from cli.vpn import VPNManager

        mgr = VPNManager({"interface_name": "wg1"})
        cfg = mgr.create_wireguard_config("dev", {"private_key": "p", "public_key": "k"})
        assert cfg.interface_name == "wg1"
        assert mgr.vpn_configs["dev"] is cfg

    def test_generate_conf_adds_mask(self, wg_config):
        from cli.vpn import VPNManager

        conf = VPNManager().generate_wireguard_conf(wg_config)
        assert "Address = 10.0.0.2/24" in conf
        assert "AllowedIPs = 10.0.0.0/8" in conf

    def test_setup_vpn_tunnel_success(self, wg_config, tmp_path):
        from cli.vpn import VPNManager

        mgr = VPNManager()
        path = str(tmp_path / "wg0.conf")
        with patch.object(mgr, "save_wireguard_config", return_value=path):
            with patch(
                "cli.vpn.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="", stderr=""),
            ):
                assert mgr.setup_vpn_tunnel("wg0", wg_config) is True

    def test_setup_vpn_tunnel_failure_and_missing(self, wg_config):
        from cli.vpn import VPNManager

        mgr = VPNManager()
        with patch.object(mgr, "save_wireguard_config", return_value="/x.conf"):
            with patch(
                "cli.vpn.subprocess.run",
                return_value=MagicMock(returncode=1, stdout="", stderr="fail"),
            ):
                assert mgr.setup_vpn_tunnel("wg0", wg_config) is False
            with patch("cli.vpn.subprocess.run", side_effect=FileNotFoundError):
                assert mgr.setup_vpn_tunnel("wg0", wg_config) is False
            with patch("cli.vpn.subprocess.run", side_effect=RuntimeError("boom")):
                assert mgr.setup_vpn_tunnel("wg0", wg_config) is False

    def test_teardown_vpn_tunnel(self, wg_config):
        from cli.vpn import VPNManager

        mgr = VPNManager()
        mgr._config_paths["wg0"] = "/etc/wireguard/wg-wg0.conf"
        with patch(
            "cli.vpn.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ):
            assert mgr.teardown_vpn_tunnel("wg0") is True
        with patch(
            "cli.vpn.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="down-fail"),
        ):
            assert mgr.teardown_vpn_tunnel("wg0") is False
        with patch("cli.vpn.subprocess.run", side_effect=OSError("x")):
            assert mgr.teardown_vpn_tunnel("wg0") is False

    def test_check_vpn_status_variants(self):
        from cli.vpn import VPNManager

        mgr = VPNManager()
        show_if = MagicMock(returncode=0, stdout="wg0\nwg1\n", stderr="")
        show_det = MagicMock(returncode=0, stdout="peer: abc", stderr="")
        with patch("cli.vpn.subprocess.run", side_effect=[show_if, show_det]):
            status = mgr.check_vpn_status("wg0")
        assert status["up"] is True
        assert "peer" in status["status"]

        bad = MagicMock(returncode=1, stdout="", stderr="err")
        with patch("cli.vpn.subprocess.run", return_value=bad):
            status = mgr.check_vpn_status("wg0")
        assert status["up"] is False
        assert status["error"] == "err"

        with patch("cli.vpn.subprocess.run", side_effect=RuntimeError("nope")):
            status = mgr.check_vpn_status("wg0")
        assert status["status"] == "error"

    def test_list_active_tunnels(self):
        from cli.vpn import VPNManager

        with patch(
            "cli.vpn.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="wg0\nwg1\n", stderr=""),
        ):
            assert VPNManager().list_active_tunnels() == ["wg0", "wg1"]
        with patch(
            "cli.vpn.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr=""),
        ):
            assert VPNManager().list_active_tunnels() == []
        with patch("cli.vpn.subprocess.run", side_effect=OSError):
            assert VPNManager().list_active_tunnels() == []

    def test_setup_vpn_with_credentials(self, wg_config):
        from cli.vpn import VPNManager

        mgr = VPNManager()
        with patch.object(mgr, "generate_wireguard_keys", return_value={"private_key": "L", "public_key": "P"}):
            with patch.object(mgr, "setup_vpn_tunnel", return_value=True) as setup:
                assert mgr.setup_vpn_with_credentials("wg0", "host:51820", "ignored", "peerpub", ["10.0.0.0/8"]) is True
                cfg = setup.call_args.args[1]
                assert cfg.private_key == "L"
                assert cfg.public_key == "peerpub"

    def test_module_helpers(self):
        from cli import vpn

        with patch.object(
            vpn.VPNManager, "generate_wireguard_keys", return_value={"private_key": "a", "public_key": "b"}
        ):
            with patch.object(vpn.VPNManager, "setup_vpn_tunnel", return_value=True):
                assert vpn.setup_remote_vpn_connection({"endpoint": "e", "peer_public_key": "p"}) is True
        with patch.object(vpn.VPNManager, "generate_wireguard_keys", side_effect=RuntimeError("x")):
            assert vpn.setup_remote_vpn_connection({}) is False
        with patch.object(vpn.VPNManager, "check_vpn_status", return_value={"up": True}):
            assert vpn.get_vpn_status("wg0")["up"] is True
        with patch.object(vpn.VPNManager, "teardown_vpn_tunnel", return_value=True):
            assert vpn.teardown_vpn_connection("wg0") is True
