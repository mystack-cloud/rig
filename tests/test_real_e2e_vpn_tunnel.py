"""
Real E2E WireGuard VPN tunnel test (require real Hetzner credentials and wireguard-tools)

This test creates a VM, provisions WireGuard on it, establishes a VPN tunnel,
and verifies the tunnel is actually working by checking connectivity.

Test flow:
1. Create VM on Hetzner Cloud
2. Provision VM with WireGuard gateway via Ansible
3. Get WireGuard gateway public key and endpoint (VM IP)
4. Generate local WireGuard keys
5. Establish VPN tunnel to gateway
6. Verify tunnel is working (ping gateway, curl through tunnel)
7. Tear down VPN tunnel
8. Clean up VM
"""

import pytest
import os
import sys
import time
import subprocess
import uuid

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import vpn module for type hints
import cli.vpn as vpn  # type: ignore


def test_requires_credentials():
    """Check if credentials are available for E2E tests"""
    assert os.getenv('HETZNER_CLOUD_API_TOKEN') is not None, "Requires HETZNER_CLOUD_API_TOKEN environment variable"


@pytest.mark.requires_credentials
@pytest.mark.vpn
@pytest.mark.integration
@pytest.mark.e2e
class TestRealE2EVPTunnel:
    """Test real E2E VPN tunnel establishment and verification"""

    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")

    @pytest.fixture(autouse=True)
    def check_wireguard_tools(self):
        """Tunnel setup needs wireguard-tools and permission to create interfaces."""
        try:
            result = subprocess.run(['wg', '--version'], capture_output=True, timeout=5)
            if result.returncode != 0:
                pytest.skip("wireguard-tools not available")
        except FileNotFoundError:
            pytest.skip("wireguard-tools not installed")
        if os.geteuid() != 0:
            pytest.skip("WireGuard tunnel setup requires root (wg-quick)")

    @pytest.fixture
    def vpn_vm_name(self, hetzner_token):
        """Generate unique VM name for VPN gateway"""
        timestamp = int(uuid.uuid4().time_low)
        return f"vpn-gw-{hetzner_token[:8].lower()}-{timestamp}"

    @pytest.fixture
    def test_vpn_interface(self, hetzner_token):
        """Generate unique VPN interface name"""
        # wg-quick names the interface after the config file, which is wg-<name>.
        # Interface names must be 15 characters or fewer, so keep this suffix short.
        return f"e2e{uuid.uuid4().hex[:8]}"

    def test_e2e_vpn_tunnel_established_and_verified(self, hetzner_token, vpn_vm_name, test_vpn_interface):
        """
        Test complete VPN tunnel lifecycle:
        1. Create remote VM
        2. Provision with WireGuard gateway
        3. Establish VPN tunnel
        4. Verify connectivity through tunnel
        5. Tear down
        6. Clean up
        """
        print(f"\n{'='*60}")
        print(f"Creating VPN gateway VM: {vpn_vm_name}")
        print(f"{'='*60}")

        from cli.remote_dev import RemoteDevEnvironment

        interface_name = f"wg-{test_vpn_interface}"
        env = RemoteDevEnvironment(
            name=vpn_vm_name,
            config={
                'firewall': {
                    'extra_rules': [{
                        'direction': 'in',
                        'protocol': 'udp',
                        'port': '51820',
                        'source_ips': ['0.0.0.0/0', '::/0'],
                        'description': 'WireGuard',
                    }],
                },
            },
        )
        env.name = vpn_vm_name
        env.vm_name = vpn_vm_name
        vpn_manager = vpn.VPNManager()
        config_path = None
        try:
            assert env.setup(), "VPN gateway VM setup should succeed"
            assert env.vm_ip is not None
            ssh_key_path = f"/tmp/{env.ssh_key_name}"
            self._provision_wireguard_gateway(env.vm_ip, ssh_key_path)

            gateway_public_key = self._ssh(env.vm_ip, ssh_key_path, 'wg show wg0 public-key').stdout.strip()
            assert gateway_public_key, "Gateway did not return a WireGuard public key"

            local_keys = vpn_manager.generate_wireguard_keys()
            peer = self._ssh(
                env.vm_ip,
                ssh_key_path,
                f"wg set wg0 peer {local_keys['public_key']} allowed-ips 10.0.0.2/32",
            )
            assert peer.returncode == 0, peer.stderr

            config = vpn.WireGuardConfig(
                interface_name=test_vpn_interface,
                private_key=local_keys['private_key'],
                public_key=gateway_public_key,
                endpoint=f"{env.vm_ip}:51820",
                ip_address="10.0.0.2/24",
                allowed_ips=["10.0.0.1/32"],
            )
            config_path = vpn_manager.save_wireguard_config(test_vpn_interface, config)
            assert vpn_manager.setup_vpn_tunnel(test_vpn_interface, config), "Failed to establish VPN tunnel"

            time.sleep(2)
            result = subprocess.run(
                ['wg', 'show', interface_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"VPN interface {interface_name} not active"

            ping_result = subprocess.run(
                ['ping', '-c', '3', '-W', '5', '10.0.0.1'],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert ping_result.returncode == 0, ping_result.stderr or ping_result.stdout
        finally:
            vpn_manager.teardown_vpn_tunnel(test_vpn_interface)
            if config_path and os.path.exists(config_path):
                os.remove(config_path)
            env.cleanup()

    def _ssh(self, vm_ip: str, ssh_key_path: str, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                'ssh', '-i', ssh_key_path,
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'IdentitiesOnly=yes',
                '-o', 'BatchMode=yes',
                f'root@{vm_ip}',
                command,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _provision_wireguard_gateway(self, vm_ip: str, ssh_key_path: str):
        """Provision VM with WireGuard gateway via Ansible"""
        import tempfile

        inventory = (
            "[remote_vm]\n"
            f"{vm_ip} ansible_user=root ansible_ssh_private_key_file={ssh_key_path}\n"
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as inventory_file:
            inventory_file.write(inventory)
            inventory_path = inventory_file.name

        playbook_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), '.rig', 'playbooks', 'vpn-gateway.yml'
        )
        try:
            result = subprocess.run(
                [
                    'ansible-playbook',
                    '-i', inventory_path,
                    '--private-key', ssh_key_path,
                    '--ssh-common-args',
                    '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes',
                    playbook_path,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                print(f"Ansible output:\n{result.stdout}")
                print(f"Ansible error:\n{result.stderr}")
                pytest.fail(f"Ansible playbook failed: {result.stderr}")
        finally:
            os.unlink(inventory_path)
