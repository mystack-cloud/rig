"""
WireGuard VPN Management Module for Remote Development Environments
"""
import os
import subprocess
import json
import tempfile
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field


@dataclass
class WireGuardConfig:
    """WireGuard configuration for a VPN tunnel"""
    interface_name: str = "wg0"
    private_key: str = ""
    public_key: str = ""
    allowed_ips: List[str] = field(default_factory=lambda: ["0.0.0.0/0", "::0/0"])
    endpoint: str = ""
    persistent_keepalive: int = 25
    ip_address: str = ""
    subnet_mask: str = "24"


class VPNManager:
    """Manages WireGuard VPN connections for remote development environments"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.interface_name = self.config.get('interface_name', 'wg0')
        self.vpn_configs: Dict[str, WireGuardConfig] = {}
        self._config_paths: Dict[str, str] = {}
        
    def generate_wireguard_keys(self) -> Dict[str, str]:
        """Generate WireGuard key pair"""
        try:
            # Generate private key
            result = subprocess.run(
                ['wg', 'genkey'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to generate private key: {result.stderr}")
            private_key = result.stdout.strip()
            
            # Generate public key from private key
            result = subprocess.run(
                ['wg', 'pubkey'],
                input=private_key,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to generate public key: {result.stderr}")
            public_key = result.stdout.strip()
            
            return {
                'private_key': private_key,
                'public_key': public_key
            }
        except FileNotFoundError:
            raise RuntimeError("WireGuard tools not found. Please install wireguard-tools")
    
    def create_wireguard_config(self, name: str, config: Dict[str, Any]) -> WireGuardConfig:
        """Create a WireGuard configuration from dict"""
        wg_config = WireGuardConfig(
            interface_name=config.get('interface_name', self.interface_name),
            private_key=config.get('private_key', ''),
            public_key=config.get('public_key', ''),
            allowed_ips=config.get('allowed_ips', ['0.0.0.0/0', '::0/0']),
            endpoint=config.get('endpoint', ''),
            persistent_keepalive=config.get('persistent_keepalive', 25),
            ip_address=config.get('ip_address', ''),
            subnet_mask=config.get('subnet_mask', '24')
        )
        self.vpn_configs[name] = wg_config
        return wg_config
    
    def generate_wireguard_conf(self, wg_config: WireGuardConfig) -> str:
        """Generate wg.conf format content for a WireGuard interface"""
        # Handle IP address that may already include subnet mask
        ip_addr = wg_config.ip_address
        if '/' in ip_addr:
            # IP already has subnet mask, use it as-is
            address = ip_addr
        else:
            # Add subnet mask
            address = f"{ip_addr}/{wg_config.subnet_mask}"
        
        conf_lines = [
            f"[Interface]",
            f"PrivateKey = {wg_config.private_key}",
            f"Address = {address}",
            f"ListenPort = 51820",
            "",
            f"[Peer]",
            f"PublicKey = {wg_config.public_key}",
            f"AllowedIPs = {', '.join(wg_config.allowed_ips)}",
            f"Endpoint = {wg_config.endpoint}",
            f"PersistentKeepalive = {wg_config.persistent_keepalive}"
        ]
        return '\n'.join(conf_lines)

    def _config_path(self, name: str) -> str:
        """Path wg-quick will accept.

        wg-quick refuses a config whose mode and parent directory are both
        world-accessible, and reading a config from /tmp can fail outright.
        Root writes under /etc/wireguard; other users get a private directory.
        The filename is the interface name, which must stay within 15 characters.
        """
        if os.geteuid() == 0:
            directory = "/etc/wireguard"
        else:
            directory = os.path.join(tempfile.gettempdir(), f"rig-wg-{os.getuid()}")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if directory != "/etc/wireguard":
            os.chmod(directory, 0o700)
        return os.path.join(directory, f"wg-{name}.conf")

    def save_wireguard_config(self, name: str, wg_config: WireGuardConfig,
                             path: Optional[str] = None) -> str:
        """Save WireGuard configuration to a mode-0600 file"""
        if path is None:
            path = self._config_path(name)
        else:
            parent = os.path.dirname(path) or "."
            os.makedirs(parent, mode=0o700, exist_ok=True)

        conf_content = self.generate_wireguard_conf(wg_config)
        # O_CREAT honors umask, and O_TRUNC keeps a previous mode, so chmod after.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(conf_content)
        os.chmod(path, 0o600)
        self._config_paths[name] = path
        return path
    
    def setup_vpn_tunnel(self, name: str, wg_config: WireGuardConfig) -> bool:
        """Setup a WireGuard VPN tunnel using wg-quick"""
        try:
            # Generate config file
            config_path = self.save_wireguard_config(name, wg_config)
            
            # Apply configuration using wg-quick
            result = subprocess.run(
                ['wg-quick', 'up', config_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                print(f"✓ VPN tunnel '{name}' established successfully")
                return True
            else:
                print(f"✗ Failed to setup VPN tunnel '{name}' ({config_path}): {result.stderr}")
                return False
                
        except FileNotFoundError:
            print("Error: wg-quick not found. Please install wireguard-tools")
            return False
        except Exception as e:
            print(f"Error setting up VPN tunnel '{name}': {e}")
            return False
    
    def teardown_vpn_tunnel(self, name: str) -> bool:
        """Tear down a WireGuard VPN tunnel"""
        try:
            config_path = self._config_paths.get(name) or self._config_path(name)

            result = subprocess.run(
                ['wg-quick', 'down', config_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                print(f"✓ VPN tunnel '{name}' torn down successfully")
                return True
            else:
                print(f"✗ Failed to teardown VPN tunnel '{name}' ({config_path}): {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error tearing down VPN tunnel '{name}': {e}")
            return False
    
    def check_vpn_status(self, name: str) -> Dict[str, Any]:
        """Check status of a WireGuard VPN tunnel"""
        try:
            # Get interface status using wg command
            result = subprocess.run(
                ['wg', 'show', 'interfaces'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                interfaces = result.stdout.strip().split('\n')
                is_up = name in interfaces
                
                # Get detailed interface info
                detail_result = subprocess.run(
                    ['wg', 'show', name],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                return {
                    'name': name,
                    'up': is_up,
                    'status': detail_result.stdout if is_up else 'not active',
                    'error': detail_result.stderr if not is_up else None
                }
            else:
                return {
                    'name': name,
                    'up': False,
                    'status': 'unknown',
                    'error': result.stderr
                }
                
        except Exception as e:
            return {
                'name': name,
                'up': False,
                'status': 'error',
                'error': str(e)
            }
    
    def list_active_tunnels(self) -> List[str]:
        """List all active WireGuard tunnels"""
        try:
            result = subprocess.run(
                ['wg', 'show', 'interfaces'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return [iface.strip() for iface in result.stdout.strip().split('\n') if iface]
            return []
        except Exception:
            return []
    
    def setup_vpn_with_credentials(self, name: str, endpoint: str, 
                                   private_key: str, peer_public_key: str,
                                   allowed_ips: Optional[List[str]] = None) -> bool:
        """Setup VPN tunnel using provided credentials"""
        # Generate key pair for local interface
        keys = self.generate_wireguard_keys()
        
        # Create config
        wg_config = WireGuardConfig(
            interface_name=name,
            private_key=keys['private_key'],
            public_key=peer_public_key,
            allowed_ips=allowed_ips or ["0.0.0.0/0", "::0/0"],
            endpoint=endpoint,
            ip_address="10.0.0.2/24"  # Default IP for dev tunnel
        )
        
        return self.setup_vpn_tunnel(name, wg_config)


def setup_remote_vpn_connection(vpn_config: Dict[str, Any]) -> bool:
    """
    Setup VPN connection to remote gateway for accessing dependent services
    
    This function creates a WireGuard tunnel to a remote VPN gateway that
    provides access to development services that may be behind firewalls.
    
    Args:
        vpn_config: Dictionary containing VPN configuration:
            - endpoint: VPN gateway endpoint (host:port)
            - private_key: Local WireGuard private key
            - peer_public_key: Remote gateway's public key
            - allowed_ips: IPs/ranges accessible through VPN
            - interface_name: Name of the WireGuard interface
    
    Returns:
        bool: True if VPN tunnel was established successfully
    """
    manager = VPNManager()
    
    try:
        # Generate local keys if not provided
        if not vpn_config.get('private_key') or not vpn_config.get('peer_public_key'):
            keys = manager.generate_wireguard_keys()
            vpn_config['private_key'] = keys['private_key']
        
        # Create and setup VPN config
        wg_config = manager.create_wireguard_config(
            vpn_config.get('interface_name', 'wg0'),
            vpn_config
        )
        
        return manager.setup_vpn_tunnel(vpn_config.get('interface_name', 'wg0'), wg_config)
        
    except Exception as e:
        print(f"Error setting up VPN connection: {e}")
        return False


def get_vpn_status(interface_name: str = "wg0") -> Dict[str, Any]:
    """Get status of VPN connection"""
    manager = VPNManager()
    return manager.check_vpn_status(interface_name)


def teardown_vpn_connection(interface_name: str = "wg0") -> bool:
    """Tear down VPN connection"""
    manager = VPNManager()
    return manager.teardown_vpn_tunnel(interface_name)
