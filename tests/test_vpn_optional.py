"""
Optional VPN E2E tests (require wireguard-tools installed)

These tests verify the VPN functionality works when wireguard-tools is available.
They are marked as optional and will be skipped if wireguard-tools is not installed.
"""

import pytest
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_saved_wireguard_config_is_private():
    """wg-quick rejects configs that are world-accessible, including files in /tmp."""
    from cli.vpn import VPNManager, WireGuardConfig

    config = WireGuardConfig(
        interface_name="perm-test",
        private_key="cMxYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
        public_key="B1lYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
        endpoint="vpn.example.com:51820",
        ip_address="10.0.0.2/24",
    )
    manager = VPNManager()
    path = manager.save_wireguard_config("perm-test", config)
    try:
        file_mode = os.stat(path).st_mode
        dir_mode = os.stat(os.path.dirname(path)).st_mode
        assert file_mode & 0o077 == 0
        assert file_mode & dir_mode & 0o007 == 0
        assert not path.startswith("/tmp/wg-")
    finally:
        os.remove(path)


def test_vpn_module_available():
    """Test that VPN module can be imported"""
    try:
        from cli.vpn import VPNManager, WireGuardConfig
        assert VPNManager is not None
        assert WireGuardConfig is not None
    except ImportError as e:
        pytest.skip(f"VPN module not available: {e}")


@pytest.mark.vpn
class TestVPNOPTIONAL:
    """Test VPN functionality (optional, requires wireguard-tools)"""

    @pytest.fixture(autouse=True)
    def check_vpn_tools(self):
        """Check if wireguard-tools are available"""
        try:
            import subprocess
            result = subprocess.run(['wg', '--version'], capture_output=True, timeout=5)
            if result.returncode != 0:
                pytest.skip("wireguard-tools not available")
        except FileNotFoundError:
            pytest.skip("wireguard-tools not installed")

    def test_vpn_config_generation(self):
        """Test VPN configuration generation"""
        from cli.vpn import VPNManager, WireGuardConfig

        config = WireGuardConfig(
            interface_name="test-vpn",
            private_key="cMxYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
            public_key="B1lYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
            endpoint="vpn.example.com:51820",
            ip_address="10.0.0.2/24",
            allowed_ips=["10.0.0.0/8"]
        )

        manager = VPNManager()
        conf = manager.generate_wireguard_conf(config)

        # Verify config contains required fields
        assert "[Interface]" in conf
        assert "[Peer]" in conf
        assert "PrivateKey" in conf
        assert "PublicKey" in conf
        assert "Endpoint" in conf
        assert "10.0.0.2/24" in conf
        assert "vpn.example.com:51820" in conf

    def test_vpn_config_save(self):
        """Test saving VPN configuration to file"""
        from cli.vpn import VPNManager, WireGuardConfig
        import tempfile
        import os

        config = WireGuardConfig(
            interface_name="save-test",
            private_key="cMxYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
            public_key="B1lYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
            endpoint="vpn.example.com:51820",
            ip_address="10.0.0.2/24"
        )

        manager = VPNManager()
        path = manager.save_wireguard_config("save-test", config)

        # Verify file was created
        assert os.path.exists(path)

        # Verify file content
        with open(path) as f:
            saved = f.read()
        conf = manager.generate_wireguard_conf(config)
        assert saved == conf

        # Clean up
        os.remove(path)

    def test_vpn_config_with_ip_no_mask(self):
        """Test VPN config with IP address that doesn't include subnet mask"""
        from cli.vpn import VPNManager, WireGuardConfig

        config = WireGuardConfig(
            interface_name="ip-test",
            private_key="cMxYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
            public_key="B1lYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
            endpoint="vpn.example.com:51820",
            ip_address="10.0.0.2",  # No subnet mask
            subnet_mask="24"
        )

        manager = VPNManager()
        conf = manager.generate_wireguard_conf(config)

        # Verify IP with subnet mask is in config
        assert "10.0.0.2/24" in conf

    def test_vpn_config_with_ip_including_mask(self):
        """Test VPN config with IP address that includes subnet mask"""
        from cli.vpn import VPNManager, WireGuardConfig

        config = WireGuardConfig(
            interface_name="ip-test",
            private_key="cMxYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
            public_key="B1lYm1lW0qP1JZ5J6Z8q1Y4m5Z5J6Z8q1Y4m5Z5J6Z8=",
            endpoint="vpn.example.com:51820",
            ip_address="10.0.0.2/24",  # Already includes subnet mask
            subnet_mask="24"
        )

        manager = VPNManager()
        conf = manager.generate_wireguard_conf(config)

        # Verify IP is NOT doubled
        assert "10.0.0.2/24/24" not in conf
        assert "10.0.0.2/24" in conf
