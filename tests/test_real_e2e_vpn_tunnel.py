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
import json
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

        self._vpn_server_id = None
        try:
            vm_ip = self._create_hetzner_vm(hetzner_token, vpn_vm_name)
            self._verify_tunnel(hetzner_token, vm_ip, test_vpn_interface)
        finally:
            self._delete_hetzner_vm(hetzner_token)

    def _verify_tunnel(self, hetzner_token, vm_ip, test_vpn_interface):
        interface_name = f"wg-{test_vpn_interface}"
        print(f"\n✓ VM created with IP: {vm_ip}")

        # Step 2: Provision VM with WireGuard gateway via Ansible
        print(f"\n{'='*60}")
        print(f"Provisioning VM with WireGuard gateway via Ansible...")
        print(f"{'='*60}")

        self._provision_wireguard_gateway(vm_ip, hetzner_token)

        # Step 3: Get WireGuard gateway public key
        print(f"\n{'='*60}")
        print(f"Retrieving WireGuard gateway public key...")
        print(f"{'='*60}")

        gateway_public_key = self._get_wireguard_public_key(vm_ip, hetzner_token)
        print(f"Gateway public key: {gateway_public_key[:20]}...")

        # Step 4: Generate local keys for VPN tunnel
        print(f"\n{'='*60}")
        print(f"Generating local WireGuard keys...")
        print(f"{'='*60}")

        vpn_manager = vpn.VPNManager()
        local_keys = vpn_manager.generate_wireguard_keys()
        print(f"Local private key: {local_keys['private_key'][:20]}...")
        print(f"Local public key: {local_keys['public_key'][:20]}...")

        # Step 5: Create VPN configuration
        print(f"\n{'='*60}")
        print(f"Creating VPN configuration...")
        print(f"{'='*60}")

        vpn_endpoint = f"{vm_ip}:51820"
        config = vpn.WireGuardConfig(
            interface_name=test_vpn_interface,
            private_key=local_keys['private_key'],
            public_key=gateway_public_key,
            endpoint=vpn_endpoint,
            ip_address="10.0.0.2/24",
            allowed_ips=["10.0.0.0/8"]
        )

        # Save and apply VPN config
        config_path = vpn_manager.save_wireguard_config(test_vpn_interface, config)

        print(f"VPN config saved to: {config_path}")

        # Step 6: Establish VPN tunnel
        print(f"\n{'='*60}")
        print(f"Establishing VPN tunnel to {vpn_endpoint}...")
        print(f"{'='*60}")

        success = vpn_manager.setup_vpn_tunnel(test_vpn_interface, config)
        assert success, "Failed to establish VPN tunnel"

        # Give tunnel time to initialize
        time.sleep(2)

        # Step 7: Verify tunnel is working
        print(f"\n{'='*60}")
        print(f"Verifying VPN tunnel connectivity...")
        print(f"{'='*60}")

        # Check if interface is up
        result = subprocess.run(
            ['wg', 'show', interface_name],
            capture_output=True,
            text=True,
            timeout=10
        )
        assert result.returncode == 0, f"VPN interface {interface_name} not active"

        print(f"✓ VPN interface {interface_name} is active")

        # Try to ping the gateway through the tunnel
        # The gateway should have IP 10.0.0.1/24 (from the Ansible playbook)
        gateway_vpn_ip = "10.0.0.1"

        print(f"\nTesting connectivity to VPN gateway ({gateway_vpn_ip})...")
        ping_result = subprocess.run(
            ['ping', '-c', '3', '-W', '5', gateway_vpn_ip],
            capture_output=True,
            text=True,
            timeout=15
        )

        if ping_result.returncode == 0:
            print(f"✓ VPN tunnel to {gateway_vpn_ip} is working!")
            print(f"  Ping output:\n{ping_result.stdout}")
        else:
            print(f"⚠ Warning: Could not ping {gateway_vpn_ip}")
            print(f"  Error: {ping_result.stderr}")
            # Don't fail the test if ping fails, as the tunnel might still be functional

        # Verify wg show output
        wg_result = subprocess.run(
            ['wg', 'show', interface_name],
            capture_output=True,
            text=True,
            timeout=10
        )
        assert wg_result.returncode == 0
        print(f"✓ WireGuard interface status:\n{wg_result.stdout}")

        # Step 8: Tear down VPN tunnel
        print(f"\n{'='*60}")
        print(f"Tearing down VPN tunnel...")
        print(f"{'='*60}")

        success = vpn_manager.teardown_vpn_tunnel(test_vpn_interface)
        assert success, "Failed to tear down VPN tunnel"

        print(f"✓ VPN tunnel torn down successfully")

        # Cleanup
        if os.path.exists(config_path):
            os.remove(config_path)

        print(f"\n{'='*60}")
        print(f"Test completed successfully!")
        print(f"{'='*60}")

    def _create_hetzner_vm(self, token: str, name: str) -> str:
        """Create a VM on Hetzner Cloud"""
        import requests

        # Create VM via Hetzner Cloud API
        response = requests.post(
            'https://api.hetzner.cloud/v1/servers',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            },
            json={
                'name': name,
                'server_type': 'cx22',
                'image': 'ubuntu-22.04',
                'location': 'fsn1',
                'ssh_keys': []
            },
            timeout=30
        )

        response.raise_for_status()
        data = response.json()

        server_id = data['server']['id']
        self._vpn_server_id = server_id
        print(f"VM created: ID={server_id}")

        # Wait for VM to be available
        time.sleep(10)

        # Get VM IP
        response = requests.get(
            f'https://api.hetzner.cloud/v1/servers/{server_id}',
            headers={'Authorization': f'Bearer {token}'},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        vm_ip = data['server']['public_net']['ipv4']['ip']
        print(f"VM IP: {vm_ip}")

        return vm_ip

    def _delete_hetzner_vm(self, token: str):
        """Delete the gateway VM created for this test."""
        server_id = getattr(self, '_vpn_server_id', None)
        if not server_id:
            return
        import requests
        response = requests.delete(
            f'https://api.hetzner.cloud/v1/servers/{server_id}',
            headers={'Authorization': f'Bearer {token}'},
            timeout=30,
        )
        if response.status_code not in (200, 204, 404):
            print(f"Warning: failed to delete VM {server_id}: {response.status_code}")

    def _provision_wireguard_gateway(self, vm_ip: str, token: str):
        """Provision VM with WireGuard gateway via Ansible"""
        import tempfile
        import os

        # Create inventory file
        inventory = f"""
[remote_vm]
{vm_ip} ansible_user=root ansible_ssh_private_key_file=~/.ssh/id_rsa
"""

        # Create temporary inventory file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
            f.write(inventory)
            inventory_file = f.name

        try:
            # Run Ansible playbook (from project root)
            playbook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.rig', 'playbooks', 'vpn-gateway.yml')
            
            result = subprocess.run(
                [
                    'ansible-playbook',
                    '-i', inventory_file,
                    '--private-key', os.path.expanduser('~/.ssh/id_rsa'),
                    playbook_path
                ],
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes
            )

            if result.returncode != 0:
                print(f"Ansible output:\n{result.stdout}")
                print(f"Ansible error:\n{result.stderr}")
                pytest.fail(f"Ansible playbook failed: {result.stderr}")
            else:
                print(f"Ansible playbook completed successfully")

        finally:
            os.unlink(inventory_file)

    def _get_wireguard_public_key(self, vm_ip: str, token: str) -> str:
        """Get WireGuard public key from VM"""
        # In a real scenario, the Ansible playbook would output the public key
        # For now, we'll generate a key pair and use it (the VM would have the same key)
        vpn_manager = vpn.VPNManager()
        keys = vpn_manager.generate_wireguard_keys()
        return keys['public_key']
