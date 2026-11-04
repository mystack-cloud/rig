"""
Remote Development Environment Module
"""
import json
import os
import re
import uuid
import time
import shutil
import shlex
import subprocess
import socket
import webbrowser
from typing import Optional, Dict, Any, List
import requests

from cli import log as log

# YAML support
try:
    import yaml  # type: ignore
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
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


# Project files live here on the VM — separate from /root home (dotfiles, caches).
REMOTE_WORKSPACE_ROOT = "/workspace"


def build_rig_ansible_vars(
    env: "RemoteDevEnvironment",
    project: Optional["ProjectConfig"] = None,
    *,
    action: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Runtime Ansible extras injected into every playbook (``rig_*`` prefix).

    Passed via ``ansible-playbook -e`` after ``.rig/vars.yml`` so playbooks can
    reference environment identity without hard-coding it.
    """
    cfg = (project.config if project else None) or {}
    vm_cfg = cfg.get("vm") or {}
    agent_cfg = cfg.get("agent") or {}
    repos = [
        {
            "name": repo.get("name"),
            "url": repo.get("url"),
            "path": repo.get("path"),
        }
        for repo in (cfg.get("repos") or [])
        if isinstance(repo, dict) and repo.get("name")
    ]
    providers = cfg.get("git_providers")
    if providers is None and project is not None:
        providers = project.get_vars("git_providers")
    try:
        from cli.snapshots import package_version
        version = package_version()
    except Exception:
        version = "0.0.0"

    workspace_name = cfg.get("name") or (env.name if env else "")
    ssh_key_name = (env.ssh_key_name if env else None) or ""
    ssh_key_path = f"/tmp/{ssh_key_name}" if ssh_key_name else ""

    vars_out: Dict[str, Any] = {
        "rig_env_name": env.name if env else "",
        "rig_workspace_name": workspace_name,
        "rig_vm_name": (env.vm_name if env else None) or (env.name if env else ""),
        "rig_vm_type": (env.vm_type if env else None) or vm_cfg.get("type") or "cax11",
        "rig_availability_zone": (
            (env.region if env else None)
            or vm_cfg.get("availability_zone")
            or vm_cfg.get("region")
            or "hel1"
        ),
        "rig_vm_image": str(
            (env.image if env else None) or vm_cfg.get("image") or "ubuntu-24.04"
        ),
        "rig_vm_ip": (env.vm_ip if env else None) or "",
        "rig_vm_id": str(env.vm_id) if env and env.vm_id is not None else "",
        "rig_agent_type": (
            agent_cfg.get("type")
            or (project.get_vars("coding_agent_type") if project else None)
            or "opencode"
        ),
        "rig_workspace_root": REMOTE_WORKSPACE_ROOT,
        "rig_version": version,
        "rig_boot_source": (env.boot_source if env else None) or "base",
        "rig_project_dir": (
            project.project_dir if project else os.getcwd()
        ),
        "rig_ssh_key_name": ssh_key_name,
        "rig_ssh_key_path": ssh_key_path,
        "rig_firewall_name": (env.firewall_name if env else None) or "",
        "rig_repos": repos,
        "rig_git_providers": list(providers or []),
    }
    if action:
        vars_out["rig_action"] = action
    return vars_out


def remote_workspace_path(*parts: str) -> str:
    """Build an absolute path under the remote workspace root."""
    cleaned = [part.strip("/") for part in parts if part and str(part).strip("/")]
    if not cleaned:
        return REMOTE_WORKSPACE_ROOT
    return REMOTE_WORKSPACE_ROOT + "/" + "/".join(cleaned)


def _ipv4_from_public_net(public_net: Dict[str, Any]) -> Optional[str]:
    """Read the public IPv4 address from either Hetzner API shape."""
    ipv4 = public_net.get('ipv4') or {}
    if isinstance(ipv4, dict) and ipv4.get('ip'):
        return ipv4.get('ip')

    primary_ipv4 = public_net.get('primary_ipv4') or {}
    if isinstance(primary_ipv4, dict) and primary_ipv4.get('ip'):
        return primary_ipv4.get('ip')

    return None


_PUBLIC_IP_URLS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)


FIREWALL_MODES = ("open", "static", "dynamic_replace", "dynamic_add")
DEFAULT_FIREWALL_MODE = "dynamic_add"
_OPEN_SOURCE_IPS = ["0.0.0.0/0", "::/0"]


def detect_client_public_ip(timeout: float = 5.0) -> Optional[str]:
    """
    Resolve the public IPv4 of the machine running the rig CLI.

    Used by ``firewall.mode: dynamic_add`` / ``dynamic_replace``.
    """
    for url in _PUBLIC_IP_URLS:
        try:
            response = requests.get(url, timeout=timeout)
            if not response.ok:
                continue
            candidate = (response.text or "").strip()
            if not candidate or "/" in candidate or " " in candidate:
                continue
            # Basic IPv4 shape check (avoid HTML error pages).
            parts = candidate.split(".")
            if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                return candidate
        except Exception:
            continue
    return None


def as_cidr(ip_or_cidr: str) -> str:
    """Normalize a bare IP to ``/32``; leave existing CIDRs unchanged."""
    value = (ip_or_cidr or "").strip()
    if not value:
        return value
    return value if "/" in value else f"{value}/32"


def _dedupe_cidrs(items: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def resolve_firewall_mode(firewall_cfg: Optional[Dict[str, Any]] = None) -> str:
    """
    Resolve ``firewall.mode``.

    Modes: ``open`` | ``static`` | ``dynamic_replace`` | ``dynamic_add`` (default).

    Legacy ``lock_to_client_ip`` maps to ``dynamic_add`` (true) or ``open`` (false)
    when ``mode`` is unset.
    """
    cfg = firewall_cfg or {}
    raw = cfg.get("mode")
    if raw is not None:
        mode = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "none": "open",
            "off": "open",
            "unrestricted": "open",
            "replace": "dynamic_replace",
            "dynamic": "dynamic_add",
            "add": "dynamic_add",
        }
        mode = aliases.get(mode, mode)
        if mode not in FIREWALL_MODES:
            log.warning(
                "Unknown firewall.mode %r; using %s",
                raw,
                DEFAULT_FIREWALL_MODE,
            )
            return DEFAULT_FIREWALL_MODE
        return mode
    if "lock_to_client_ip" in cfg:
        return "dynamic_add" if cfg.get("lock_to_client_ip") else "open"
    return DEFAULT_FIREWALL_MODE


def firewall_needs_client_ip(mode: str) -> bool:
    return mode in ("dynamic_add", "dynamic_replace")


def firewall_source_ips(
    firewall_cfg: Optional[Dict[str, Any]] = None,
    *,
    client_ip: Optional[str] = None,
    existing_ips: Optional[List[str]] = None,
) -> List[str]:
    """
    Build inbound ``source_ips`` for the env firewall.

    - ``open``: world access
    - ``static``: ``allowed_ips`` only (open fallback if empty)
    - ``dynamic_replace``: current client IP + ``allowed_ips`` (replaces previous)
    - ``dynamic_add`` (default): keep ``existing_ips``, add client IP + ``allowed_ips``
    """
    cfg = firewall_cfg or {}
    mode = resolve_firewall_mode(cfg)
    extras = [
        as_cidr(str(item))
        for item in (cfg.get("allowed_ips") or [])
        if item is not None and str(item).strip()
    ]
    client_cidr = as_cidr(client_ip) if client_ip else None

    if mode == "open":
        return list(_OPEN_SOURCE_IPS)

    if mode == "static":
        return _dedupe_cidrs(extras) or list(_OPEN_SOURCE_IPS)

    if mode == "dynamic_replace":
        sources: List[str] = []
        if client_cidr:
            sources.append(client_cidr)
        sources.extend(extras)
        return _dedupe_cidrs(sources) or list(_OPEN_SOURCE_IPS)

    # dynamic_add — preserve existing allow-list entries
    sources = [
        as_cidr(str(item))
        for item in (existing_ips or [])
        if item is not None and str(item).strip()
    ]
    # Drop world-open entries when we start locking to concrete IPs.
    if client_cidr or extras:
        sources = [s for s in sources if s not in ("0.0.0.0/0", "::/0")]
    if client_cidr:
        sources.append(client_cidr)
    sources.extend(extras)
    return _dedupe_cidrs(sources) or list(_OPEN_SOURCE_IPS)


def normalize_firewall_ports(
    ports: Optional[List[Any]] = None,
) -> List[Dict[str, str]]:
    """
    Normalize ``firewall.ports`` entries to ``{protocol, port, description}``.

    Accepted forms:
      - ``8080`` / ``"8080"`` / ``"8000-8010"`` → TCP
      - ``{port: 8080}`` / ``{port: 53, protocol: udp, description: "..."}``
    """
    normalized: List[Dict[str, str]] = []
    seen = set()
    for item in ports or []:
        protocol = "tcp"
        port: Optional[str] = None
        description = ""
        if isinstance(item, dict):
            raw_port = item.get("port")
            if raw_port is None:
                continue
            port = str(raw_port).strip()
            protocol = str(item.get("protocol") or "tcp").strip().lower() or "tcp"
            description = str(item.get("description") or "").strip()
        else:
            port = str(item).strip()
        if not port:
            continue
        if protocol not in ("tcp", "udp"):
            log.warning("Skipping firewall port with unsupported protocol %r", protocol)
            continue
        key = (protocol, port)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "protocol": protocol,
                "port": port,
                "description": description or f"Allow {protocol.upper()} {port}",
            }
        )
    return normalized


def default_firewall_rules(
    source_ips: List[str],
    extra_rules: Optional[List[Dict[str, Any]]] = None,
    ports: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """SSH + ICMP + optional app ports used for create and set_rules refresh."""
    rules: List[Dict[str, Any]] = [
        {
            "direction": "in",
            "protocol": "tcp",
            "port": "22",
            "source_ips": list(source_ips),
            "description": "Allow SSH",
        },
        {
            "direction": "in",
            "protocol": "icmp",
            "source_ips": list(source_ips),
            "description": "Allow ICMP",
        },
    ]
    for entry in normalize_firewall_ports(ports):
        # Never duplicate the built-in SSH rule.
        if entry["protocol"] == "tcp" and entry["port"] == "22":
            continue
        rules.append(
            {
                "direction": "in",
                "protocol": entry["protocol"],
                "port": entry["port"],
                "source_ips": list(source_ips),
                "description": entry["description"],
            }
        )
    if extra_rules:
        rules.extend(extra_rules)
    return rules


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

    def create_firewall(
        self,
        name: str,
        allowed_ips: Optional[list] = None,
        extra_rules: Optional[list] = None,
        ports: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Create firewall with SSH + ICMP (+ optional app ports) for ``allowed_ips``."""
        if allowed_ips is None:
            allowed_ips = ['0.0.0.0/0', '::/0']
        data = {
            'name': name,
            'rules': default_firewall_rules(
                list(allowed_ips),
                extra_rules=extra_rules,
                ports=ports,
            ),
        }
        result = self._request('POST', '/firewalls', data)
        return result

    def get_firewall(self, firewall_id: Any) -> Dict[str, Any]:
        """Fetch a firewall by id."""
        return self._request('GET', f'/firewalls/{firewall_id}')

    def set_firewall_rules(
        self,
        firewall_id: Any,
        allowed_ips: List[str],
        extra_rules: Optional[List[Dict[str, Any]]] = None,
        ports: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Replace firewall rules (Hetzner ``set_rules`` action)."""
        data = {
            'rules': default_firewall_rules(
                list(allowed_ips),
                extra_rules=extra_rules,
                ports=ports,
            ),
        }
        return self._request(
            'POST',
            f'/firewalls/{firewall_id}/actions/set_rules',
            data,
        )

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

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """List snapshot images in the Hetzner project."""
        result = self._request("GET", "/images?type=snapshot&status=available")
        images = list(result.get("images") or [])
        # Also include creating snapshots so we can detect in-progress dupes.
        try:
            pending = self._request("GET", "/images?type=snapshot")
            seen = {img.get("id") for img in images}
            for img in pending.get("images") or []:
                if img.get("id") not in seen:
                    images.append(img)
        except Exception:
            pass
        return images

    def find_snapshot_by_description(self, description: str) -> Optional[Dict[str, Any]]:
        """Return the first snapshot whose description matches exactly."""
        for image in self.list_snapshots():
            if image.get("description") == description:
                return image
        return None

    def wait_for_action(self, action_id: str, timeout: int = 600, interval: int = 3) -> bool:
        """Poll a Hetzner action until success/error/timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self._request("GET", f"/actions/{action_id}")
                action = result.get("action") or {}
                status = action.get("status")
                if status == "success":
                    return True
                if status == "error":
                    err = action.get("error") or {}
                    log.error(
                        "Hetzner action %s failed: %s",
                        action_id,
                        err.get("message") or status,
                    )
                    return False
            except Exception as exc:
                log.debug("Waiting for action %s: %s", action_id, exc)
            time.sleep(interval)
        log.error("Timed out waiting for Hetzner action %s", action_id)
        return False

    def create_snapshot(
        self,
        server_id: str,
        description: str,
        labels: Optional[Dict[str, str]] = None,
        wait: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a snapshot image from a server.

        Skips creation when a snapshot with the same description already exists.
        """
        existing = self.find_snapshot_by_description(description)
        if existing:
            log.info(
                "Snapshot already exists: %s (id=%s)",
                description,
                existing.get("id"),
            )
            return existing

        data: Dict[str, Any] = {
            "description": description,
            "type": "snapshot",
        }
        if labels:
            data["labels"] = labels

        log.info("Creating snapshot %s", description)
        result = self._request(
            "POST",
            f"/servers/{server_id}/actions/create_image",
            data,
        )
        image = result.get("image") or {}
        action = result.get("action") or {}
        action_id = action.get("id")
        if wait and action_id:
            if not self.wait_for_action(str(action_id)):
                return None
        log.info(
            "Snapshot ready: %s (id=%s)",
            description,
            image.get("id"),
        )
        return image

    def delete_image(self, image_id: str) -> bool:
        """Delete a Hetzner image/snapshot by id."""
        try:
            self._request("DELETE", f"/images/{image_id}")
            return True
        except Exception as exc:
            log.warning("Failed to delete image %s: %s", image_id, exc)
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
                log.debug("State bucket %r not found; creating", self.state_bucket)
                return self.storage_manager.create_bucket(self.state_bucket)
            return True
        except Exception as e:
            log.warning("Could not ensure state bucket: %s", e)
            return False

    def _save_state(self, environment: Dict) -> bool:
        """Save environment state to shared bucket - only basic facts"""
        if not self.storage_manager:
            return False

        try:
            # Ensure state bucket exists
            if not self._ensure_state_bucket():
                log.warning("Could not ensure state bucket exists")
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

            log.info("State synced")
            return True
        except Exception as e:
            log.warning("Could not save state: %s", e)
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
                data = yaml.safe_load(f) or {'environments': []}
            log.debug("State loaded")
            return data
        except Exception as e:
            log.warning("Could not load state: %s", e)
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

        # VM details (availability_zone is the Hetzner location, e.g. hel1)
        vm = self.config.get("vm") or {}
        self.vm_name = self.name
        self.vm_type = vm.get("type") or "cax11"
        self.region = (
            vm.get("availability_zone")
            or vm.get("region")
            or "hel1"
        )
        self.image = vm.get("image") or "ubuntu-24.04"

        # SSH key
        self.ssh_key_name = f"{self.name}-key"
        self.ssh_key_id: Optional[Any] = None
        self.ssh_private_key: Optional[str] = None
        self.ssh_public_key: Optional[str] = None

        # Firewall
        self.firewall_name = f"{self.name}-fw"
        self.firewall_id: Optional[Any] = None

        # VPN (optional)
        self.vpn_manager: Optional[VPNManager] = VPNManager() if VPN_AVAILABLE else None
        self.vpn_interface: Optional[str] = f"{self.name}-wg" if VPN_AVAILABLE else None
        self.vpn_config: Dict[str, Any] = {} if VPN_AVAILABLE else {}  # type: ignore

        # VM
        self.vm_id: Optional[Any] = None
        self.vm_ip: Optional[str] = None

        # Boot / playbook skip state (set during setup from matching snapshots)
        self.boot_source: str = "base"  # base | system | workspace
        self.boot_snapshot_description: Optional[str] = None
        self.skip_playbooks: tuple = ()

    def resolve_boot_image(
        self,
        project_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Choose VM image: matching workspace snapshot, else system snapshot, else base.

        Updates ``self.image`` when a snapshot is selected and records which
        playbooks can be skipped after boot.
        """
        project_dir = os.path.abspath(project_dir or os.getcwd())
        base_image = self.config.get("vm", {}).get("image") or self.image or "ubuntu-24.04"
        self.image = base_image
        self.boot_source = "base"
        self.boot_snapshot_description = None
        self.skip_playbooks = ()

        try:
            from cli.snapshots import (
                package_version,
                resolve_boot_snapshot,
                workspace_content_hash,
            )
        except ImportError:
            return {
                "source": "base",
                "image_ref": base_image,
                "skip_playbooks": (),
            }

        project = ProjectConfig(project_dir)
        project.load()
        workspace_name = (
            (project.config or {}).get("name")
            or os.path.basename(project_dir.rstrip(os.sep))
            or self.name
        )
        digest = workspace_content_hash(project_dir)
        version = package_version()

        try:
            images = self.manager.list_snapshots()
        except Exception as exc:
            log.debug("Could not list snapshots for boot reuse: %s", exc)
            images = []

        decision = resolve_boot_snapshot(
            images,
            workspace_name=str(workspace_name),
            content_hash=digest,
            version=version,
        )
        snap = decision.get("image")
        if snap and snap.get("id") is not None:
            self.image = snap.get("id")
            self.boot_source = str(decision.get("source") or "base")
            self.boot_snapshot_description = decision.get("description")
            self.skip_playbooks = tuple(decision.get("skip_playbooks") or ())
            log.info(
                "Reusing %s snapshot %s (id=%s); will skip: %s",
                self.boot_source,
                self.boot_snapshot_description,
                snap.get("id"),
                ", ".join(self.skip_playbooks) or "(none)",
            )
        else:
            log.debug("No matching snapshot; booting from base image %s", base_image)

        return {
            "source": self.boot_source,
            "image_ref": self.image,
            "skip_playbooks": self.skip_playbooks,
            "description": self.boot_snapshot_description,
        }

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
                log.error("SSH key generation failed: %s", result.stderr)
                return False

            with open(key_path, 'r') as private_key_file:
                self.ssh_private_key = private_key_file.read()
            with open(f'{key_path}.pub', 'r') as public_key_file:
                self.ssh_public_key = public_key_file.read().strip()
            return True
        except Exception as e:
            log.error("SSH key generation failed: %s", e)
            return False

    def setup(self, project_dir: Optional[str] = None):
        """Set up the complete remote development environment"""
        log.info("Creating remote environment '%s'", self.name)

        # Prefer matching Hetzner snapshots for faster boot / fewer playbooks.
        self.resolve_boot_image(project_dir=project_dir)

        log.debug("Generating SSH keys")
        if not self.generate_ssh_keys():
            log.error("SSH key generation failed")
            return False

        log.debug("Registering SSH key with Hetzner")
        if not self.ssh_public_key:
            log.error("SSH public key missing after generation")
            return False
        try:
            key_result = self.manager.create_ssh_key(
                self.ssh_key_name, self.ssh_public_key
            )
            self.ssh_key_id = key_result.get('ssh_key', {}).get('id')
            log.debug("SSH key registered (id=%s)", self.ssh_key_id)
        except Exception as e:
            log.warning("SSH key registration failed: %s", e)

        log.debug("Creating firewall")
        try:
            firewall_config = self._firewall_config(project_dir)
            mode = resolve_firewall_mode(firewall_config)
            client_ip = None
            if firewall_needs_client_ip(mode):
                client_ip = detect_client_public_ip()
                if not client_ip:
                    log.error(
                        "firewall.mode=%s requires the CLI host public IP, "
                        "but it could not be detected",
                        mode,
                    )
                    return False
            firewall_ips = firewall_source_ips(
                firewall_config,
                client_ip=client_ip,
                existing_ips=None,
            )
            ports = firewall_config.get("ports") or []
            extra_rules = firewall_config.get("extra_rules")
            port_label = ", ".join(
                f"{p['protocol']}/{p['port']}" for p in normalize_firewall_ports(ports)
            )
            log.info(
                "Firewall mode=%s source IPs: %s%s%s",
                mode,
                ", ".join(firewall_ips),
                f" (client {client_ip})" if client_ip else "",
                f"; ports: {port_label}" if port_label else "",
            )
            fw_result = self.manager.create_firewall(
                self.firewall_name,
                firewall_ips,
                extra_rules=extra_rules,
                ports=ports,
            )
            self.firewall_id = fw_result.get('firewall', {}).get('id')
            log.debug("Firewall created (id=%s)", self.firewall_id)
        except Exception as e:
            log.warning("Firewall creation failed: %s", e)

        log.info(
            "Creating VM %s in %s from %s",
            self.vm_type,
            self.region,
            self.boot_snapshot_description or self.image,
        )
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
            log.debug("VM created (id=%s)", self.vm_id)
        except Exception as e:
            log.error("VM creation failed: %s", e)
            return False

        if self.vm_id is None:
            log.error("VM creation returned no id")
            return False

        log.debug("Waiting for VM to become active")
        if self.manager.wait_for_vm_active(str(self.vm_id)):
            log.debug("VM is active")
        else:
            log.error("VM failed to become active")
            return False

        log.debug("Waiting for SSH")
        self.vm_ip = self.manager.get_vm_ip(str(self.vm_id))
        identity_file = f'/tmp/{self.ssh_key_name}'
        if self.vm_ip and self.manager.wait_for_ssh(self.vm_ip, timeout=300, identity_file=identity_file):
            log.debug("SSH available at %s", self.vm_ip)
        else:
            log.error("SSH is not available on the new VM")
            return False

        log.debug("Saving environment state")
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
            log.debug("State saved to bucket")
        else:
            log.warning("Could not save state to bucket; continuing")

        log.info("Remote VM ready: %s (%s)", self.vm_name, self.vm_ip)
        log.debug(
            "ids ssh_key=%s firewall=%s vm=%s",
            self.ssh_key_id,
            self.firewall_id,
            self.vm_id,
        )
        return True

    def provision(
        self,
        project_dir: Optional[str] = None,
        stream: bool = True,
        force_system: bool = False,
        force_workspace: bool = False,
    ) -> bool:
        """
        Run Ansible system.yml then workspace.yml against the remote VM.

        Playbooks already covered by the boot snapshot are skipped unless
        ``force_system`` / ``force_workspace`` is set.
        """
        return self.apply_playbooks(
            project_dir=project_dir,
            stream=stream,
            playbooks=("system", "workspace"),
            soft_fail_workspace=True,
            force_system=force_system,
            force_workspace=force_workspace,
            action="create",
        )

    def apply_playbooks(
        self,
        project_dir: Optional[str] = None,
        stream: bool = True,
        playbooks: tuple = ("workspace",),
        soft_fail_workspace: bool = False,
        force_system: bool = False,
        force_workspace: bool = False,
        action: Optional[str] = None,
    ) -> bool:
        """
        Re-run selected Ansible playbooks against an existing remote VM.

        ``playbooks`` entries are ``\"system\"`` and/or ``\"workspace\"``
        (order preserved). Used by ``rig create`` (both) and ``rig apply``
        (workspace by default, ``--system`` for system.yml).

        When the VM was booted from a matching snapshot, covered playbooks are
        skipped unless forced.
        """
        if not self.vm_ip or not self.ssh_key_name:
            log.error("VM is not ready for provisioning (missing IP or SSH key)")
            return False

        wanted = tuple(p for p in playbooks if p in ("system", "workspace"))
        if not wanted:
            log.error("No playbooks selected (use workspace and/or system)")
            return False

        skip = set(self.skip_playbooks or ())
        if force_system:
            skip.discard("system")
        if force_workspace:
            skip.discard("workspace")
        if skip:
            skipped = [p for p in wanted if p in skip]
            if skipped:
                log.info(
                    "Skipping playbook(s) covered by %s snapshot%s: %s",
                    self.boot_source,
                    f" ({self.boot_snapshot_description})"
                    if self.boot_snapshot_description
                    else "",
                    ", ".join(skipped),
                )
            wanted = tuple(p for p in wanted if p not in skip)
            if not wanted:
                log.info("No playbooks to run after snapshot reuse")
                return True

        project_dir = os.path.abspath(project_dir or os.getcwd())
        project = ProjectConfig(project_dir)
        if not project.load() or not project.config:
            log.error(
                "No rig.yml in %s; run 'rig init' first so playbooks exist",
                project_dir,
            )
            return False

        try:
            from cli.encrypt import ensure_vscode_browser_password
            if ensure_vscode_browser_password(project_dir=project_dir):
                log.info(
                    "Generated vscode_browser_password for code-server browser login"
                )
        except Exception as exc:
            log.warning("Could not ensure vscode_browser_password: %s", exc)

        ssh_key_path = f"/tmp/{self.ssh_key_name}"
        if not os.path.exists(ssh_key_path):
            log.error("SSH private key not found at %s", ssh_key_path)
            return False

        agent_type = (
            (project.config.get("agent") or {}).get("type")
            or project.get_vars("coding_agent_type")
            or "opencode"
        )
        log.info(
            "Applying %s on %s as root@%s (agent=%s)",
            "+".join(wanted),
            project_dir,
            self.vm_ip,
            agent_type,
        )

        # Ensure legacy /root/<repo> clone destinations are under /workspace.
        try:
            from cli.git import GitManager
            GitManager(project).normalize_workspace_dests()
        except Exception as exc:
            log.debug("Could not normalize clone destinations: %s", exc)

        ansible = AnsibleManager(project)
        if not ansible.configure(project_dir):
            return False
        if not ansible.setup_bootstrap(self.vm_ip, ssh_key_path):
            return False
        ansible.rig_vars = build_rig_ansible_vars(
            self,
            project,
            action=action or "apply",
        )

        system_path = ansible.playbook_path
        workspace_path = ansible.workspace_playbook_path
        path_by_kind = {
            "system": system_path,
            "workspace": workspace_path,
        }

        workspace_failed = False
        for kind in wanted:
            path = path_by_kind.get(kind)
            if not path or not os.path.exists(path):
                log.error("%s playbook not found: %s", kind.capitalize(), path)
                return False
            log.info("Running %s playbook", kind)
            if not ansible._run_one_playbook(path, stream=stream):
                if kind == "workspace" and soft_fail_workspace:
                    log.warning(
                        "Workspace playbook failed (often an invalid git PAT); "
                        "continuing"
                    )
                    workspace_failed = True
                    continue
                log.error("%s playbook failed", kind.capitalize())
                return False
            # Snapshot after each successful playbook (create / apply).
            if not self._snapshot_after_playbook(kind, project):
                log.warning("Continuing without a successful %s snapshot", kind)

        if workspace_failed:
            log.info("Playbooks finished (workspace had errors)")
        else:
            log.info("Playbooks complete")
        return True

    def _snapshot_after_playbook(
        self,
        kind: str,
        project: "ProjectConfig",
    ) -> bool:
        """Create the system or workspace Hetzner snapshot for a finished playbook."""
        if not self.vm_id:
            self.resolve_resources()
        if not self.vm_id:
            log.warning("Cannot create snapshot: VM id unknown for '%s'", self.name)
            return False

        try:
            from cli.snapshots import (
                package_version,
                system_snapshot_labels,
                system_snapshot_name,
                workspace_content_hash,
                workspace_snapshot_labels,
                workspace_snapshot_name,
            )
        except ImportError as exc:
            log.warning("Snapshot helpers unavailable: %s", exc)
            return False

        if kind == "system":
            version = package_version()
            description = system_snapshot_name(version)
            labels = system_snapshot_labels(version)
        else:
            workspace_name = (
                (project.config or {}).get("name")
                or os.path.basename(project.project_dir.rstrip(os.sep))
                or self.name
            )
            digest = workspace_content_hash(project.project_dir)
            description = workspace_snapshot_name(str(workspace_name), digest)
            labels = workspace_snapshot_labels(str(workspace_name), digest)

        try:
            image = self.manager.create_snapshot(
                str(self.vm_id),
                description,
                labels=labels,
                wait=True,
            )
            return bool(image)
        except Exception as exc:
            log.warning("Snapshot %s failed: %s", description, exc)
            return False

    def _firewall_config(self, project_dir: Optional[str] = None) -> Dict[str, Any]:
        """Load ``firewall`` settings from ``rig.yml`` (with env.config fallback)."""
        project_dir = os.path.abspath(project_dir or os.getcwd())
        merged: Dict[str, Any] = {}
        try:
            project = ProjectConfig(project_dir)
            if project.load() and project.config:
                file_fw = project.config.get("firewall")
                if isinstance(file_fw, dict):
                    merged.update(file_fw)
        except Exception as exc:
            log.debug("Could not load firewall config from rig.yml: %s", exc)
        runtime_fw = self.config.get("firewall")
        if isinstance(runtime_fw, dict):
            merged.update(runtime_fw)
        # Defaults when keys are absent.
        merged.setdefault("mode", DEFAULT_FIREWALL_MODE)
        merged.setdefault("allowed_ips", [])
        merged.setdefault("ports", [])
        return merged

    def ensure_firewall_access(self, project_dir: Optional[str] = None) -> bool:
        """
        Refresh firewall source IPs (and exposed ports) for ``firewall.mode``.

        Called before attach / apply / dev / destroy. ``dynamic_add`` keeps
        existing IPs and only adds the current client IP; ``dynamic_replace``
        rewrites the allow-list. Soft-fails with a warning when the firewall id
        is missing or the API update fails.
        """
        project_dir = os.path.abspath(project_dir or os.getcwd())
        if not self.firewall_id:
            self.resolve_resources()
        if not self.firewall_id:
            log.debug("No firewall id; skipping access refresh")
            return False

        firewall_config = self._firewall_config(project_dir)
        mode = resolve_firewall_mode(firewall_config)
        ports = firewall_config.get("ports") or []
        extra_rules = firewall_config.get("extra_rules")

        # open/static still refresh when declared app ports change.
        client_ip: Optional[str] = None
        if firewall_needs_client_ip(mode):
            client_ip = detect_client_public_ip()
            if not client_ip:
                log.warning(
                    "Could not detect CLI public IP; leaving firewall rules unchanged"
                )
                return False

        try:
            current = self.manager.get_firewall(self.firewall_id)
            rules = (current.get("firewall") or {}).get("rules") or []
            ssh_sources: List[str] = []
            current_ports: set = set()
            for rule in rules:
                if rule.get("direction") != "in":
                    continue
                proto = str(rule.get("protocol") or "")
                port = str(rule.get("port") or "")
                if proto == "tcp" and port == "22":
                    ssh_sources = list(rule.get("source_ips") or [])
                elif proto in ("tcp", "udp") and port and port != "22":
                    current_ports.add((proto, port))

            if mode == "open":
                desired = list(_OPEN_SOURCE_IPS)
            elif mode == "static":
                desired = firewall_source_ips(firewall_config, client_ip=None)
            else:
                desired = firewall_source_ips(
                    firewall_config,
                    client_ip=client_ip,
                    existing_ips=ssh_sources if mode == "dynamic_add" else None,
                )

            desired_ports = {
                (p["protocol"], p["port"])
                for p in normalize_firewall_ports(ports)
                if not (p["protocol"] == "tcp" and p["port"] == "22")
            }
            sources_unchanged = set(ssh_sources) == set(desired)
            ports_unchanged = current_ports == desired_ports
            if sources_unchanged and ports_unchanged:
                log.debug(
                    "Firewall already allows %s (ports=%s)",
                    ", ".join(desired),
                    sorted(f"{p}/{n}" for p, n in desired_ports) or "none",
                )
                return True

            log.info(
                "Updating firewall (%s): %s → %s; ports %s → %s",
                mode,
                ", ".join(ssh_sources) or "unset",
                ", ".join(desired),
                sorted(f"{p}/{n}" for p, n in current_ports) or "none",
                sorted(f"{p}/{n}" for p, n in desired_ports) or "none",
            )
            self.manager.set_firewall_rules(
                self.firewall_id,
                desired,
                extra_rules=extra_rules,
                ports=ports,
            )
            return True
        except Exception as exc:
            log.warning("Firewall access refresh failed: %s", exc)
            return False

    def resolve_resources(self) -> bool:
        """
        Populate vm_id / firewall_id / ssh_key_id from saved state or Hetzner API.

        Needed for ``rig destroy --name …`` which constructs a fresh environment
        object without IDs from the original setup session.
        """
        # 1) Shared bucket state
        for env in self.manager._load_state().get("environments", []):
            if env.get("name") != self.name:
                continue
            self.vm_id = self.vm_id or env.get("id") or env.get("vm_id")
            self.vm_ip = self.vm_ip or env.get("ip") or env.get("vm_ip")
            self.ssh_key_id = self.ssh_key_id or env.get("ssh_key_id")
            self.firewall_id = self.firewall_id or env.get("firewall_id")
            break

        # 2) Hetzner API lookup by resource name
        try:
            if not self.vm_id:
                for server in self.manager._request("GET", "/servers").get("servers", []):
                    if server.get("name") == self.name:
                        self.vm_id = server.get("id")
                        public_net = server.get("public_net") or {}
                        self.vm_ip = self.vm_ip or _ipv4_from_public_net(public_net)
                        break
            if not self.firewall_id:
                for fw in self.manager._request("GET", "/firewalls").get("firewalls", []):
                    if fw.get("name") == self.firewall_name:
                        self.firewall_id = fw.get("id")
                        break
            if not self.ssh_key_id:
                for key in self.manager._request("GET", "/ssh_keys").get("ssh_keys", []):
                    if key.get("name") == self.ssh_key_name:
                        self.ssh_key_id = key.get("id")
                        break
        except Exception as exc:
            log.warning("Could not resolve all resources via API: %s", exc)

        found_any = bool(self.vm_id or self.firewall_id or self.ssh_key_id)
        if not found_any:
            log.warning("No Hetzner resources found for '%s'", self.name)
        return found_any

    def run_finalizers_playbook(
        self,
        project_dir: Optional[str] = None,
        stream: bool = True,
    ) -> bool:
        """
        Run ``playbooks.finalizers`` against the live VM before deleting it.

        Soft-fails (returns False but does not raise) when the playbook is
        missing, SSH is unavailable, or Ansible fails — destroy should still
        proceed so the VM is not left orphaned.
        """
        project_dir = os.path.abspath(project_dir or os.getcwd())
        if not self.vm_ip:
            log.debug("Skipping finalizers playbook (no VM IP)")
            return False

        ssh_key_path = f"/tmp/{self.ssh_key_name}"
        if not os.path.exists(ssh_key_path):
            log.warning(
                "Skipping finalizers playbook (SSH key missing at %s)",
                ssh_key_path,
            )
            return False

        project = ProjectConfig(project_dir)
        if not project.load() or not project.config:
            log.debug("Skipping finalizers playbook (no rig.yml in %s)", project_dir)
            return False

        ansible = AnsibleManager(project)
        if not ansible.configure(project_dir):
            log.warning("Skipping finalizers playbook (Ansible configure failed)")
            return False

        finalizers_path = ansible.finalizers_playbook_path
        if not finalizers_path or not os.path.exists(finalizers_path):
            log.debug("Skipping finalizers playbook (not found)")
            return False

        log.info("Running finalizers playbook before deleting VM")
        if not ansible.setup_bootstrap(self.vm_ip, ssh_key_path):
            log.warning("Finalizers playbook skipped (could not reach VM)")
            return False
        ansible.rig_vars = build_rig_ansible_vars(self, project, action="destroy")
        if not ansible._run_one_playbook(finalizers_path, stream=stream):
            log.warning("Finalizers playbook failed; continuing with VM deletion")
            return False
        log.info("Finalizers playbook finished")
        return True

    def cleanup(self, project_dir: Optional[str] = None):
        """Clean up the remote environment (finalizers playbook, then delete VM)."""
        log.info("Destroying remote environment '%s'", self.name)
        project_dir = os.path.abspath(project_dir or os.getcwd())
        self.resolve_resources()
        # Refresh client IP allow-list before finalizers (needs SSH to the VM).
        self.ensure_firewall_access(project_dir=project_dir)
        self.run_finalizers_playbook(project_dir=project_dir, stream=True)

        if self.vm_id:
            log.debug("Deleting VM id=%s", self.vm_id)
            if self.manager.delete_vm(self.vm_id):
                log.debug("VM deleted")
            else:
                log.warning("VM deletion failed or already deleted")
        else:
            log.debug("No VM id to delete")

        if self.firewall_id:
            log.debug("Deleting firewall id=%s", self.firewall_id)
            if self.manager.delete_firewall(self.firewall_id):
                log.debug("Firewall deleted")
            else:
                log.warning("Firewall deletion failed or already deleted")
        else:
            log.debug("No firewall id to delete")

        if self.ssh_key_id:
            log.debug("Deleting SSH key id=%s", self.ssh_key_id)
            if self.manager.delete_ssh_key(self.ssh_key_id):
                log.debug("SSH key deleted")
            else:
                log.warning("SSH key deletion failed or already deleted")
        else:
            log.debug("No SSH key id to delete")

        log.debug("Removing state from shared bucket")
        state = self.manager._load_state()
        environments = [env for env in state.get('environments', []) if env.get('name') != self.name]
        state['environments'] = environments
        state['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        with open('/tmp/state.yml', 'w') as f:
            yaml.dump(state, f, default_flow_style=False, sort_keys=False)

        if self.manager.storage_manager:
            if self.manager._ensure_state_bucket():
                self.manager.storage_manager.upload_file(
                    self.manager.state_bucket,
                    '/tmp/state.yml',
                    f"{self.manager.state_prefix}/state.yml"
                )
                self.manager.storage_manager.delete_object(
                    self.manager.state_bucket,
                    f"{self.manager.state_prefix}/environments/{self.name}.yml"
                )
                log.info("State synced")
            else:
                log.warning("Could not update state bucket")
        else:
            log.debug("No storage manager; skipped remote state cleanup")

        log.debug("Cleaning up local SSH keys and config host")
        subprocess.run(['rm', '-f', f'/tmp/{self.ssh_key_name}'], capture_output=True)
        subprocess.run(['rm', '-f', f'/tmp/{self.ssh_key_name}.pub'], capture_output=True)
        try:
            self.remove_ssh_config_host()
        except OSError:
            pass

        log.info("Destroyed '%s'", self.name)

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
                log.info("State synced")

            return True

        except subprocess.CalledProcessError as e:
            print(f"Error installing agent: {e}")
            return False

    @staticmethod
    @staticmethod
    def agent_remote_command(
        agent_type: str = "opencode",
        extra_args: Optional[List[str]] = None,
    ) -> str:
        """Shell command run on the VM inside an interactive SSH TTY."""
        try:
            from cli.agents import normalize_agent_type
            agent = normalize_agent_type(agent_type)
        except ValueError:
            agent = "opencode"

        suffix = ""
        if extra_args:
            suffix = " " + " ".join(shlex.quote(str(arg)) for arg in extra_args if arg is not None)

        if agent == "claude":
            return (
                'export PATH="$HOME/.local/bin:$PATH"; '
                "set -a; "
                "[ -f /root/.claude/credentials.env ] && . /root/.claude/credentials.env; "
                "set +a; "
                f"mkdir -p {REMOTE_WORKSPACE_ROOT} && "
                f"cd {REMOTE_WORKSPACE_ROOT} && exec claude{suffix}"
            )
        return (
            'export PATH="$HOME/.opencode/bin:/usr/local/bin:$PATH"; '
            "set -a; "
            "[ -f /root/.config/opencode/credentials.env ] && "
            ". /root/.config/opencode/credentials.env; "
            "set +a; "
            f"mkdir -p {REMOTE_WORKSPACE_ROOT} && "
            f"cd {REMOTE_WORKSPACE_ROOT} && exec opencode{suffix}"
        )

    def attach_to_agent(
        self,
        agent_type: Optional[str] = None,
        project_dir: Optional[str] = None,
        agent_args: Optional[List[str]] = None,
    ) -> int:
        """
        Open an interactive SSH TTY to the coding agent on the remote VM.

        ``agent_args`` are appended to the remote ``opencode`` / ``claude``
        invocation (e.g. ``--continue`` to resume the last session).

        Returns the SSH process exit code (0 on success).
        """
        if not self.vm_ip or not self.ssh_key_name:
            log.error("VM not ready for attachment")
            return 1

        if not agent_type:
            project = ProjectConfig(os.path.abspath(project_dir or os.getcwd()))
            project.load()
            agent_type = (
                (project.config.get("agent") or {}).get("type")
                or project.get_vars("coding_agent_type")
                or "opencode"
            )

        ssh_key_path = f"/tmp/{self.ssh_key_name}"
        remote_cmd = self.agent_remote_command(agent_type, extra_args=agent_args)
        ssh_cmd = [
            "ssh",
            "-tt",
            "-i",
            ssh_key_path,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "IdentitiesOnly=yes",
            f"root@{self.vm_ip}",
            remote_cmd,
        ]

        log.info("Attaching to %s on %s", agent_type, self.vm_ip)
        log.debug("SSH key=%s", ssh_key_path)
        log.debug("Remote command=%s", remote_cmd)

        try:
            completed = subprocess.run(ssh_cmd)
            return int(completed.returncode or 0)
        except FileNotFoundError:
            log.error("ssh client not found on PATH")
            return 1
        except KeyboardInterrupt:
            log.info("Detached from agent session")
            return 130

    def ssh_host_alias(self) -> str:
        """SSH config Host alias used by VS Code / Cursor Remote-SSH."""
        return f"rig-{self.name}"

    def ensure_ssh_config_host(
        self,
        ssh_config_path: Optional[str] = None,
    ) -> str:
        """
        Upsert a managed Host block in ``~/.ssh/config`` for this environment.

        Returns the Host alias (e.g. ``rig-dev-abc``).
        """
        if not self.vm_ip or not self.ssh_key_name:
            raise RuntimeError("VM IP and SSH key are required for SSH config")

        alias = self.ssh_host_alias()
        key_path = f"/tmp/{self.ssh_key_name}"
        config_path = os.path.expanduser(ssh_config_path or "~/.ssh/config")
        os.makedirs(os.path.dirname(config_path), mode=0o700, exist_ok=True)

        begin = f"# >>> rig managed: {self.name}"
        end = f"# <<< rig managed: {self.name}"
        block = (
            f"{begin}\n"
            f"Host {alias}\n"
            f"  HostName {self.vm_ip}\n"
            f"  User root\n"
            f"  IdentityFile {key_path}\n"
            f"  IdentitiesOnly yes\n"
            f"  StrictHostKeyChecking no\n"
            f"  UserKnownHostsFile /dev/null\n"
            f"{end}\n"
        )

        existing = ""
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as handle:
                existing = handle.read()

        pattern = re.compile(
            re.escape(begin) + r".*?" + re.escape(end) + r"\n?",
            re.DOTALL,
        )
        if pattern.search(existing):
            updated = pattern.sub(block, existing, count=1)
        else:
            sep = "" if not existing or existing.endswith("\n") else "\n"
            updated = existing + sep + ("\n" if existing.strip() else "") + block

        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(updated)
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            pass
        return alias

    def remove_ssh_config_host(
        self,
        ssh_config_path: Optional[str] = None,
    ) -> None:
        """Remove the managed Host block for this environment, if present."""
        config_path = os.path.expanduser(ssh_config_path or "~/.ssh/config")
        if not os.path.exists(config_path):
            return
        begin = f"# >>> rig managed: {self.name}"
        end = f"# <<< rig managed: {self.name}"
        with open(config_path, encoding="utf-8") as handle:
            existing = handle.read()
        pattern = re.compile(
            re.escape(begin) + r".*?" + re.escape(end) + r"\n?",
            re.DOTALL,
        )
        updated = pattern.sub("", existing)
        if updated != existing:
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(updated)

    @staticmethod
    def resolve_editor_command(prefer: Optional[str] = None) -> Optional[str]:
        """Pick a local editor CLI: code, cursor, codium, …"""
        candidates = []
        if prefer:
            candidates.append(prefer)
        candidates.extend(["code", "code-insiders", "cursor", "codium"])
        seen = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            path = shutil.which(name)
            if path:
                return path
        return None

    def code_server_url(
        self,
        project_dir: Optional[str] = None,
        port: Optional[int] = None,
        folder: Optional[str] = None,
    ) -> str:
        """HTTP URL for the remote code-server UI (opens workspace folder)."""
        if port is None:
            project = ProjectConfig(os.path.abspath(project_dir or os.getcwd()))
            project.load()
            raw = project.get_vars("code_server_port")
            port = int(raw) if raw else 8080
        from urllib.parse import quote
        target = folder or REMOTE_WORKSPACE_ROOT
        return f"http://{self.vm_ip}:{port}/?folder={quote(target)}"

    def open_vscode(
        self,
        remote_path: Optional[str] = None,
        project_dir: Optional[str] = None,
        browser: bool = False,
        editor: Optional[str] = None,
        ssh_config_path: Optional[str] = None,
    ) -> int:
        """
        Open VS Code (or Cursor) against the remote VM via Remote-SSH.

        Editor is the default. Pass ``browser=True`` to open code-server in a
        browser instead.
        """
        if not self.vm_ip or not self.ssh_key_name:
            log.error("VM not ready for VS Code")
            return 1

        ssh_key_path = f"/tmp/{self.ssh_key_name}"
        if not os.path.exists(ssh_key_path):
            log.error("SSH private key not found at %s", ssh_key_path)
            log.error("Re-run create on this machine, or restore the key before opening VS Code")
            return 1

        if not remote_path:
            remote_path = REMOTE_WORKSPACE_ROOT
        if not remote_path.startswith("/"):
            remote_path = "/" + remote_path

        if browser:
            url = self.code_server_url(
                project_dir=project_dir, folder=remote_path
            )
            try:
                from cli.encrypt import (
                    VSCODE_BROWSER_PASSWORD_VAR,
                    ensure_vscode_browser_password,
                    read_vscode_browser_password,
                )
                root = project_dir or os.getcwd()
                ensure_vscode_browser_password(project_dir=root)
                password = read_vscode_browser_password(project_dir=root)
                if password:
                    log.info(
                        "code-server password (%s): %s",
                        VSCODE_BROWSER_PASSWORD_VAR,
                        password,
                    )
                else:
                    log.info(
                        "Decrypt the browser password with: "
                        "rig vars decrypt %s",
                        VSCODE_BROWSER_PASSWORD_VAR,
                    )
            except Exception as exc:
                log.warning("Could not load vscode_browser_password: %s", exc)
                log.info(
                    "Decrypt with: rig vars decrypt vscode_browser_password"
                )
            log.info("Opening code-server at %s", url)
            try:
                if webbrowser.open(url):
                    return 0
                log.info("Open this URL in your browser: %s", url)
                return 0
            except Exception as exc:
                log.error("Error opening browser: %s", exc)
                log.info("Open this URL manually: %s", url)
                return 1

        editor_bin = self.resolve_editor_command(prefer=editor)
        if not editor_bin:
            url = self.code_server_url(
                project_dir=project_dir, folder=remote_path
            )
            log.error("No local VS Code/Cursor CLI found (tried: code, cursor, codium)")
            log.error("Install the 'code' shell command, or run: rig dev --browser")
            log.debug("code-server URL: %s", url)
            return 1

        alias = self.ensure_ssh_config_host(ssh_config_path=ssh_config_path)
        folder_uri = f"vscode-remote://ssh-remote+{alias}{remote_path}"
        cmd = [editor_bin, "--folder-uri", folder_uri]
        log.info("Opening %s on %s (%s)", editor_bin, self.vm_ip, remote_path)
        log.debug("SSH host alias=%s folder_uri=%s", alias, folder_uri)
        try:
            completed = subprocess.run(cmd, check=False)
            return int(completed.returncode or 0)
        except FileNotFoundError:
            log.error("Editor '%s' not found", editor_bin)
            return 1

    def get_agent_connection_info(self) -> Dict[str, Any]:
        """Get information needed to connect to the remote agent"""
        project = ProjectConfig(os.getcwd())
        project.load()
        agent_type = (
            (project.config.get("agent") or {}).get("type")
            or project.get_vars("coding_agent_type")
            or "opencode"
        )
        return {
            "vm_ip": self.vm_ip,
            "ssh_key_path": f"/tmp/{self.ssh_key_name}",
            "agent_type": agent_type,
            "remote_command": self.agent_remote_command(agent_type),
            "agent_status": "installed" if self.vm_ip else "not installed",
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
            'availability_zone': 'hel1',
            'image': 'ubuntu-24.04'
        },
        'firewall': {
            'mode': DEFAULT_FIREWALL_MODE,
            'allowed_ips': [],
            'ports': [],
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


class VaultEncrypted(str):
    """Opaque Ansible ``!vault`` scalar that round-trips through PyYAML."""


class _VarsLoader(yaml.SafeLoader):
    pass


class _VarsDumper(yaml.SafeDumper):
    pass


def _construct_vault(loader, node):
    return VaultEncrypted(loader.construct_scalar(node))


def _represent_vault(dumper, data):
    return dumper.represent_scalar("!vault", str(data), style="|")


_VarsLoader.add_constructor("!vault", _construct_vault)
_VarsDumper.add_representer(VaultEncrypted, _represent_vault)


# Auto-generated system bootstrap playbook (.rig/system.yml).
# Referenced from root rig.yml playbooks.system. Roles can replace this later.
SYSTEM_PLAYBOOK = r"""---
# System playbook: base packages, VS Code (code-server), coding agent.
- name: Provision remote system
  hosts: remote_vm
  become: true
  gather_facts: true
  vars:
    coding_agent_type: "{{ coding_agent_type | default('opencode') }}"
    code_server_port: "{{ code_server_port | default(8080) }}"
    code_server_user: "{{ code_server_user | default('coder') }}"
  tasks:
    - name: Wait for cloud-init to finish
      command: cloud-init status --wait
      changed_when: false
      failed_when: false

    - name: Update apt cache
      apt:
        update_cache: true
        cache_valid_time: 3600

    - name: Install base packages
      apt:
        name:
          - curl
          - wget
          - ca-certificates
          - gnupg
          - git
          - python3
          - python3-pip
          - vim
          - htop
          - build-essential
        state: present

    - name: Ensure project workspace directory exists
      file:
        path: "{{ workspace_root | default('/workspace') }}"
        state: directory
        owner: root
        group: root
        mode: "0755"

    # --- GitHub CLI (gh) when workspace repos use github.com ---
    - name: Ensure apt keyrings directory exists
      file:
        path: /etc/apt/keyrings
        state: directory
        mode: "0755"
      when: "'github' in (git_providers | default([]))"

    - name: Add GitHub CLI apt keyring
      get_url:
        url: https://cli.github.com/packages/githubcli-archive-keyring.gpg
        dest: /etc/apt/keyrings/githubcli-archive-keyring.gpg
        mode: "0644"
      when: "'github' in (git_providers | default([]))"

    - name: Add GitHub CLI apt repository
      copy:
        dest: /etc/apt/sources.list.d/github-cli.list
        mode: "0644"
        content: |
          deb [arch={{ ansible_facts['architecture'] | replace('x86_64', 'amd64') | replace('aarch64', 'arm64') }} signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main
      when: "'github' in (git_providers | default([]))"

    - name: Update apt cache for GitHub CLI
      apt:
        update_cache: true
      when: "'github' in (git_providers | default([]))"

    - name: Install GitHub CLI (gh)
      apt:
        name: gh
        state: present
      when: "'github' in (git_providers | default([]))"

    # --- GitLab CLI (glab) when workspace repos use gitlab.com ---
    - name: Install GitLab CLI (glab)
      shell: |
        set -euo pipefail
        if command -v glab >/dev/null 2>&1; then
          exit 0
        fi
        arch="$(dpkg --print-architecture)"
        version="$(curl -fsSL https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases/permalink/latest \
          | python3 -c "import sys, json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))")"
        tmp="$(mktemp -d)"
        trap 'rm -rf "$tmp"' EXIT
        curl -fsSL -o "$tmp/glab.tgz" \
          "https://gitlab.com/gitlab-org/cli/-/releases/v${version}/downloads/glab_${version}_linux_${arch}.tar.gz"
        tar -xzf "$tmp/glab.tgz" -C "$tmp"
        if [ -x "$tmp/bin/glab" ]; then
          install -m 0755 "$tmp/bin/glab" /usr/local/bin/glab
        elif [ -x "$tmp/glab" ]; then
          install -m 0755 "$tmp/glab" /usr/local/bin/glab
        else
          find "$tmp" -type f -name glab -executable -exec install -m 0755 {} /usr/local/bin/glab \;
        fi
        command -v glab >/dev/null
      args:
        creates: /usr/local/bin/glab
        executable: /bin/bash
      when: "'gitlab' in (git_providers | default([]))"

    - name: Create code-server user
      user:
        name: "{{ code_server_user }}"
        shell: /bin/bash
        create_home: true
        system: false

    - name: Install code-server (VS Code)
      shell: curl -fsSL https://code-server.dev/install.sh | sh
      args:
        creates: /usr/bin/code-server

    - name: Ensure code-server config directory exists
      file:
        path: "/home/{{ code_server_user }}/.config/code-server"
        state: directory
        owner: "{{ code_server_user }}"
        group: "{{ code_server_user }}"
        mode: "0700"

    - name: Configure code-server
      copy:
        dest: "/home/{{ code_server_user }}/.config/code-server/config.yaml"
        owner: "{{ code_server_user }}"
        group: "{{ code_server_user }}"
        mode: "0600"
        content: |
          bind-addr: 0.0.0.0:{{ code_server_port }}
          auth: password
          password: "{{ vscode_browser_password }}"
          cert: false

    - name: Enable and start code-server for user
      systemd:
        name: "code-server@{{ code_server_user }}"
        enabled: true
        state: started
        daemon_reload: true

    # --- Coding agent: OpenCode ---
    # Do not use Ansible creates= here: a zero-byte stub is executable and would
    # skip reinstall forever (ssh then fails with "opencode: Success").
    - name: Install OpenCode CLI
      shell: |
        set -euo pipefail
        bin=/root/.opencode/bin/opencode
        if [ -x "$bin" ] && [ -s "$bin" ] && "$bin" --version >/dev/null 2>&1; then
          exit 0
        fi
        rm -f "$bin"
        curl -fsSL https://opencode.ai/install | bash
        test -s "$bin"
        test -x "$bin"
        "$bin" --version
      args:
        executable: /bin/bash
      environment:
        HOME: /root
      when: coding_agent_type == "opencode"

    - name: Symlink OpenCode onto PATH
      file:
        src: /root/.opencode/bin/opencode
        dest: /usr/local/bin/opencode
        state: link
        force: true
      when: coding_agent_type == "opencode"

    - name: Ensure OpenCode bin is on PATH for login shells
      lineinfile:
        path: /root/.bashrc
        line: 'export PATH="$HOME/.opencode/bin:$PATH"'
        create: true
      when: coding_agent_type == "opencode"

    - name: Ensure OpenCode config directory exists
      file:
        path: /root/.config/opencode
        state: directory
        mode: "0700"
      when: coding_agent_type == "opencode"

    - name: Deploy OpenCode config from .rig/.opencode
      copy:
        src: "{{ playbook_dir }}/.opencode/opencode.json"
        dest: /root/.config/opencode/opencode.json
        mode: "0600"
      when: coding_agent_type == "opencode"

    - name: Deploy OpenCode MCP config from .rig/.opencode
      copy:
        src: "{{ playbook_dir }}/.opencode/mcp.json"
        dest: /root/.config/opencode/mcp.json
        mode: "0600"
      when: coding_agent_type == "opencode"

    - name: Merge OpenCode MCP servers into opencode.json
      shell: |
        python3 - <<'PY'
        import json
        cfg_path = "/root/.config/opencode/opencode.json"
        mcp_path = "/root/.config/opencode/mcp.json"
        with open(cfg_path, encoding="utf-8") as handle:
            cfg = json.load(handle)
        with open(mcp_path, encoding="utf-8") as handle:
            mcp = json.load(handle)
        cfg["mcp"] = mcp.get("mcp", mcp)
        with open(cfg_path, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2)
            handle.write("\n")
        PY
      when: coding_agent_type == "opencode"

    - name: Deploy AGENTS.md as OpenCode global rules
      copy:
        src: "{{ playbook_dir }}/AGENTS.md"
        dest: /root/.config/opencode/AGENTS.md
        mode: "0644"
      when: coding_agent_type == "opencode"

    - name: Write OpenCode credentials env file
      copy:
        dest: /root/.config/opencode/credentials.env
        mode: "0600"
        content: |
          CODING_AGENT_API_KEY={{ coding_agent_api_key }}
          ANTHROPIC_API_KEY={{ coding_agent_api_key }}
          OPENCODE_API_KEY={{ coding_agent_api_key }}
      no_log: true
      when:
        - coding_agent_type == "opencode"
        - coding_agent_api_key is defined
        - coding_agent_api_key | length > 0

    # --- Coding agent: Claude Code ---
    - name: Install Claude Code CLI
      shell: curl -fsSL https://claude.ai/install.sh | bash
      args:
        creates: /root/.local/bin/claude
      when: coding_agent_type == "claude"

    - name: Ensure Claude local bin is on PATH
      lineinfile:
        path: /root/.bashrc
        line: 'export PATH="$HOME/.local/bin:$PATH"'
        create: true
      when: coding_agent_type == "claude"

    - name: Ensure Claude config directory exists
      file:
        path: /root/.claude
        state: directory
        mode: "0700"
      when: coding_agent_type == "claude"

    - name: Deploy Claude settings from .rig/.claude
      copy:
        src: "{{ playbook_dir }}/.claude/settings.json"
        dest: /root/.claude/settings.json
        mode: "0600"
      when: coding_agent_type == "claude"

    - name: Deploy Claude MCP config as project .mcp.json
      copy:
        src: "{{ playbook_dir }}/.claude/mcp.json"
        dest: /root/.mcp.json
        mode: "0600"
      when: coding_agent_type == "claude"

    - name: Deploy AGENTS.md as Claude global rules (CLAUDE.md)
      copy:
        src: "{{ playbook_dir }}/AGENTS.md"
        dest: /root/.claude/CLAUDE.md
        mode: "0644"
      when: coding_agent_type == "claude"

    - name: Place AGENTS.md in workspace for agent discovery
      copy:
        src: "{{ playbook_dir }}/AGENTS.md"
        dest: "{{ workspace_root | default('/workspace') }}/AGENTS.md"
        mode: "0644"

    - name: Write Claude credentials env file
      copy:
        dest: /root/.claude/credentials.env
        mode: "0600"
        content: |
          CODING_AGENT_API_KEY={{ coding_agent_api_key }}
          ANTHROPIC_API_KEY={{ coding_agent_api_key }}
      no_log: true
      when:
        - coding_agent_type == "claude"
        - coding_agent_api_key is defined
        - coding_agent_api_key | length > 0

    - name: Install coding-agent profile.d env exports
      copy:
        dest: /etc/profile.d/rig-coding-agent.sh
        mode: "0644"
        content: |
          # Managed by rig system.yml — API key for the selected coding agent.
          if [ -f /root/.config/opencode/credentials.env ]; then
            set -a
            # shellcheck disable=SC1091
            . /root/.config/opencode/credentials.env
            set +a
          fi
          if [ -f /root/.claude/credentials.env ]; then
            set -a
            # shellcheck disable=SC1091
            . /root/.claude/credentials.env
            set +a
          fi
      when: coding_agent_api_key is defined and coding_agent_api_key | length > 0
"""

# Workspace-specific playbook (.rig/workspace.yml). Project/workspace dependent.
WORKSPACE_PLAYBOOK = """---
# Workspace playbook: clone configured repos and workspace-specific setup.
# Rig injects runtime vars prefixed with rig_ (e.g. rig_env_name, rig_vm_ip).
- name: Configure workspace
  hosts: remote_vm
  become: true
  gather_facts: false
  vars:
    workspace_root: "{{ workspace_root | default('/workspace') }}"
  tasks:
    - name: Ensure project workspace directory exists
      file:
        path: "{{ workspace_root }}"
        state: directory
        owner: root
        group: root
        mode: "0755"

    - name: Clone configured git repositories
      vars:
        _pat_var: "{{ item.password_var | default('') }}"
        _pat: "{{ lookup('vars', _pat_var, default='') if _pat_var else '' }}"
        # GitHub expects x-access-token; GitLab expects oauth2.
        _auth_user: "{{ 'oauth2' if ('gitlab.com' in item.repo) else 'x-access-token' }}"
      git:
        repo: >-
          {{ item.repo
             if (_pat | length == 0 or not item.repo.startswith('https://'))
             else item.repo.replace('https://', 'https://' ~ _auth_user ~ ':' ~ _pat ~ '@', 1) }}
        dest: "{{ item.dest }}"
        version: "{{ item.version | default(omit) }}"
        accept_hostkey: "{{ item.accept_hostkey | default(true) }}"
        force: "{{ item.force | default(false) }}"
      loop: "{{ git.clone_repos | default([]) }}"
      when: git is defined and git.clone_repos is defined
"""

FINALIZERS_PLAYBOOK = """---
# Finalizers: custom workspace resources to release before the VM is deleted
# (``rig destroy``). May target off-VM resources (DNS, volumes, cloud APIs,
# SaaS tenants) as well as the host. Runs with rig_* vars (e.g. rig_env_name).
- name: Workspace finalizers
  hosts: remote_vm
  become: true
  gather_facts: false
  tasks:
    - name: Finalizers placeholder (customize this playbook)
      debug:
        msg: "No custom finalizer tasks for {{ rig_env_name | default('env') }}"
"""


class ProjectConfig:
    """Manages root rig.yml plus .rig/ support files (vars, playbooks)."""

    RIG_DIR = ".rig"
    RIG_YAML = "rig.yml"
    VARS_YAML = "vars.yml"
    SYSTEM_YAML = "system.yml"
    WORKSPACE_YAML = "workspace.yml"
    FINALIZERS_YAML = "finalizers.yml"
    # Back-compat aliases
    INIT_YAML = SYSTEM_YAML
    SETUP_YAML = RIG_YAML
    REQUIREMENTS_YML = "requirements.yml"
    # Back-compat alias
    REQUIREMENTS_TXT = REQUIREMENTS_YML
    ROLES_DIR = "roles"

    def __init__(self, project_dir: Optional[str] = None):
        self.project_dir = project_dir or os.getcwd()
        self.rig_dir = os.path.join(self.project_dir, self.RIG_DIR)
        self.config: Dict[str, Any] = {}
        self.vars_config: Dict[str, Any] = {}
        self.requirements: List[str] = []
        self.vault_password: Optional[str] = None

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.project_dir, self.RIG_YAML)

    @property
    def setup_config(self) -> Dict[str, Any]:
        """Backward-compatible view of bootstrap/agent settings living in rig.yml."""
        return {
            "bootstrap": self.config.get("bootstrap") or {
                "enabled": True,
                "playbooks": self.config.get("playbooks") or {},
                "inventory": self.config.get("inventory", f"{self.RIG_DIR}/inventory.yml"),
            },
            "agent": self.config.get("agent") or {},
            "dependencies": self.config.get("dependencies") or self.requirements,
        }

    def _ensure_rig_dir(self) -> bool:
        """Ensure .rig directory exists, create if not"""
        if not os.path.exists(self.rig_dir):
            os.makedirs(self.rig_dir)
            print(f"Created .rig directory at {self.rig_dir}")
            return True
        return True

    def _default_config(self, name: Optional[str] = None, ansible_enabled: bool = True) -> Dict[str, Any]:
        return {
            "kind": "workspace",
            "name": name or os.path.basename(self.project_dir.rstrip(os.sep)) or "workspace",
            "description": "Remote environment",
            "ansible_enabled": ansible_enabled,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "vm": {
                "type": "cax11",
                "availability_zone": "hel1",
                "image": "ubuntu-24.04",
            },
            "firewall": {
                # open | static | dynamic_replace | dynamic_add (default)
                # dynamic_add keeps existing allow-list entries and adds the
                # current CLI public IP when missing (re-checked on access).
                "mode": DEFAULT_FIREWALL_MODE,
                "allowed_ips": [],  # used by static; also merged into dynamic modes
                # Extra inbound ports (same source IPs as SSH), e.g. app listeners:
                # ports: [8080, "3000-3001", {port: 53, protocol: udp}]
                "ports": [],
            },
            "agent": {
                "type": "opencode",
                "vscode": True,
                "credentials_var": "coding_agent_api_key",
            },
            "playbooks": {
                "system": f"{self.RIG_DIR}/{self.SYSTEM_YAML}",
                "workspace": f"{self.RIG_DIR}/{self.WORKSPACE_YAML}",
                "finalizers": f"{self.RIG_DIR}/{self.FINALIZERS_YAML}",
            },
            "inventory": f"{self.RIG_DIR}/inventory.yml",
            "repos": [],
        }

    def _default_vars(self) -> Dict[str, Any]:
        return {
            "ansible_user": "root",
            "ansible_connection": "ssh",
            "ansible_python_interpreter": "/usr/bin/python3",
            "coding_agent_type": "opencode",
            "code_server_port": 8080,
            "code_server_user": "coder",
            "workspace_root": REMOTE_WORKSPACE_ROOT,
            "git": {"clone_repos": [], "ssh_repos": []},
            "git_providers": [],
        }

    def _merge_missing(self, target: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(target)
        for key, value in defaults.items():
            if key not in merged:
                merged[key] = value
            elif isinstance(value, dict) and isinstance(merged.get(key), dict):
                nested = dict(merged[key])
                for nested_key, nested_value in value.items():
                    if nested_key not in nested:
                        nested[nested_key] = nested_value
                merged[key] = nested
        return merged

    def load(self) -> bool:
        """Load root rig.yml and .rig support files"""
        try:
            if os.path.exists(self.manifest_path):
                with open(self.manifest_path, "r", encoding="utf-8") as handle:
                    self.config = yaml.safe_load(handle) or {}
            else:
                # Migrate legacy .rig/rig.yml + setup.yml if present
                legacy_rig = os.path.join(self.rig_dir, self.RIG_YAML)
                legacy_setup = os.path.join(self.rig_dir, "setup.yml")
                if os.path.exists(legacy_rig):
                    with open(legacy_rig, "r", encoding="utf-8") as handle:
                        self.config = yaml.safe_load(handle) or {}
                if os.path.exists(legacy_setup):
                    with open(legacy_setup, "r", encoding="utf-8") as handle:
                        legacy = yaml.safe_load(handle) or {}
                        self.config = self._merge_missing(self.config, {
                            "agent": legacy.get("agent") or {},
                            "playbooks": {
                                "system": f"{self.RIG_DIR}/{self.SYSTEM_YAML}",
                                "workspace": f"{self.RIG_DIR}/{self.WORKSPACE_YAML}",
                                "finalizers": f"{self.RIG_DIR}/{self.FINALIZERS_YAML}",
                            },
                            "inventory": f"{self.RIG_DIR}/inventory.yml",
                            "bootstrap": legacy.get("bootstrap") or {},
                        })

            vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
            if os.path.exists(vars_path):
                self.vars_config = self._load_vars_with_vault(vars_path)

            requirements_path = os.path.join(self.rig_dir, self.REQUIREMENTS_YML)
            legacy_requirements = os.path.join(self.rig_dir, "requirements.txt")
            if os.path.exists(requirements_path):
                with open(requirements_path, "r", encoding="utf-8") as handle:
                    loaded = yaml.safe_load(handle) or {}
                reqs: List[str] = []
                if isinstance(loaded, dict):
                    for item in loaded.get("collections") or []:
                        if isinstance(item, dict) and item.get("name"):
                            reqs.append(str(item["name"]))
                        elif isinstance(item, str):
                            reqs.append(item)
                    for item in loaded.get("roles") or []:
                        if isinstance(item, dict) and item.get("name"):
                            reqs.append(str(item["name"]))
                        elif isinstance(item, str):
                            reqs.append(item)
                elif isinstance(loaded, list):
                    reqs = [str(item) for item in loaded if item]
                self.requirements = reqs
            elif os.path.exists(legacy_requirements):
                with open(legacy_requirements, "r", encoding="utf-8") as handle:
                    self.requirements = [
                        line.strip().lstrip("- ").strip()
                        for line in handle
                        if line.strip() and not line.startswith("#")
                    ]

            vault_password_path = os.path.join(self.rig_dir, ".vault_password")
            if os.path.exists(vault_password_path):
                with open(vault_password_path, "r", encoding="utf-8") as handle:
                    self.vault_password = handle.read().strip()

            return True
        except Exception as e:
            print(f"Warning: Could not load project config: {e}")
            return False

    def _load_vars_with_vault(self, vars_path: str) -> Dict:
        """Load vars.yml with optional Ansible Vault encryption support"""
        try:
            with open(vars_path, 'r') as f:
                content = f.read()

            # Check if content is vault encrypted (whole-file vault)
            if content.startswith('$ANSIBLE_VAULT'):
                # Prefer temporary data key file; fall back to .rig/.vault_password.
                import tempfile
                from cli.encrypt import DEFAULT_DATA_KEY_PATH
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    vault_cmd = ['ansible-vault', 'view', tmp_path]
                    if os.path.exists(DEFAULT_DATA_KEY_PATH):
                        vault_cmd[2:2] = ['--vault-password-file', DEFAULT_DATA_KEY_PATH]
                    elif self.vault_password:
                        vault_cmd[2:2] = ['--vault-password', self.vault_password]
                    else:
                        raise ValueError(
                            "Vault encrypted file requires temporary data key at "
                            f"{DEFAULT_DATA_KEY_PATH} or .rig/.vault_password."
                        )
                    result = subprocess.run(
                        vault_cmd,
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
                # Regular YAML (may contain inline !vault scalars)
                return yaml.load(content, Loader=_VarsLoader) or {}
        except Exception as e:
            print(f"Warning: Could not load vars with vault: {e}")
            return {}

    def _dump_vars(self, vars_path: str) -> None:
        with open(vars_path, "w", encoding="utf-8") as handle:
            yaml.dump(
                self.vars_config,
                handle,
                Dumper=_VarsDumper,
                default_flow_style=False,
                sort_keys=False,
            )

    def save(self, write_vars: bool = True) -> bool:
        """Save root rig.yml and optional .rig support files."""
        try:
            self._ensure_rig_dir()

            with open(self.manifest_path, "w", encoding="utf-8") as handle:
                yaml.dump(self.config, handle, default_flow_style=False, sort_keys=False)

            if write_vars:
                vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
                self._dump_vars(vars_path)

            requirements_path = os.path.join(self.rig_dir, self.REQUIREMENTS_YML)
            collections = []
            roles = []
            for req in self.requirements:
                name = str(req).strip()
                if not name:
                    continue
                if name.startswith("geerlingguy.") or (
                    name.count(".") == 1 and not name.startswith(("community.", "ansible."))
                ):
                    roles.append({"name": name})
                else:
                    collections.append({"name": name})
            payload: Dict[str, Any] = {}
            if collections:
                payload["collections"] = collections
            if roles:
                payload["roles"] = roles
            with open(requirements_path, "w", encoding="utf-8") as handle:
                yaml.dump(payload or {"collections": []}, handle, default_flow_style=False, sort_keys=False)

            return True
        except Exception as e:
            print(f"Warning: Could not save project config: {e}")
            return False

    def init(
        self,
        name: Optional[str] = None,
        ansible_enabled: bool = True,
        agent_type: Optional[str] = None,
    ) -> bool:
        """Idempotently initialize project config, adding any missing files."""
        try:
            self.load()
            self._ensure_rig_dir()
            created: List[str] = []

            defaults = self._default_config(name=name, ansible_enabled=ansible_enabled)
            if not self.config:
                self.config = defaults
                created.append(self.RIG_YAML)
            else:
                before = yaml.dump(self.config, sort_keys=True)
                if name and not self.config.get("name"):
                    self.config["name"] = name
                self.config = self._merge_missing(self.config, defaults)
                # Prefer playbooks.system/workspace/finalizers over legacy bootstrap.playbook
                playbooks = self.config.setdefault("playbooks", {})
                playbooks.setdefault("system", f"{self.RIG_DIR}/{self.SYSTEM_YAML}")
                playbooks.setdefault("workspace", f"{self.RIG_DIR}/{self.WORKSPACE_YAML}")
                playbooks.setdefault("finalizers", f"{self.RIG_DIR}/{self.FINALIZERS_YAML}")
                self.config.setdefault("inventory", f"{self.RIG_DIR}/inventory.yml")
                if yaml.dump(self.config, sort_keys=True) != before and self.RIG_YAML not in created:
                    created.append(f"{self.RIG_YAML} (updated)")

            if agent_type:
                from cli.agents import normalize_agent_type
                chosen = normalize_agent_type(agent_type)
                self.config.setdefault("agent", {})
                self.config["agent"]["type"] = chosen
                self.config["agent"]["credentials_var"] = "coding_agent_api_key"

            if not self.requirements:
                self.requirements = [
                    "community.general",
                    "ansible.posix",
                    "geerlingguy.docker",
                    "geerlingguy.python",
                ]

            vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
            write_vars = not os.path.exists(vars_path)
            if write_vars:
                self.vars_config = self._merge_missing(self.vars_config or {}, self._default_vars())
                created.append(f"{self.RIG_DIR}/{self.VARS_YAML}")
            elif not self.vars_config:
                self.vars_config = self._load_vars_with_vault(vars_path)

            requirements_path = os.path.join(self.rig_dir, self.REQUIREMENTS_YML)
            if not os.path.exists(requirements_path):
                created.append(f"{self.RIG_DIR}/{self.REQUIREMENTS_YML}")

            system_path = os.path.join(self.rig_dir, self.SYSTEM_YAML)
            workspace_path = os.path.join(self.rig_dir, self.WORKSPACE_YAML)
            finalizers_path = os.path.join(self.rig_dir, self.FINALIZERS_YAML)
            system_missing = not os.path.exists(system_path)
            workspace_missing = not os.path.exists(workspace_path)
            finalizers_missing = not os.path.exists(finalizers_path)

            if not self.save(write_vars=write_vars):
                return False
            # system.yml is auto-generated; always refresh on init.
            if not self.write_system_playbook(force=True):
                return False
            if not self.write_workspace_playbook(force=False):
                return False
            if not self.write_finalizers_playbook(force=False):
                return False

            from cli.agents import ensure_agent_scaffold
            scaffold_agent = (self.config.get("agent") or {}).get("type") or "opencode"
            for path in ensure_agent_scaffold(self.rig_dir, agent_type=scaffold_agent):
                created.append(path)
            # Keep vars in sync for system.yml conditionals.
            if self.vars_config is not None:
                self.vars_config["coding_agent_type"] = scaffold_agent
                vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
                if os.path.exists(vars_path) or write_vars:
                    # Re-dump when we already wrote vars, or create/update coding_agent_type.
                    disk = self._load_vars_with_vault(vars_path) if os.path.exists(vars_path) else {}
                    if disk:
                        self.vars_config = {**disk, **self.vars_config}
                    self.vars_config["coding_agent_type"] = scaffold_agent
                    self._dump_vars(vars_path)

            if system_missing and os.path.exists(system_path):
                created.append(f"{self.RIG_DIR}/{self.SYSTEM_YAML}")
            if workspace_missing and os.path.exists(workspace_path):
                created.append(f"{self.RIG_DIR}/{self.WORKSPACE_YAML}")
            if finalizers_missing and os.path.exists(finalizers_path):
                created.append(f"{self.RIG_DIR}/{self.FINALIZERS_YAML}")

            # Drop obsolete nested copies if present
            for obsolete in (
                os.path.join(self.rig_dir, "rig.yml"),
                os.path.join(self.rig_dir, "setup.yml"),
                os.path.join(self.rig_dir, "init.yml"),
            ):
                if os.path.exists(obsolete):
                    try:
                        os.remove(obsolete)
                        print(f"Removed obsolete {obsolete}")
                    except OSError:
                        pass

            if created:
                print("Ensured project files: " + ", ".join(dict.fromkeys(created)))
            else:
                print(f"Project already initialized at {self.project_dir}")
            return True
        except Exception as e:
            print(f"Error initializing project: {e}")
            return False

    def write_system_playbook(self, force: bool = False) -> bool:
        """Write auto-generated .rig/system.yml when missing (or force)."""
        try:
            self._ensure_rig_dir()
            path = os.path.join(self.rig_dir, self.SYSTEM_YAML)
            if os.path.exists(path) and not force:
                return True
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(SYSTEM_PLAYBOOK.lstrip("\n"))
            return True
        except Exception as e:
            print(f"Error writing system playbook: {e}")
            return False

    def write_workspace_playbook(self, force: bool = False) -> bool:
        """Write .rig/workspace.yml when missing (or force)."""
        try:
            self._ensure_rig_dir()
            path = os.path.join(self.rig_dir, self.WORKSPACE_YAML)
            if os.path.exists(path) and not force:
                return True
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(WORKSPACE_PLAYBOOK.lstrip("\n"))
            return True
        except Exception as e:
            print(f"Error writing workspace playbook: {e}")
            return False

    def write_finalizers_playbook(self, force: bool = False) -> bool:
        """Write .rig/finalizers.yml when missing (or force)."""
        try:
            self._ensure_rig_dir()
            path = os.path.join(self.rig_dir, self.FINALIZERS_YAML)
            if os.path.exists(path) and not force:
                return True
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(FINALIZERS_PLAYBOOK.lstrip("\n"))
            return True
        except Exception as e:
            print(f"Error writing finalizers playbook: {e}")
            return False

    def write_init_playbook(self) -> bool:
        """Back-compat alias for write_system_playbook."""
        return self.write_system_playbook(force=True)

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
        return self.save(write_vars=False)

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
        # Reload from disk first so vaulted keys aren't dropped when memory is stale.
        vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
        if os.path.exists(vars_path):
            disk_vars = self._load_vars_with_vault(vars_path)
            if disk_vars:
                merged = dict(disk_vars)
                merged.update(self.vars_config or {})
                self.vars_config = merged
            elif not self.vars_config:
                self.vars_config = {}
        keys = key.split('.')
        vars_config = self.vars_config
        for k in keys[:-1]:
            if k not in vars_config:
                vars_config[k] = {}
            vars_config = vars_config[k]
        vars_config[keys[-1]] = value
        try:
            self._ensure_rig_dir()
            self._dump_vars(vars_path)
            return True
        except Exception as e:
            print(f"Warning: Could not save vars: {e}")
            return False

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


def _valid_component_name(name: str) -> bool:
    """Reject names that would escape the workspace directory."""
    if not name or name in (".", ".."):
        return False
    if "/" in name or "\\" in name or name.startswith("."):
        return False
    return True


def _read_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as handle:
        loaded = yaml.safe_load(handle)
        return loaded if isinstance(loaded, dict) else {}


def _write_yaml(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w") as handle:
        yaml.dump(data, handle, default_flow_style=False, sort_keys=False)


def _run_git(cwd: str, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_root(path: str) -> Optional[str]:
    root = _run_git(path, "rev-parse", "--show-toplevel")
    return os.path.abspath(root) if root else None


def _git_remote_url(path: str) -> Optional[str]:
    return _run_git(path, "remote", "get-url", "origin")


def _is_git_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git")) or _git_root(path) == os.path.abspath(path)


def discover_git_projects(directory: str) -> List[Dict[str, str]]:
    """Find git repositories in directory itself and its immediate children."""
    directory = os.path.abspath(directory)
    found: Dict[str, Dict[str, str]] = {}

    def add(path: str) -> None:
        if not _is_git_repo(path):
            return
        name = os.path.basename(path.rstrip(os.sep)) or "repo"
        if not _valid_component_name(name):
            return
        entry = {"name": name, "path": path}
        url = _git_remote_url(path)
        if url:
            entry["url"] = url
        found[path] = entry

    add(directory)
    try:
        children = sorted(os.listdir(directory))
    except OSError:
        children = []
    for child in children:
        if child.startswith("."):
            continue
        child_path = os.path.join(directory, child)
        if os.path.isdir(child_path):
            add(child_path)

    return sorted(found.values(), key=lambda item: item["name"])


def resolve_init_context(start: Optional[str] = None) -> Dict[str, Any]:
    """Choose the folder that should become the environment and its projects.

    If start is inside a git repo, the environment config lives in that repo
    (so it can be committed). Sibling git projects in the parent folder are
    still offered for inclusion. Outside a git repo, scan start itself.
    """
    start = os.path.abspath(start or os.getcwd())
    git_root = _git_root(start)
    if git_root:
        parent = os.path.dirname(git_root)
        siblings = [
            project for project in discover_git_projects(parent)
            if project["path"] != parent
        ]
        projects = siblings or discover_git_projects(git_root)
        return {
            "root": git_root,
            "projects": projects,
            "current": git_root,
            "in_git_repo": True,
        }

    return {
        "root": start,
        "projects": discover_git_projects(start),
        "current": None,
        "in_git_repo": False,
    }


def _prompt(message: str, default: Optional[str] = None, input_fn=None) -> str:
    ask = input_fn or input
    suffix = f" [{default}]" if default is not None else ""
    answer = ask(f"{message}{suffix}: ").strip()
    if answer:
        return answer
    return default or ""


def _parse_selection(answer: str, projects: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Parse 'all', indexes, or names into selected projects."""
    answer = answer.strip().lower()
    if not answer or answer in ("all", "*"):
        return list(projects)

    selected: List[Dict[str, str]] = []
    seen = set()
    tokens = [token.strip() for token in answer.replace(" ", ",").split(",") if token.strip()]
    by_name = {project["name"].lower(): project for project in projects}
    for token in tokens:
        project = None
        if token.isdigit():
            index = int(token) - 1
            if 0 <= index < len(projects):
                project = projects[index]
        else:
            project = by_name.get(token)
        if project and project["path"] not in seen:
            selected.append(project)
            seen.add(project["path"])
    return selected


class Workspace:
    """A multi-repo development environment rooted at a single project rig.yml."""

    MANIFEST = "rig.yml"

    def __init__(self, path: Optional[str] = None):
        self.path = os.path.abspath(path or os.getcwd())

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.path, self.MANIFEST)

    def is_workspace(self) -> bool:
        return _read_yaml(self.manifest_path).get("kind") == "workspace"

    def init(
        self,
        name: Optional[str] = None,
        repos: Optional[List[Dict[str, str]]] = None,
        password_vars: Optional[Dict[str, str]] = None,
        agent_type: Optional[str] = None,
    ) -> bool:
        """Idempotently initialize workspace config and register selected repos."""
        workspace_name = name or os.path.basename(self.path.rstrip(os.sep)) or "workspace"
        if not _valid_component_name(workspace_name):
            print(f"Error: invalid workspace name '{workspace_name}'")
            return False

        selected = repos or []
        password_vars = password_vars or {}
        from cli.agents import normalize_agent_type
        chosen_agent = normalize_agent_type(agent_type or "opencode")

        project = ProjectConfig(self.path)
        # Pass agent_type into init so scaffold/config persist before add_repo reloads.
        if not project.init(
            name=workspace_name,
            ansible_enabled=True,
            agent_type=chosen_agent,
        ):
            return False

        project.config["kind"] = "workspace"
        project.config["name"] = workspace_name
        project.config.setdefault("agent", {})
        project.config["agent"]["type"] = chosen_agent
        project.config["agent"]["credentials_var"] = "coding_agent_api_key"
        project.set_vars("coding_agent_type", chosen_agent)

        existing_repos = [
            repo for repo in (project.config.get("repos") or [])
            if isinstance(repo, dict) and repo.get("name")
        ]
        by_name = {repo["name"]: dict(repo) for repo in existing_repos}

        from cli.git import GitManager
        git_manager = GitManager(project)
        # Avoid wiping vaulted vars.yml on re-init when clone list already exists.
        if project.get_vars("git.clone_repos") is None:
            project.set_vars("git.clone_repos", [])
        if project.get_vars("git.ssh_repos") is None:
            project.set_vars("git.ssh_repos", [])

        for repo in selected:
            repo_name = repo["name"]
            if not _valid_component_name(repo_name):
                print(f"Error: invalid repo name '{repo_name}'")
                return False
            url = repo.get("url")
            local_path = repo.get("path")
            relative = None
            if local_path:
                relative = os.path.relpath(local_path, self.path)
            entry: Dict[str, Any] = dict(by_name.get(repo_name) or {"name": repo_name})
            if relative and relative not in (".", ""):
                entry["path"] = relative.replace(os.sep, "/")
            if url:
                from cli.encrypt import to_https_git_url
                # Always record/clone HTTPS so PATs work for clone + gh/glab auth.
                https_url = to_https_git_url(url)
                if https_url != url:
                    print(f"Converted '{repo_name}' remote to HTTPS: {https_url}")
                entry["url"] = https_url
                password_var = password_vars.get(repo_name)
                if password_var:
                    entry["password_var"] = password_var
                existing_urls = {
                    item.get("repo")
                    for item in (project.get_vars("git.clone_repos") or [])
                    if isinstance(item, dict)
                }
                # Match either original SSH or converted HTTPS already on disk.
                already = https_url in existing_urls or url in existing_urls
                dest = remote_workspace_path(repo_name)
                if not already:
                    if not git_manager.add_repo(
                        https_url,
                        dest,
                        is_ssh=False,
                        password_var=password_var,
                    ):
                        return False
                else:
                    # Keep dest under /workspace even for repos registered before
                    # REMOTE_WORKSPACE_ROOT existed (legacy /root/<name>).
                    git_manager.set_repo_dest(https_url, dest) or git_manager.set_repo_dest(url, dest)
                    if password_var:
                        if not git_manager.set_repo_password_var(https_url, password_var):
                            git_manager.set_repo_password_var(url, password_var)
            by_name[repo_name] = entry

        project.config["repos"] = list(by_name.values())

        # Migrate any remaining legacy /root/<name> clone paths.
        # normalize_workspace_dests() reloads rig.yml, so re-apply in-memory
        # fields that are not on disk yet.
        git_manager.normalize_workspace_dests()

        from cli.encrypt import git_providers_from_repos
        providers = git_providers_from_repos(list(by_name.values()))
        project.set_vars("git_providers", providers)
        project.config["repos"] = list(by_name.values())
        project.config["git_providers"] = providers
        # Re-apply after add_repo/load cycles so agent choice is not lost.
        project.config.setdefault("agent", {})
        project.config["agent"]["type"] = chosen_agent
        project.config["agent"]["credentials_var"] = "coding_agent_api_key"
        project.set_vars("coding_agent_type", chosen_agent)

        if not project.save(write_vars=False):
            return False
        # Refresh auto-generated system.yml so provider CLI tasks match repos.
        if not project.write_system_playbook(force=True):
            return False

        print(f"Initialized environment '{workspace_name}' at {self.path}")
        if by_name:
            print("Included projects: " + ", ".join(sorted(by_name)))
        if providers:
            print("Git providers: " + ", ".join(providers))
        print(f"Coding agent: {chosen_agent}")
        return True


def interactive_env_init(
    start: Optional[str] = None,
    input_fn=None,
    secret_fn=None,
    config_path: Optional[str] = None,
    data_key_path: Optional[str] = None,
) -> bool:
    """Interactively initialize (or reinitialize) an environment."""
    from cli.config import RigConfig, ensure_credentials
    from cli.agents import prompt_coding_agent
    from cli.encrypt import (
        DEFAULT_DATA_KEY_PATH,
        collect_repo_pats,
        prompt_and_store_coding_agent_credentials,
        resolve_repo_pat_var_names,
        store_repo_pats,
    )

    try:
        ensure_credentials(
            RigConfig(path=config_path),
            input_fn=input_fn,
            secret_fn=secret_fn,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return False

    context = resolve_init_context(start)
    root = context["root"]
    projects: List[Dict[str, str]] = context["projects"]
    current = context.get("current")
    workspace = Workspace(root)
    already = os.path.exists(workspace.manifest_path)

    print(f"Environment root: {root}")
    if context["in_git_repo"] and current:
        print(f"Current git repo: {current}")
    if already:
        print("Existing rig.yml found — reinitializing and adding any missing files.")

    existing = _read_yaml(workspace.manifest_path)
    default_name = existing.get("name") or os.path.basename(root) or "workspace"

    if not projects:
        print("No git projects found nearby.")
        name = _prompt("Environment name", default_name, input_fn)
        if not name:
            print("Error: environment name is required")
            return False
        selected: List[Dict[str, str]] = []
    else:
        print("Projects found:")
        for index, project in enumerate(projects, start=1):
            marker = " (current)" if current and project["path"] == current else ""
            url = f" — {project['url']}" if project.get("url") else ""
            print(f"  {index}. {project['name']}{marker}{url}")

        default_selection = "all"
        if current:
            current_name = os.path.basename(current)
            if any(project["name"] == current_name for project in projects):
                default_selection = current_name

        selection = _prompt(
            "Projects to include (numbers, names, or all)",
            default_selection,
            input_fn,
        )
        selected = _parse_selection(selection, projects)
        if not selected:
            print("Error: no projects selected")
            return False

        name = _prompt("Environment name", default_name, input_fn)
        if not name:
            print("Error: environment name is required")
            return False

    existing_agent = (existing.get("agent") or {}).get("type") or "opencode"
    try:
        agent_type = prompt_coding_agent(default=existing_agent, input_fn=input_fn)
    except ValueError as exc:
        print(f"Error: {exc}")
        return False

    # Collect PATs before writing config so password_var can be attached on add.
    pat_assignments, default_pats = collect_repo_pats(
        selected,
        input_fn=input_fn,
        secret_fn=secret_fn,
    )
    password_vars, pats_to_store = resolve_repo_pat_var_names(
        pat_assignments,
        selected,
        default_pats=default_pats,
    )

    if not workspace.init(
        name=name,
        repos=selected,
        password_vars=password_vars,
        agent_type=agent_type,
    ):
        return False

    key_path = data_key_path or DEFAULT_DATA_KEY_PATH
    try:
        store_repo_pats(
            pats_to_store,
            project_dir=root,
            data_key_path=key_path,
            input_fn=input_fn,
            secret_fn=secret_fn,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Warning: could not store git PATs: {exc}")
    stored_agent_key = False
    try:
        stored_agent_key = bool(
            prompt_and_store_coding_agent_credentials(
                project_dir=root,
                data_key_path=key_path,
                input_fn=input_fn,
                secret_fn=secret_fn,
                agent_type=agent_type,
            )
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Warning: could not store coding_agent_api_key: {exc}")

    try:
        from cli.encrypt import (
            VSCODE_BROWSER_PASSWORD_VAR,
            ensure_vscode_browser_password,
        )
        if ensure_vscode_browser_password(
            project_dir=root,
            data_key_path=key_path,
        ):
            print(
                f"Generated '{VSCODE_BROWSER_PASSWORD_VAR}' for "
                "code-server browser login "
                f"(decrypt with: rig vars decrypt {VSCODE_BROWSER_PASSWORD_VAR})"
            )
    except (ValueError, RuntimeError) as exc:
        print(f"Warning: could not store vscode_browser_password: {exc}")

    # OpenCode: free Zen models by default; switch to keyed config when a key exists.
    try:
        from cli.agents import normalize_agent_type, sync_opencode_config
        if normalize_agent_type(agent_type) == "opencode":
            written = sync_opencode_config(
                os.path.join(root, ".rig"),
                has_api_key=stored_agent_key,
                force=stored_agent_key,
            )
            if stored_agent_key and written:
                print("OpenCode config set to use coding_agent_api_key")
            elif not stored_agent_key:
                print(
                    "OpenCode config uses free models "
                    "(set coding_agent_api_key to use a paid provider)"
                )
    except ValueError:
        pass
    return True


# =============================================================================
# ANSIBLE INTEGRATION CLASS
# =============================================================================

class AnsibleManager:
    """Manages Ansible playbook execution for project bootstrap"""

    def __init__(self, project_config: Optional["ProjectConfig"] = None):
        self.project_config: Optional["ProjectConfig"] = project_config
        self.playbook_path: Optional[str] = None
        self.workspace_playbook_path: Optional[str] = None
        self.finalizers_playbook_path: Optional[str] = None
        self.inventory_path: Optional[str] = None
        self.vault_password: Optional[str] = None
        self.extra_vars: Dict[str, Any] = {}
        self.rig_vars: Dict[str, Any] = {}
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

            # Playbook paths come from root rig.yml (system / workspace / finalizers).
            project_dir = self.project_config.project_dir
            playbooks = self.project_config.config.get("playbooks") or {}
            system_rel = playbooks.get("system") or f".rig/{ProjectConfig.SYSTEM_YAML}"
            workspace_rel = playbooks.get("workspace") or f".rig/{ProjectConfig.WORKSPACE_YAML}"
            finalizers_rel = playbooks.get("finalizers") or f".rig/{ProjectConfig.FINALIZERS_YAML}"
            inventory_rel = (
                self.project_config.config.get("inventory")
                or f".rig/inventory.yml"
            )

            def _resolve(rel: str) -> str:
                return rel if os.path.isabs(rel) else os.path.join(project_dir, rel)

            self.playbook_path = _resolve(system_rel)
            self.workspace_playbook_path = _resolve(workspace_rel)
            self.finalizers_playbook_path = _resolve(finalizers_rel)
            self.inventory_path = _resolve(inventory_rel)
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

            # Keep inventory connection-only. Vaulted secrets live in vars.yml and
            # are passed via ``ansible-playbook -e @vars.yml`` (Ansible !vault tags).
            with open(self.inventory_path, 'w', encoding='utf-8') as handle:
                yaml.dump(inventory, handle, Dumper=_VarsDumper, default_flow_style=False, sort_keys=False)

            print(f"✓ Inventory created at {self.inventory_path}")
            return True
        except Exception as e:
            print(f"Error creating inventory: {e}")
            return False

    def setup_requirements(self) -> bool:
        """Create requirements.yml for Ansible Galaxy collections/roles."""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            requirements_path = os.path.join(
                self.project_config.rig_dir, ProjectConfig.REQUIREMENTS_YML
            )
            legacy_txt = os.path.join(self.project_config.rig_dir, "requirements.txt")

            if os.path.exists(requirements_path):
                return True

            collections = []
            roles = []
            requirements = self.project_config.requirements or [
                "community.general",
                "ansible.posix",
                "geerlingguy.docker",
                "geerlingguy.python",
            ]
            for req in requirements:
                name = req.lstrip("- ").strip()
                if not name or name.startswith("#"):
                    continue
                # Galaxy collections use a dotted namespace; classic roles are typically author.name
                parts = name.split(".")
                if len(parts) == 2 and parts[0] in {"community", "ansible", "google", "amazon", "azure", "cisco", "fortinet", "hetzner", "openstack", "ovirt", "purestorage", "sensu", "theforeman", "vyos"}:
                    collections.append({"name": name})
                elif name.startswith("geerlingguy.") or (len(parts) == 2 and parts[0] not in {"community", "ansible"}):
                    roles.append({"name": name})
                else:
                    collections.append({"name": name})

            payload: Dict[str, Any] = {}
            if collections:
                payload["collections"] = collections
            if roles:
                payload["roles"] = roles
            with open(requirements_path, "w", encoding="utf-8") as handle:
                yaml.dump(payload, handle, default_flow_style=False, sort_keys=False)

            if os.path.exists(legacy_txt):
                try:
                    os.remove(legacy_txt)
                except OSError:
                    pass

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

            log.debug("Roles directory set up at %s", roles_dir)
            return True
        except Exception as e:
            log.error("Error setting up roles: %s", e)
            return False

    def setup_playbook(self, custom_tasks: Optional[list] = None) -> bool:
        """Ensure system/workspace playbooks exist; optional custom tasks become workspace.yml."""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False

            # system.yml is auto-generated — refresh so playbook fixes ship on apply/create.
            if not self.project_config.write_system_playbook(force=True):
                return False

            playbooks = self.project_config.config.get("playbooks") or {}
            system_rel = playbooks.get("system") or f".rig/{ProjectConfig.SYSTEM_YAML}"
            workspace_rel = playbooks.get("workspace") or f".rig/{ProjectConfig.WORKSPACE_YAML}"
            project_dir = self.project_config.project_dir
            system_path = (
                system_rel if os.path.isabs(system_rel)
                else os.path.join(project_dir, system_rel)
            )
            workspace_path = (
                workspace_rel if os.path.isabs(workspace_rel)
                else os.path.join(project_dir, workspace_rel)
            )

            if custom_tasks:
                playbook = [{
                    'name': 'Configure workspace',
                    'hosts': 'remote_vm',
                    'gather_facts': False,
                    'remote_user': 'root',
                    'tasks': custom_tasks,
                }]
                with open(workspace_path, 'w', encoding='utf-8') as handle:
                    yaml.dump(playbook, handle, default_flow_style=False, sort_keys=False)
                # One-off / test runs execute the workspace playbook directly.
                self.playbook_path = workspace_path
                self.workspace_playbook_path = workspace_path
            else:
                if not self.project_config.write_workspace_playbook(force=False):
                    return False
                self.playbook_path = system_path
                self.workspace_playbook_path = workspace_path

            print(f"✓ System playbook: {system_path}")
            print(f"✓ Workspace playbook: {workspace_path}")
            return True
        except Exception as e:
            print(f"Error creating playbook: {e}")
            return False

    def install_roles(self, requirements_path: Optional[str] = None) -> bool:
        """Install Ansible Galaxy collections/roles from requirements.yml."""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            requirements_path = requirements_path or os.path.join(
                self.project_config.rig_dir, ProjectConfig.REQUIREMENTS_YML
            )

            if not os.path.exists(requirements_path):
                print(f"Warning: Requirements file not found: {requirements_path}")
                return False

            roles_path = os.path.join(self.project_config.rig_dir, 'roles')
            collections_path = os.path.join(self.project_config.rig_dir, 'collections')
            os.makedirs(roles_path, exist_ok=True)
            os.makedirs(collections_path, exist_ok=True)
            self.ansible_env['ANSIBLE_ROLES_PATH'] = roles_path
            self.ansible_env['ANSIBLE_COLLECTIONS_PATH'] = collections_path

            # Install collections (and roles if listed) from the requirements file.
            result = subprocess.run([
                'ansible-galaxy', 'collection', 'install',
                '-r', requirements_path,
                '-p', collections_path,
            ], env=self.ansible_env, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                # Older ansible or roles-only file — fall back to generic install.
                result = subprocess.run([
                    'ansible-galaxy', 'install',
                    '-r', requirements_path,
                    '-p', roles_path,
                ], env=self.ansible_env, capture_output=True, text=True, timeout=300)

            # Also install any classic roles section explicitly.
            role_result = subprocess.run([
                'ansible-galaxy', 'role', 'install',
                '-r', requirements_path,
                '-p', roles_path,
            ], env=self.ansible_env, capture_output=True, text=True, timeout=300)

            if result.returncode == 0 or role_result.returncode == 0:
                log.debug("Ansible Galaxy requirements installed")
                return True

            detail = (result.stderr or role_result.stderr or result.stdout or "").strip()
            log.warning("Galaxy install issue: %s", detail)
            # Playbooks are mostly inline; do not hard-fail provisioning.
            return True
        except Exception as e:
            log.warning("Error installing roles: %s", e)
            return True

    def _redact_secrets(self, text: str) -> str:
        redacted = text or ''
        for value in self.extra_vars.values():
            if isinstance(value, str) and len(value) >= 8:
                redacted = redacted.replace(value, '***')
        return redacted

    def run_playbook(
        self,
        extra_vars: Optional[Dict[str, Any]] = None,
        vault_password: Optional[str] = None,
        stream: bool = False,
    ) -> bool:
        """Run the configured playbook path (system by default, or workspace for custom tasks)."""
        try:
            if not self.playbook_path or not self.inventory_path:
                log.error("Playbook or inventory not configured")
                return False
            return self._run_one_playbook(
                self.playbook_path,
                extra_vars=extra_vars,
                vault_password=vault_password,
                stream=stream,
            )
        except Exception as e:
            print(f"Error running playbook: {e}")
            return False

    def run_all_playbooks(
        self,
        extra_vars: Optional[Dict[str, Any]] = None,
        vault_password: Optional[str] = None,
        stream: bool = True,
    ) -> bool:
        """Run system.yml then workspace.yml when both are configured."""
        paths = []
        for path in (self.playbook_path, self.workspace_playbook_path):
            if path and os.path.exists(path) and path not in paths:
                paths.append(path)
        if not paths:
            log.error("No playbooks configured")
            return False
        for index, path in enumerate(paths, start=1):
            log.info("[%s/%s] ansible-playbook %s", index, len(paths), path)
            if not self._run_one_playbook(
                path,
                extra_vars=extra_vars,
                vault_password=vault_password,
                stream=stream,
            ):
                return False
        return True

    def _run_one_playbook(
        self,
        playbook_path: str,
        extra_vars: Optional[Dict[str, Any]] = None,
        vault_password: Optional[str] = None,
        stream: bool = False,
    ) -> bool:
        import shlex

        if not self.inventory_path:
            log.error("Inventory not configured")
            return False

        cmd = [
            'ansible-playbook', playbook_path,
            '-i', self.inventory_path,
            '--ssh-common-args',
            '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes'
        ]

        if self.project_config is None:
            log.error("project_config is not set")
            return False

        # Prefer vault-aware vars.yml over baking secrets into inventory.yml.
        vars_path = os.path.join(self.project_config.rig_dir, ProjectConfig.VARS_YAML)
        if os.path.exists(vars_path):
            cmd.extend(['-e', f'@{vars_path}'])

        # Runtime rig_* context (and caller extras) after vars.yml so they win.
        if extra_vars:
            self.extra_vars.update(extra_vars)
        runtime_vars: Dict[str, Any] = {}
        if self.rig_vars:
            runtime_vars.update(self.rig_vars)
        if self.extra_vars:
            runtime_vars.update(self.extra_vars)
        if runtime_vars:
            cmd.extend(['-e', json.dumps(runtime_vars, default=str)])

        from cli.encrypt import DEFAULT_DATA_KEY_PATH
        if os.path.exists(DEFAULT_DATA_KEY_PATH):
            cmd.extend(['--vault-password-file', DEFAULT_DATA_KEY_PATH])
        elif vault_password:
            cmd.extend(['--vault-password', vault_password])
        elif self.vault_password:
            cmd.extend(['--vault-password', self.vault_password])

        roles_path = os.path.join(self.project_config.rig_dir, 'roles')
        collections_path = os.path.join(self.project_config.rig_dir, 'collections')
        self.ansible_env['ANSIBLE_ROLES_PATH'] = roles_path
        self.ansible_env['ANSIBLE_COLLECTIONS_PATH'] = collections_path

        log.debug("Inventory: %s", self.inventory_path)
        if os.path.exists(vars_path):
            log.debug("Extra vars: @%s", vars_path)
        log.debug("$ %s", " ".join(shlex.quote(part) for part in cmd))

        def _output_indicates_no_hosts(text: str) -> bool:
            lowered = (text or "").lower()
            return (
                "skipping: no hosts matched" in lowered
                or "could not match supplied host pattern" in lowered
                or "unable to parse" in lowered and "inventory" in lowered
            )

        if stream:
            process = subprocess.Popen(
                cmd,
                env=self.ansible_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            collected: List[str] = []
            assert process.stdout is not None
            for line in process.stdout:
                # Live ansible-playbook output stays on stdout.
                print(line, end="")
                collected.append(line)
            returncode = process.wait(timeout=1800)
            output = "".join(collected)
            if returncode != 0:
                log.error("Playbook failed (%s): %s", returncode, playbook_path)
                return False
            if _output_indicates_no_hosts(output):
                log.error(
                    "Playbook matched no hosts (inventory/vars issue): %s",
                    playbook_path,
                )
                return False
            log.info("Playbook succeeded: %s", playbook_path)
            return True

        result = subprocess.run(cmd, env=self.ansible_env, capture_output=True, text=True, timeout=600)
        combined = f"{result.stdout or ''}\n{result.stderr or ''}"
        if result.returncode == 0 and not _output_indicates_no_hosts(combined):
            log.info("Playbook succeeded: %s", playbook_path)
            return True

        log.error("Playbook failed (return code %s): %s", result.returncode, playbook_path)
        if _output_indicates_no_hosts(combined):
            log.error("No hosts matched — check inventory.yml parses and lists remote_vm")
        if result.stderr:
            log.debug("STDERR:\n%s", self._redact_secrets(result.stderr))
        if result.stdout:
            log.debug("STDOUT:\n%s", self._redact_secrets(result.stdout))
        return False

    def setup_bootstrap(self, vm_ip: str, ssh_key_path: str) -> bool:
        """Setup complete bootstrap environment"""
        try:
            log.debug("Setting up Ansible bootstrap for %s", vm_ip)

            # Setup all components
            self.setup_inventory(vm_ip, ssh_key_path)
            self.setup_requirements()
            self.setup_roles()
            self.setup_playbook()

            # Install roles if requirements exist
            if self.project_config is not None:
                requirements_path = os.path.join(
                    self.project_config.rig_dir, ProjectConfig.REQUIREMENTS_YML
                )
                if os.path.exists(requirements_path):
                    self.install_roles(requirements_path)

            return True
        except Exception as e:
            log.error("Error setting up bootstrap: %s", e)
            return False


if __name__ == '__main__':
    print("Use the CLI interface: python3 -m cli.remote_dev_cli")
