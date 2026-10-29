"""
Remote Development Environment Module
"""
import os
import uuid
import time
import shutil
import subprocess
import socket
from typing import Optional, Dict, Any, List
import requests

# YAML support
try:
    import yaml  # type: ignore
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    print("Warning: PyYAML not installed, installing it...")
    import yaml  # type: ignore
    YAML_AVAILABLE = True

# WireGuard VPN support (optional)
try:
    from .vpn import VPNManager, setup_remote_vpn_connection, get_vpn_status, teardown_vpn_connection
    VPN_AVAILABLE = True
    VPNManagerType = VPNManager
except ImportError:
    VPN_AVAILABLE = False
    VPNManagerType = Any  # type: ignore


def _ipv4_from_public_net(public_net: Dict[str, Any]) -> Optional[str]:
    """Read the public IPv4 address from either Hetzner API shape."""
    ipv4 = public_net.get('ipv4') or {}
    if isinstance(ipv4, dict) and ipv4.get('ip'):
        return ipv4.get('ip')

    primary_ipv4 = public_net.get('primary_ipv4') or {}
    if isinstance(primary_ipv4, dict) and primary_ipv4.get('ip'):
        return primary_ipv4.get('ip')

    return None


class RemoteDevManager:
    """Manages remote development environments on Hetzner Cloud"""

    def __init__(self, hetzner_token: Optional[str] = None, storage_manager: Optional[Any] = None, is_test: bool = False):
        self.hetzner_token = hetzner_token or os.getenv('HETZNER_CLOUD_API_TOKEN')
        self.base_url = "https://api.hetzner.cloud/v1"

        # Storage manager for shared state
        self.storage_manager = storage_manager
        # Use test bucket if is_test is True, otherwise use production bucket
        if is_test:
            import uuid
            test_prefix = os.getenv('TEST_BUCKET_PREFIX', 'test-')
            self.state_bucket = f"{test_prefix}rig-{uuid.uuid4().hex[:8]}"
        else:
            self.state_bucket = os.getenv('STATE_BUCKET', 'rig')
        self.state_prefix = "remote-dev/environments"

        if not self.hetzner_token:
            raise ValueError("Hetzner Cloud token missing. Please set HETZNER_CLOUD_API_TOKEN")

    def _request(self, method: str, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make API request to Hetzner Cloud"""
        headers = {
            'Authorization': f'Bearer {self.hetzner_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }

        url = f"{self.base_url}{endpoint}"

        if method == 'GET':
            response = requests.get(url, headers=headers, timeout=30)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data, timeout=30)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers, timeout=30)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def create_ssh_key(self, name: str, public_key: str) -> Dict:
        """Create SSH key in Hetzner Cloud"""
        data = {
            'name': name,
            'public_key': public_key
        }

        result = self._request('POST', '/ssh_keys', data)
        return result

    def get_ssh_key_by_name(self, name: str) -> Optional[Dict]:
        """Find SSH key by name"""
        result = self._request('GET', '/ssh_keys')

        for key in result.get('ssh_keys', []):
            if key.get('name') == name:
                return key

        return None

    def delete_ssh_key(self, key_id: str) -> bool:
        """Delete SSH key by ID"""
        try:
            self._request('DELETE', f'/ssh_keys/{key_id}')
            return True
        except Exception:
            return False

    def create_firewall(self, name: str, allowed_ips: Optional[list] = None,
                        extra_rules: Optional[list] = None) -> Dict[str, Any]:
        """Create firewall with SSH rules"""
        if allowed_ips is None:
            allowed_ips = ['0.0.0.0/0', '::/0']

        rules = [
            {
                'direction': 'in',
                'protocol': 'tcp',
                'port': '22',
                'source_ips': allowed_ips,
                'description': 'Allow SSH'
            },
            {
                'direction': 'in',
                'protocol': 'icmp',
                # ICMP doesn't use ports, omit port field (API sets to null)
                'source_ips': allowed_ips,
                'description': 'Allow ICMP'
            }
        ]
        if extra_rules:
            rules.extend(extra_rules)

        data = {
            'name': name,
            'rules': rules
        }

        result = self._request('POST', '/firewalls', data)
        return result

    def create_vm(self,
                  name: str,
                  vm_type: str,
                  region: str,
                  image: str,
                  ssh_key_id: Optional[str] = None,
                  firewall_id: Optional[str] = None,
                  start_after_create: bool = True) -> Dict:
        """Create a VM for remote development"""
        data = {
            'name': name,
            'server_type': vm_type,
            'location': region,
            'image': image,
            'start_after_create': start_after_create
        }

        if ssh_key_id:
            data['ssh_keys'] = [ssh_key_id]

        if firewall_id:
            data['firewall'] = {'id': firewall_id}

        result = self._request('POST', '/servers', data)
        return result

    def get_vm(self, vm_id: str) -> Dict:
        """Get VM details"""
        result = self._request('GET', f'/servers/{vm_id}')
        return result

    def get_vm_by_name(self, name: str) -> Optional[Dict]:
        """Find VM by name"""
        result = self._request('GET', '/servers')

        for server in result.get('servers', []):
            if server.get('name') == name:
                return server

        return None

    def delete_vm(self, vm_id: str) -> bool:
        """Delete VM"""
        try:
            self._request('DELETE', f'/servers/{vm_id}')
            return True
        except Exception:
            return False

    def delete_firewall(self, firewall_id: str) -> bool:
        """Delete firewall, retrying while the VM is still detaching it."""
        for _ in range(12):
            try:
                self._request('DELETE', f'/firewalls/{firewall_id}')
                return True
            except Exception:
                time.sleep(5)
        return False

    def wait_for_vm_active(self, vm_id: str, timeout: int = 300) -> bool:
        """Wait for VM to become active or running"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            vm = self.get_vm(vm_id)
            status = vm.get('server', {}).get('status', '')

            if status in ['active', 'running']:
                return True

            if status in ['error', 'unknown']:
                return False

            time.sleep(5)

        return False

    def wait_for_ssh(self, ip: str, timeout: int = 180, interval: int = 5,
                     identity_file: Optional[str] = None, username: str = 'root') -> bool:
        """Wait until SSH accepts connections, authenticating when a key is available."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                if identity_file:
                    result = subprocess.run(
                        [
                            'ssh', '-i', identity_file,
                            '-o', 'StrictHostKeyChecking=no',
                            '-o', 'UserKnownHostsFile=/dev/null',
                            '-o', 'IdentitiesOnly=yes',
                            '-o', 'BatchMode=yes',
                            '-o', 'ConnectTimeout=10',
                            f'{username}@{ip}',
                            'true',
                        ],
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    if result.returncode == 0:
                        return True
                else:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(10)
                    result_code = sock.connect_ex((ip, 22))
                    sock.close()
                    if result_code == 0:
                        return True
            except Exception:
                pass

            time.sleep(interval)

        return False

    def get_vm_ip(self, vm_id: str) -> Optional[str]:
        """Get VM's public IP address"""
        vm = self.get_vm(vm_id)
        public_net = vm.get('server', {}).get('public_net', {})
        return _ipv4_from_public_net(public_net)

    def _ensure_state_bucket(self) -> bool:
        """Ensure the state bucket exists, create if not"""
        if not self.storage_manager:
            return False

        try:
            # List buckets to check if state bucket exists
            buckets = self.storage_manager.list_buckets()
            if self.state_bucket not in buckets:
                print(f"  State bucket '{self.state_bucket}' not found, creating it...")
                return self.storage_manager.create_bucket(self.state_bucket)
            return True
        except Exception as e:
            print(f"Warning: Could not ensure state bucket: {e}")
            return False

    def _save_state(self, environment: Dict) -> bool:
        """Save environment state to shared bucket - only basic facts"""
        if not self.storage_manager:
            return False

        try:
            # Ensure state bucket exists
            if not self._ensure_state_bucket():
                print("Warning: Could not ensure state bucket exists")
                return False

            # Get all environments first
            existing = self.list_dev_environments()

            # Check if environment already exists
            found = False
            for env in existing:
                if env.get('name') == environment.get('name'):
                    found = True
                    break

            if not found:
                # Add new environment
                existing.append(environment)

            # Convert to list format for storage (only basic facts)
            state_data = {
                'environments': existing,
                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }

            # Write temp file for upload FIRST
            with open('/tmp/state.yml', 'w') as f:
                yaml.dump(state_data, f, default_flow_style=False, sort_keys=False)

            # Upload state to bucket
            self.storage_manager.upload_file(
                self.state_bucket,
                '/tmp/state.yml',
                f"{self.state_prefix}/state.yml"
            )

            # Save detailed environment data to separate file
            env_name = environment.get('name', 'unknown')
            env_data = {
                'vm_id': environment.get('id'),
                'vm_ip': environment.get('ip'),
                'ssh_key_id': environment.get('ssh_key_id'),
                'firewall_id': environment.get('firewall_id'),
                'created_at': environment.get('created_at'),
                'type': environment.get('type'),
                'region': environment.get('region'),
                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }

            with open(f'/tmp/{env_name}.yml', 'w') as f:
                yaml.dump(env_data, f, default_flow_style=False, sort_keys=False)

            self.storage_manager.upload_file(
                self.state_bucket,
                f'/tmp/{env_name}.yml',
                f"{self.state_prefix}/environments/{env_name}.yml"
            )

            return True
        except Exception as e:
            print(f"Warning: Could not save state: {e}")
            return False

    def _load_state(self) -> Dict:
        """Load environment state from shared bucket"""
        if not self.storage_manager:
            return {'environments': []}

        try:
            # Ensure state bucket exists
            if not self._ensure_state_bucket():
                return {'environments': []}

            # Download state from bucket
            self.storage_manager.download_file(
                self.state_bucket,
                f"{self.state_prefix}/state.yml",
                '/tmp/state.yml'
            )

            with open('/tmp/state.yml', 'r') as f:
                return yaml.safe_load(f) or {'environments': []}
        except Exception as e:
            print(f"Warning: Could not load state: {e}")
            return {'environments': []}

    def list_dev_environments(self, prefix: str = "dev-") -> list:
        """List all development environments with given prefix"""
        # First try to load from shared state
        state = self._load_state()
        environments = state.get('environments', [])

        # Filter by prefix
        dev_envs = [env for env in environments if env.get('name', '').startswith(prefix)]

        # If no state found, fall back to direct API call
        if not dev_envs:
            result = self._request('GET', '/servers')

            for server in result.get('servers', []):
                name = server.get('name', '')
                if name.startswith(prefix):
                    public_net = server.get('public_net', {})
                    dev_envs.append({
                        'name': name,
                        'id': server.get('id'),
                        'status': server.get('status'),
                        'ip': _ipv4_from_public_net(public_net),
                        'type': server.get('server_type'),
                        'region': server.get('location')
                    })

        return dev_envs


class RemoteDevEnvironment:
    """Manages a complete remote development environment"""

    def __init__(self, name: Optional[str] = None, config: Optional[Dict[str, Any]] = None, storage_manager: Optional[Any] = None):
        self.name = name or f"dev-{uuid.uuid4().hex[:8]}"
        self.config = config or {}

        self.hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
        self.manager = RemoteDevManager(self.hetzner_token, storage_manager)

        # VM details
        self.vm_name = self.name
        self.vm_type = self.config.get('vm', {}).get('type', 'cax11')
        self.region = self.config.get('vm', {}).get('region', 'hel1')
        self.image = self.config.get('vm', {}).get('image', 'ubuntu-24.04')

        # SSH key
        self.ssh_key_name = f"{self.name}-key"
        self.ssh_key_id = None
        self.ssh_private_key = None
        self.ssh_public_key = None

        # Firewall
        self.firewall_name = f"{self.name}-fw"
        self.firewall_id = None

        # VPN (optional)
        self.vpn_manager: Optional[VPNManager] = VPNManager() if VPN_AVAILABLE else None
        self.vpn_interface: Optional[str] = f"{self.name}-wg" if VPN_AVAILABLE else None
        self.vpn_config: Dict[str, Any] = {} if VPN_AVAILABLE else {}  # type: ignore

        # VM
        self.vm_id = None
        self.vm_ip = None

    def generate_ssh_keys(self):
        """Generate an SSH key pair that can actually log in to the VM."""
        key_path = f'/tmp/{self.ssh_key_name}'
        try:
            for path in (key_path, f'{key_path}.pub'):
                if os.path.exists(path):
                    os.remove(path)

            result = subprocess.run(
                ['ssh-keygen', '-t', 'ed25519', '-C', f'{self.ssh_key_name}@rig',
                 '-N', '', '-f', key_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                print(f"SSH key generation failed: {result.stderr}")
                return False

            with open(key_path, 'r') as private_key_file:
                self.ssh_private_key = private_key_file.read()
            with open(f'{key_path}.pub', 'r') as public_key_file:
                self.ssh_public_key = public_key_file.read().strip()
            return True
        except Exception as e:
            print(f"SSH key generation failed: {e}")
            return False

    def setup(self):
        """Set up the complete remote development environment"""
        print(f"\n=== Setting up Remote Dev Environment: {self.name} ===")

        # 1. Generate SSH keys
        print("  1. Generating SSH keys...")
        if not self.generate_ssh_keys():
            print("     ✗ SSH key generation failed")
            return False

        # 2. Create SSH key in Hetzner
        print("  2. Registering SSH key with Hetzner...")
        try:
            key_result = self.manager.create_ssh_key(self.ssh_key_name, self.ssh_public_key)
            self.ssh_key_id = key_result.get('ssh_key', {}).get('id')
            print(f"     ✓ SSH key registered (ID: {self.ssh_key_id})")
        except Exception as e:
            print(f"     ⚠️  SSH key creation failed: {e}")

        # 3. Create firewall
        print("  3. Creating firewall...")
        try:
            firewall_config = self.config.get('firewall', {})
            firewall_ips = firewall_config.get('allowed_ips', ['0.0.0.0/0'])
            extra_rules = firewall_config.get('extra_rules')
            fw_result = self.manager.create_firewall(
                self.firewall_name, firewall_ips, extra_rules=extra_rules
            )
            self.firewall_id = fw_result.get('firewall', {}).get('id')
            print(f"     ✓ Firewall created (ID: {self.firewall_id})")
        except Exception as e:
            print(f"     ⚠️  Firewall creation failed: {e}")

        # 4. Create VM
        print(f"  4. Creating VM ({self.vm_type} in {self.region})...")
        try:
            vm_result = self.manager.create_vm(
                self.vm_name,
                self.vm_type,
                self.region,
                self.image,
                ssh_key_id=self.ssh_key_id,
                firewall_id=self.firewall_id
            )
            self.vm_id = vm_result.get('server', {}).get('id')
            print(f"     ✓ VM created (ID: {self.vm_id})")
        except Exception as e:
            print(f"     ✗ VM creation failed: {e}")
            return False

        # 5. Wait for VM to be active
        print("  5. Waiting for VM to be active...")
        if self.manager.wait_for_vm_active(self.vm_id):
            print("     ✓ VM is active")
        else:
            print("     ✗ VM failed to become active")
            return False

        # 6. Wait until SSH login works
        print("  6. Waiting for SSH to be available...")
        self.vm_ip = self.manager.get_vm_ip(self.vm_id)
        identity_file = f'/tmp/{self.ssh_key_name}'
        if self.vm_ip and self.manager.wait_for_ssh(self.vm_ip, timeout=300, identity_file=identity_file):
            print(f"     ✓ SSH is available ({self.vm_ip})")
        else:
            print("     ✗ SSH is not available")
            return False

        # 7. Save state to shared bucket
        print("  7. Saving state to shared bucket...")
        env_state = {
            'name': self.vm_name,
            'id': self.vm_id,
            'status': 'active',
            'ip': self.vm_ip,
            'type': self.vm_type,
            'region': self.region,
            'ssh_key_id': self.ssh_key_id,
            'firewall_id': self.firewall_id,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }
        if self.manager._save_state(env_state):
            print("     ✓ State saved to bucket")
        else:
            print("     ⚠️  Could not save state (continuing...)")

        print(f"\n✓ Remote dev environment is ready!")
        print(f"  VM Name: {self.vm_name}")
        print(f"  VM IP: {self.vm_ip}")
        print(f"  SSH Key ID: {self.ssh_key_id}")
        print(f"  Firewall ID: {self.firewall_id}")

        return True

    def cleanup(self):
        """Clean up the remote development environment"""
        print(f"\n=== Cleaning up Remote Dev Environment: {self.name} ===")

        # 1. Delete VM
        if self.vm_id:
            print("  1. Deleting VM...")
            if self.manager.delete_vm(self.vm_id):
                print(f"     ✓ VM deleted")
            else:
                print(f"     ⚠️  VM deletion failed or already deleted")

        # 2. Delete firewall
        if self.firewall_id:
            print("  2. Deleting firewall...")
            if self.manager.delete_firewall(self.firewall_id):
                print(f"     ✓ Firewall deleted")
            else:
                print(f"     ⚠️  Firewall deletion failed or already deleted")

        # 3. Delete SSH key
        if self.ssh_key_id:
            print("  3. Deleting SSH key...")
            if self.manager.delete_ssh_key(self.ssh_key_id):
                print(f"     ✓ SSH key deleted")
            else:
                print(f"     ⚠️  SSH key deletion failed or already deleted")

        # 4. Remove state from shared bucket
        print("  4. Removing state from shared bucket...")
        state = self.manager._load_state()
        environments = [env for env in state.get('environments', []) if env.get('name') != self.name]
        state['environments'] = environments
        state['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        with open('/tmp/state.yml', 'w') as f:
            yaml.dump(state, f, default_flow_style=False, sort_keys=False)

        if self.manager.storage_manager:
            # Ensure state bucket exists before uploading
            if self.manager._ensure_state_bucket():
                self.manager.storage_manager.upload_file(
                    self.manager.state_bucket,
                    '/tmp/state.yml',
                    f"{self.manager.state_prefix}/state.yml"
                )
                # Also remove the per-environment file
                self.manager.storage_manager.delete_object(
                    self.manager.state_bucket,
                    f"{self.manager.state_prefix}/environments/{self.name}.yml"
                )
        print("     ✓ State removed from bucket")

        # 5. Clean up local SSH keys
        print("  4. Cleaning up local SSH keys...")
        import subprocess
        subprocess.run(['rm', '-f', f'/tmp/{self.ssh_key_name}'], capture_output=True)
        subprocess.run(['rm', '-f', f'/tmp/{self.ssh_key_name}.pub'], capture_output=True)

        print(f"\n✓ Cleanup complete!")

    def get_connection_info(self) -> Dict:
        """Get information needed to connect to the VM"""
        return {
            'vm_ip': self.vm_ip,
            'ssh_key_path': f'/tmp/{self.ssh_key_name}',
            'username': 'root',  # Hetzner uses root by default for Ubuntu
            'vm_name': self.vm_name,
            'region': self.region
        }

    def setup_vpn(self, endpoint: str, peer_public_key: str, allowed_ips: Optional[List[str]] = None) -> bool:
        """
        Setup WireGuard VPN tunnel to remote gateway
        
        Args:
            endpoint: VPN gateway endpoint (host:port)
            peer_public_key: Remote gateway's public key
            allowed_ips: IPs/ranges accessible through VPN (default: 0.0.0.0/0, ::0/0)
        
        Returns:
            bool: True if VPN tunnel was established successfully
        """
        if not VPN_AVAILABLE or self.vpn_manager is None:
            print("Error: VPN support not available. Install wireguard-tools to enable.")
            return False
        
        print(f"\n=== Setting up VPN tunnel to {endpoint} ===")
        
        # Generate local keys
        keys = self.vpn_manager.generate_wireguard_keys()
        self.vpn_config = {
            'endpoint': endpoint,
            'private_key': keys['private_key'],
            'peer_public_key': peer_public_key,
            'allowed_ips': allowed_ips or ['0.0.0.0/0', '::0/0'],
            'interface_name': self.vpn_interface
        }
        
        # Setup VPN tunnel
        assert self.vpn_interface is not None
        success = self.vpn_manager.setup_vpn_with_credentials(
            name=self.vpn_interface,
            endpoint=endpoint,
            private_key=keys['private_key'],
            peer_public_key=peer_public_key,
            allowed_ips=allowed_ips
        )
        
        if success:
            print(f"✓ VPN tunnel '{self.vpn_interface}' established successfully")
            # Save VPN config for cleanup
            with open(f'/tmp/{self.vpn_interface}.json', 'w') as f:
                import json
                json.dump(self.vpn_config, f, indent=2)
        else:
            print(f"✗ Failed to setup VPN tunnel")
        
        return success

    def teardown_vpn(self) -> bool:
        """
        Tear down WireGuard VPN tunnel
        
        Returns:
            bool: True if VPN tunnel was torn down successfully
        """
        if not VPN_AVAILABLE or self.vpn_manager is None or not self.vpn_interface:
            print("Error: VPN support not available")
            return False
        
        print(f"\n=== Tearing down VPN tunnel {self.vpn_interface} ===")
        
        success = self.vpn_manager.teardown_vpn_tunnel(self.vpn_interface)
        
        if success:
            print(f"✓ VPN tunnel '{self.vpn_interface}' torn down successfully")
            # Clean up config file
            import os
            config_path = f'/tmp/{self.vpn_interface}.json'
            if os.path.exists(config_path):
                os.remove(config_path)
        else:
            print(f"✗ Failed to teardown VPN tunnel")
        
        return success

    def check_vpn_status(self) -> Dict[str, Any]:
        """
        Check status of WireGuard VPN tunnel
        
        Returns:
            Dict containing VPN status information
        """
        if not VPN_AVAILABLE or self.vpn_manager is None or not self.vpn_interface:
            return {'error': 'VPN support not available'}
        return self.vpn_manager.check_vpn_status(self.vpn_interface)

    def list_environments(self) -> list:
        """List all development environments"""
        return self.manager.list_dev_environments("dev-")

    def install_agent(self, agent_type: str = "opencode") -> bool:
        """Install a coding agent on the remote VM"""
        if not self.vm_ip or not self.ssh_key_name:
            print("Error: VM not ready for agent installation")
            return False

        print(f"\n=== Installing {agent_type} agent on remote VM ===")

        # SSH key path
        ssh_key_path = f'/tmp/{self.ssh_key_name}'

        # Agent installation script
        install_script = f"""
#!/bin/bash
set -e

# Update system
apt-get update && apt-get upgrade -y

# Install required packages
apt-get install -y curl git python3 python3-pip

# Install OpenCode CLI if requested
if [ "{agent_type}" = "opencode" ]; then
    echo "Installing OpenCode CLI..."
    curl -fsSL https://opencode.ai/install.sh | sh

    # Also install opencode server for remote attachment
    pip3 install opencode-server 2>/dev/null || pip install opencode-server
fi

# Install development tools
apt-get install -y vim htop net-tools

# Create opencode user for agent operations
id -u opencode >/dev/null 2>&1 || useradd -m -s /bin/bash opencode

# Setup SSH access for opencode user
mkdir -p /home/opencode/.ssh
chmod 700 /home/opencode/.ssh

# Allow root SSH access for agent attachment
sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd

echo "Agent installation complete!"
"""

        try:
            # Write script to temp file
            with open('/tmp/install_agent.sh', 'w') as f:
                f.write(install_script)

            # Copy script to VM
            import subprocess
            subprocess.run([
                'ssh', '-i', ssh_key_path, '-o', 'StrictHostKeyChecking=no',
                f'root@{self.vm_ip}', 'mkdir -p /tmp'
            ], check=True, capture_output=True)

            subprocess.run([
                'scp', '-i', ssh_key_path, '-o', 'StrictHostKeyChecking=no',
                '/tmp/install_agent.sh', f'root@{self.vm_ip}:/tmp/'
            ], check=True, capture_output=True)

            # Execute installation
            subprocess.run([
                'ssh', '-i', ssh_key_path, '-o', 'StrictHostKeyChecking=no',
                f'root@{self.vm_ip}', 'bash /tmp/install_agent.sh'
            ], check=True, capture_output=True)

            print(f"✓ {agent_type} agent installed successfully")

            # Update state with agent info
            state = self.manager._load_state()
            state['environments'] = [
                env for env in state.get('environments', [])
                if env.get('name') != self.name
            ]

            # Add environment with agent info
            env_state = {
                'name': self.name,
                'id': self.vm_id,
                'status': 'active',
                'ip': self.vm_ip,
                'type': self.vm_type,
                'region': self.region,
                'ssh_key_id': self.ssh_key_id,
                'firewall_id': self.firewall_id,
                'agent_installed': True,
                'agent_type': agent_type,
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }
            state['environments'].append(env_state)
            state['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

            with open('/tmp/state.yml', 'w') as f:
                yaml.dump(state, f, default_flow_style=False, sort_keys=False)

            if self.manager.storage_manager:
                self.manager.storage_manager.upload_file(
                    self.manager.state_bucket,
                    '/tmp/state.yml',
                    f"{self.manager.state_prefix}/state.yml"
                )

            return True

        except subprocess.CalledProcessError as e:
            print(f"Error installing agent: {e}")
            return False

    def attach_to_agent(self, agent_type: str = "opencode") -> bool:
        """Attach to the remote agent using opencode server"""
        if not self.vm_ip or not self.ssh_key_name:
            print("Error: VM not ready for attachment")
            return False

        print(f"\n=== Attaching to {agent_type} agent ===")

        # SSH key path
        ssh_key_path = f'/tmp/{self.ssh_key_name}'

        try:
            # Check if opencode server is running on VM
            import subprocess

            # Get agent status
            result = subprocess.run([
                'ssh', '-i', ssh_key_path, '-o', 'StrictHostKeyChecking=no',
                f'root@{self.vm_ip}', 'opencode status 2>/dev/null || echo "not installed"'
            ], capture_output=True, text=True)

            if result.returncode != 0 or "not installed" in result.stdout:
                print("Agent not installed or not running. Please install first.")
                return False

            # Display agent status
            print("\nAgent Status:")
            print(result.stdout)

            # Provide connection instructions
            print(f"\nTo attach to the agent:")
            print(f"  opencode connect --ssh {self.vm_ip}")
            print(f"  (using SSH key: {ssh_key_path})")

            return True

        except subprocess.CalledProcessError as e:
            print(f"Error attaching to agent: {e}")
            return False

    def get_agent_connection_info(self) -> Dict[str, Any]:
        """Get information needed to connect to the remote agent"""
        return {
            'vm_ip': self.vm_ip,
            'ssh_key_path': f'/tmp/{self.ssh_key_name}',
            'agent_type': 'opencode',
            'agent_status': 'installed' if self.vm_ip else 'not installed'
        }


if __name__ == '__main__':
    # Example usage
    import sys

    token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not token:
        print("Error: HETZNER_CLOUD_API_TOKEN not set")
        sys.exit(1)

    # Example configuration
    config = {
        'vm': {
            'name': 'my-dev-env',
            'type': 'cax11',
            'region': 'hel1',
            'image': 'ubuntu-24.04'
        },
        'firewall': {
            'allowed_ips': ['0.0.0.0/0']  # For testing - use specific IPs in production
        }
    }

    env = RemoteDevEnvironment(config=config)

    try:
        if env.setup():
            print("\n" + "=" * 50)
            print("Environment is ready!")
            print("=" * 50)

            # Get connection info
            conn_info = env.get_connection_info()
            print(f"\nTo connect to your VM:")
            print(f"  ssh -i {conn_info['ssh_key_path']} {conn_info['username']}@{conn_info['vm_ip']}")

            input("\nPress Enter to cleanup and exit...")
            env.cleanup()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        env.cleanup()


# =============================================================================
# PROJECT CONFIGURATION CLASSES
# =============================================================================

class ProjectConfig:
    """Manages project-based configuration in .rig folder"""

    RIG_DIR = ".rig"
    RIG_YAML = "rig.yml"
    SETUP_YAML = "setup.yml"
    VARS_YAML = "vars.yml"
    REQUIREMENTS_TXT = "requirements.txt"
    ROLES_DIR = "roles"

    def __init__(self, project_dir: Optional[str] = None):
        self.project_dir = project_dir or os.getcwd()
        self.rig_dir = os.path.join(self.project_dir, self.RIG_DIR)
        self.config: Dict[str, Any] = {}
        self.setup_config: Dict[str, Any] = {}
        self.vars_config: Dict[str, Any] = {}
        self.requirements: List[str] = []
        self.vault_password: Optional[str] = None

    def _ensure_rig_dir(self) -> bool:
        """Ensure .rig directory exists, create if not"""
        if not os.path.exists(self.rig_dir):
            os.makedirs(self.rig_dir)
            print(f"Created .rig directory at {self.rig_dir}")
            return True
        return True

    def load(self) -> bool:
        """Load all project configuration files"""
        try:
            # Load rig.yml
            rig_path = os.path.join(self.rig_dir, self.RIG_YAML)
            if os.path.exists(rig_path):
                with open(rig_path, 'r') as f:
                    self.config = yaml.safe_load(f) or {}

            # Load setup.yml
            setup_path = os.path.join(self.rig_dir, self.SETUP_YAML)
            if os.path.exists(setup_path):
                with open(setup_path, 'r') as f:
                    self.setup_config = yaml.safe_load(f) or {}

            # Load vars.yml
            vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
            if os.path.exists(vars_path):
                with open(vars_path, 'r') as f:
                    # Try to load with vault support
                    self.vars_config = self._load_vars_with_vault(vars_path)

            # Load requirements.txt
            requirements_path = os.path.join(self.rig_dir, self.REQUIREMENTS_TXT)
            if os.path.exists(requirements_path):
                with open(requirements_path, 'r') as f:
                    self.requirements = [line.strip() for line in f
                                         if line.strip() and not line.startswith('#')]

            # Load vault password if .rig/.vault_password exists
            vault_password_path = os.path.join(self.rig_dir, '.vault_password')
            if os.path.exists(vault_password_path):
                with open(vault_password_path, 'r') as f:
                    self.vault_password = f.read().strip()

            return True
        except Exception as e:
            print(f"Warning: Could not load project config: {e}")
            return False

    def _load_vars_with_vault(self, vars_path: str) -> Dict:
        """Load vars.yml with optional Ansible Vault encryption support"""
        try:
            with open(vars_path, 'r') as f:
                content = f.read()

            # Check if content is vault encrypted
            if content.startswith('$ANSIBLE_VAULT'):
                if not self.vault_password:
                    raise ValueError("Vault encrypted file requires vault password. " +
                                     "Create .rig/.vault_password with your password.")

                # Use ansible-vault to decrypt
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    result = subprocess.run(
                        ['ansible-vault', 'view', '--vault-password', self.vault_password, tmp_path],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    if result.returncode == 0:
                        return yaml.safe_load(result.stdout) or {}
                    else:
                        raise ValueError(f"Failed to decrypt vault: {result.stderr}")
                finally:
                    os.unlink(tmp_path)
            else:
                # Regular YAML file
                return yaml.safe_load(content) or {}
        except Exception as e:
            print(f"Warning: Could not load vars with vault: {e}")
            return {}

    def save(self) -> bool:
        """Save all project configuration files"""
        try:
            self._ensure_rig_dir()

            # Save rig.yml
            rig_path = os.path.join(self.rig_dir, self.RIG_YAML)
            with open(rig_path, 'w') as f:
                yaml.dump(self.config, f, default_flow_style=False, sort_keys=False)

            # Save setup.yml
            setup_path = os.path.join(self.rig_dir, self.SETUP_YAML)
            with open(setup_path, 'w') as f:
                yaml.dump(self.setup_config, f, default_flow_style=False, sort_keys=False)

            # Save vars.yml (without encryption - user must encrypt manually if needed)
            vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
            with open(vars_path, 'w') as f:
                yaml.dump(self.vars_config, f, default_flow_style=False, sort_keys=False)

            # Save requirements.txt
            requirements_path = os.path.join(self.rig_dir, self.REQUIREMENTS_TXT)
            with open(requirements_path, 'w') as f:
                for req in self.requirements:
                    f.write(f"{req}\n")

            return True
        except Exception as e:
            print(f"Warning: Could not save project config: {e}")
            return False

    def init(self, name: Optional[str] = None, ansible_enabled: bool = True) -> bool:
        """Initialize project configuration with defaults"""
        try:
            self._ensure_rig_dir()

            # Default rig.yml
            self.config = {
                'name': name or os.path.basename(self.project_dir),
                'description': 'Remote development environment',
                'ansible_enabled': ansible_enabled,
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            }

            # Default setup.yml
            self.setup_config = {
                'bootstrap': {
                    'enabled': ansible_enabled,
                    'playbook': 'playbook.yml',
                    'inventory': 'inventory.yml'
                },
                'dependencies': self.requirements
            }

            # Default vars.yml
            self.vars_config = {
                'ansible_user': 'root',
                'ansible_connection': 'ssh',
                'ansible_python_interpreter': '/usr/bin/python3'
            }

            # Default requirements.txt
            self.requirements = [
                'community.general',
                'ansible.posix',
                'geerlingguy.docker',
                'geerlingguy.python'
            ]

            return self.save()
        except Exception as e:
            print(f"Error initializing project: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with dot notation support"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key: str, value: Any) -> bool:
        """Set configuration value with dot notation support"""
        keys = key.split('.')
        config = self.config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
        return self.save()

    def get_vars(self, key: Optional[str] = None, default: Any = None) -> Any:
        """Get variables value"""
        if key:
            keys = key.split('.')
            value = self.vars_config
            for k in keys:
                if isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    return default
            return value
        return self.vars_config

    def set_vars(self, key: str, value: Any) -> bool:
        """Set variables value"""
        keys = key.split('.')
        vars_config = self.vars_config
        for k in keys[:-1]:
            if k not in vars_config:
                vars_config[k] = {}
            vars_config = vars_config[k]
        vars_config[keys[-1]] = value
        return self.save()

    def encrypt_vars(self, vars_path: Optional[str] = None) -> bool:
        """Encrypt vars.yml file with Ansible Vault"""
        try:
            vars_path = vars_path or os.path.join(self.rig_dir, self.VARS_YAML)

            if not os.path.exists(vars_path):
                print(f"Error: vars file not found: {vars_path}")
                return False

            if not self.vault_password:
                print("Error: Vault password not set")
                return False

            # Encrypt the file
            result = subprocess.run([
                'ansible-vault', 'encrypt', vars_path,
                '--vault-password', self.vault_password
            ], capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                print(f"✓ vars.yml encrypted successfully")
                return True
            else:
                print(f"Error encrypting vars.yml: {result.stderr}")
                return False
        except Exception as e:
            print(f"Error encrypting vars: {e}")
            return False


# =============================================================================
# ANSIBLE INTEGRATION CLASS
# =============================================================================

class AnsibleManager:
    """Manages Ansible playbook execution for project bootstrap"""

    def __init__(self, project_config: Optional["ProjectConfig"] = None):
        self.project_config: Optional["ProjectConfig"] = project_config
        self.playbook_path: Optional[str] = None
        self.inventory_path: Optional[str] = None
        self.vault_password: Optional[str] = None
        self.extra_vars: Dict[str, Any] = {}
        self.ansible_env = os.environ.copy()

    def set_project_config(self, config: ProjectConfig):
        """Set project configuration"""
        self.project_config = config

    def configure(self, project_dir: Optional[str] = None) -> bool:
        """Configure Ansible manager with project settings"""
        try:
            if not self.project_config:
                self.project_config = ProjectConfig(project_dir)

            if not self.project_config.load():
                print("Warning: Could not load project config, using defaults")
                self.project_config.init()

            # Set paths from config
            rig_dir = self.project_config.rig_dir
            self.playbook_path = os.path.join(rig_dir, 'playbook.yml')
            self.inventory_path = os.path.join(rig_dir, 'inventory.yml')
            self.vault_password = self.project_config.vault_password

            return True
        except Exception as e:
            print(f"Error configuring Ansible manager: {e}")
            return False

    def setup_inventory(self, vm_ip: str, ssh_key_path: str, username: str = 'root') -> bool:
        """Create Ansible inventory for the VM"""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            rig_dir = self.project_config.rig_dir
            self.inventory_path = os.path.join(rig_dir, 'inventory.yml')

            inventory = {
                'all': {
                    'hosts': {
                        'remote_vm': {
                            'ansible_host': vm_ip,
                            'ansible_user': username,
                            'ansible_ssh_private_key_file': ssh_key_path,
                            'ansible_connection': 'ssh',
                            'ansible_python_interpreter': '/usr/bin/python3'
                        }
                    }
                }
            }

            # Merge with any existing vars
            if self.project_config.vars_config:
                inventory['all']['vars'] = self.project_config.vars_config

            with open(self.inventory_path, 'w') as f:
                yaml.dump(inventory, f, default_flow_style=False, sort_keys=False)

            print(f"✓ Inventory created at {self.inventory_path}")
            return True
        except Exception as e:
            print(f"Error creating inventory: {e}")
            return False

    def setup_requirements(self) -> bool:
        """Create requirements.txt for Ansible roles"""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            requirements_path = os.path.join(self.project_config.rig_dir, 'requirements.txt')

            # If requirements already exist, keep them
            if os.path.exists(requirements_path):
                return True

            # Create default requirements
            requirements = self.project_config.requirements if self.project_config.requirements else [
                'community.general',
                'ansible.posix',
                'geerlingguy.docker',
                'geerlingguy.python'
            ]

            with open(requirements_path, 'w') as f:
                f.write('# Ansible Galaxy roles\n')
                for req in requirements:
                    f.write(f"- {req}\n")

            print(f"✓ Requirements created at {requirements_path}")
            return True
        except Exception as e:
            print(f"Error creating requirements: {e}")
            return False

    def setup_roles(self) -> bool:
        """Set up roles directory with custom roles"""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            roles_dir = os.path.join(self.project_config.rig_dir, 'roles')

            if not os.path.exists(roles_dir):
                os.makedirs(roles_dir)

            # Create role directory structure if empty
            if not os.listdir(roles_dir):
                # Create a default role structure
                default_role = os.path.join(roles_dir, 'default_role')
                os.makedirs(os.path.join(default_role, 'tasks'))
                os.makedirs(os.path.join(default_role, 'handlers'))
                os.makedirs(os.path.join(default_role, 'templates'))
                os.makedirs(os.path.join(default_role, 'files'))

                # Create tasks/main.yml
                tasks_path = os.path.join(default_role, 'tasks', 'main.yml')
                with open(tasks_path, 'w') as f:
                    f.write("""---
- name: Configure remote development environment
  hosts: remote_vm
  gather_facts: yes
  tasks:
    - name: Update apt cache
      apt:
        update_cache: yes
        cache_valid_time: 3600

    - name: Install development tools
      apt:
        name:
          - build-essential
          - git
          - curl
          - vim
        state: present
""")

            print(f"✓ Roles directory set up at {roles_dir}")
            return True
        except Exception as e:
            print(f"Error setting up roles: {e}")
            return False

    def setup_playbook(self, custom_tasks: Optional[list] = None) -> bool:
        """Create bootstrap playbook"""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            self.playbook_path = os.path.join(self.project_config.rig_dir, 'playbook.yml')

            playbook = [{
                'name': 'Bootstrap Remote Development Environment',
                'hosts': 'remote_vm',
                'gather_facts': True,
                'remote_user': 'root',
                'tasks': custom_tasks or [
                    {
                        'name': 'Update apt cache',
                        'apt': {
                            'update_cache': 'yes',
                            'cache_valid_time': '3600'
                        }
                    },
                    {
                        'name': 'Install development tools',
                        'apt': {
                            'name': ['build-essential', 'git', 'curl', 'vim', 'htop'],
                            'state': 'present'
                        }
                    },
                    {
                        'name': 'Install Python packages',
                        'pip': {
                            'name': ['pyyaml', 'requests'],
                            'state': 'present'
                        }
                    }
                ]
            }]

            with open(self.playbook_path, 'w') as f:
                yaml.dump(playbook, f, default_flow_style=False, sort_keys=False)

            print(f"✓ Playbook created at {self.playbook_path}")
            return True
        except Exception as e:
            print(f"Error creating playbook: {e}")
            return False

    def install_roles(self, requirements_path: Optional[str] = None) -> bool:
        """Install Ansible Galaxy roles from requirements.txt"""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            requirements_path = requirements_path or os.path.join(
                self.project_config.rig_dir, 'requirements.txt'
            )

            if not os.path.exists(requirements_path):
                print(f"Warning: Requirements file not found: {requirements_path}")
                return False

            # Set up roles path
            roles_path = os.path.join(self.project_config.rig_dir, 'roles')
            self.ansible_env['ANSIBLE_ROLES_PATH'] = roles_path

            # Install roles
            result = subprocess.run([
                'ansible-galaxy', 'install', '-r', requirements_path, '-p', roles_path
            ], env=self.ansible_env, capture_output=True, text=True, timeout=120)

            if result.returncode == 0:
                print("✓ Ansible roles installed successfully")
                return True
            else:
                print(f"Error installing roles: {result.stderr}")
                return False
        except Exception as e:
            print(f"Error installing roles: {e}")
            return False

    def _redact_secrets(self, text: str) -> str:
        redacted = text or ''
        for value in self.extra_vars.values():
            if isinstance(value, str) and len(value) >= 8:
                redacted = redacted.replace(value, '***')
        return redacted

    def run_playbook(self, extra_vars: Optional[Dict[str, Any]] = None, vault_password: Optional[str] = None) -> bool:
        """Run the bootstrap playbook"""
        try:
            if not self.playbook_path or not self.inventory_path:
                print("Error: Playbook or inventory not configured")
                return False

            # Set up command
            cmd = [
                'ansible-playbook', self.playbook_path,
                '-i', self.inventory_path,
                '--ssh-common-args',
                '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes'
            ]

            # Add extra variables
            if extra_vars:
                self.extra_vars.update(extra_vars)
            if self.extra_vars:
                vars_str = ' '.join([f"{k}={v}" for k, v in self.extra_vars.items()])
                cmd.extend(['-e', vars_str])

            # Add vault password if needed
            if vault_password:
                cmd.extend(['--vault-password', vault_password])
            elif self.vault_password:
                cmd.extend(['--vault-password', self.vault_password])

            # Set roles path
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            roles_path = os.path.join(self.project_config.rig_dir, 'roles')
            self.ansible_env['ANSIBLE_ROLES_PATH'] = roles_path

            print(f"Running playbook: {self.playbook_path}")
            print(f"Inventory: {self.inventory_path}")

            result = subprocess.run(cmd, env=self.ansible_env, capture_output=True, text=True, timeout=600)

            if result.returncode == 0:
                print("✓ Playbook executed successfully")
                return True
            else:
                print("Error executing playbook:")
                print(f"Return code: {result.returncode}")
                if result.stderr:
                    print(f"STDERR:\n{self._redact_secrets(result.stderr)}")
                if result.stdout:
                    print(f"STDOUT:\n{self._redact_secrets(result.stdout)}")
                return False
        except Exception as e:
            print(f"Error running playbook: {e}")
            return False

    def setup_bootstrap(self, vm_ip: str, ssh_key_path: str) -> bool:
        """Setup complete bootstrap environment"""
        try:
            print("\n=== Setting up Ansible Bootstrap ===")

            # Setup all components
            self.setup_inventory(vm_ip, ssh_key_path)
            self.setup_requirements()
            self.setup_roles()
            self.setup_playbook()

            # Install roles if requirements exist
            if self.project_config is not None:
                requirements_path = os.path.join(self.project_config.rig_dir, 'requirements.txt')
                if os.path.exists(requirements_path):
                    self.install_roles(requirements_path)

            return True
        except Exception as e:
            print(f"Error setting up bootstrap: {e}")
            return False


if __name__ == '__main__':
    print("Use the CLI interface: python3 -m cli.remote_dev_cli")
