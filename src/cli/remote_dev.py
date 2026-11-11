"""
Remote Development Environment Module
"""
import json
import os
import re
import sys
import uuid
import time
import shutil
import shlex
import subprocess
import socket
import webbrowser
from typing import Optional, Dict, Any, List, Sequence, Tuple
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

# Local editor CLIs for ``rig dev`` (Remote-SSH). ``editor.enabled`` is the
# chosen set; ``editor.default`` is the one ``rig dev`` opens. ``--editor``
# picks another enabled one.
SUPPORTED_EDITORS = ("code", "code-insiders", "cursor", "codium")
EDITOR_LABELS = {
    "code": "VS Code",
    "code-insiders": "VS Code Insiders",
    "cursor": "Cursor",
    "codium": "VSCodium",
}
DEFAULT_EDITOR = "code"


def normalize_editor_type(value: Optional[str]) -> str:
    """Normalize a rig.yml / CLI editor preference."""
    raw = (value or "").strip().lower().replace(" ", "-").replace("_", "-")
    aliases = {
        "code": "code",
        "vscode": "code",
        "vs-code": "code",
        "code-insiders": "code-insiders",
        "insiders": "code-insiders",
        "cursor": "cursor",
        "codium": "codium",
        "vscodium": "codium",
    }
    editor = aliases.get(raw, raw)
    if editor not in SUPPORTED_EDITORS:
        raise ValueError(
            f"Unsupported editor {value!r}. "
            f"Choose one of: {', '.join(SUPPORTED_EDITORS)}"
        )
    return editor


def prompt_editor(
    default: str = DEFAULT_EDITOR,
    input_fn=None,
) -> str:
    """Ask which local editor CLI to prefer for ``rig dev`` (single choice)."""
    ask = input_fn or input
    try:
        default = normalize_editor_type(default)
    except ValueError:
        default = DEFAULT_EDITOR
    choices = ", ".join(f"{e} ({EDITOR_LABELS[e]})" for e in SUPPORTED_EDITORS)
    answer = ask(f"Editor [{choices}] [{default}]: ").strip()
    if not answer:
        return default
    return normalize_editor_type(answer)


def coerce_editor_types(value: Any) -> List[str]:
    """
    Normalize a string, comma-separated string, or list into editor ids.

    Order is preserved and duplicates are dropped. Empty input returns [].
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parts = [part.strip() for part in text.replace(" ", ",").split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                parts.append(text)
    else:
        text = str(value).strip()
        parts = [text] if text else []

    selected: List[str] = []
    seen = set()
    for part in parts:
        editor = normalize_editor_type(part)
        if editor not in seen:
            selected.append(editor)
            seen.add(editor)
    return selected


def _editor_block(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("editor")
    if isinstance(raw, dict):
        return raw
    return {}


def editor_default_field(config: Optional[Dict[str, Any]] = None) -> Any:
    """``editor.default``, or a legacy ``default_editor`` / ``editor`` string."""
    config = config or {}
    raw = _editor_block(config).get("default")
    if raw not in (None, ""):
        return raw
    raw = config.get("default_editor")
    if raw not in (None, ""):
        return raw
    legacy = config.get("editor")
    if isinstance(legacy, str) and legacy.strip():
        return legacy
    return None


def editor_enabled_field(config: Optional[Dict[str, Any]] = None) -> Any:
    """``editor.enabled``, or the legacy top-level ``editors`` list."""
    config = config or {}
    enabled = _editor_block(config).get("enabled")
    if enabled:
        return enabled
    return config.get("editors")


def resolve_configured_editors(
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], Optional[str]]:
    """
    Return ``(enabled_editors, default_editor)``.

    ``editor.enabled`` is the chosen set. ``editor.default`` is the ``rig dev``
    default when it is a member of that set. Legacy ``editors``,
    ``default_editor``, and a string ``editor`` are still accepted. An empty
    config returns ``([], None)`` so ``rig dev`` can auto-detect a local binary.
    """
    config = config or {}
    editors: List[str] = []
    raw_list = editor_enabled_field(config)
    if raw_list:
        try:
            editors = coerce_editor_types(raw_list)
        except ValueError:
            editors = []
    if not editors:
        raw_editor = editor_default_field(config)
        if raw_editor:
            try:
                editors = coerce_editor_types(raw_editor)
            except ValueError:
                editors = []
    if not editors:
        return [], None

    primary: Optional[str] = None
    raw_primary = editor_default_field(config)
    if isinstance(raw_primary, str) and raw_primary.strip():
        try:
            primary = normalize_editor_type(raw_primary)
        except ValueError:
            primary = None
    if primary not in editors:
        primary = editors[0]
    return editors, primary


def apply_editor_selection(
    config: Dict[str, Any],
    editors: Any,
    default: Any = None,
) -> List[str]:
    """
    Write ``editor.default`` and ``editor.enabled`` onto a workspace config dict.

    ``editor.default`` is the ``rig dev`` default. The requested name is used
    when that editor was selected, otherwise the first selected editor.
    """
    try:
        selected = coerce_editor_types(editors) or [DEFAULT_EDITOR]
    except ValueError:
        selected = [DEFAULT_EDITOR]
    primary: Optional[str] = None
    if isinstance(default, str) and default.strip():
        try:
            primary = normalize_editor_type(default)
        except ValueError:
            primary = None
    if primary not in selected:
        primary = selected[0]
    block = config.get("editor")
    if not isinstance(block, dict):
        block = {}
    block["default"] = primary
    block["enabled"] = list(selected)
    config["editor"] = block
    config.pop("default_editor", None)
    config.pop("editors", None)
    return selected


def parse_editor_selection(
    answer: str,
    *,
    default: Sequence[str],
) -> List[str]:
    """Parse 'all', indexes, or names the same way git project selection works."""
    try:
        defaults = coerce_editor_types(list(default)) or [DEFAULT_EDITOR]
    except ValueError:
        defaults = [DEFAULT_EDITOR]
    raw = (answer or "").strip()
    if not raw:
        return defaults
    if raw.lower() in ("all", "*"):
        return list(SUPPORTED_EDITORS)

    selected: List[str] = []
    seen = set()
    tokens = [token.strip() for token in raw.replace(" ", ",").split(",") if token.strip()]
    if not tokens:
        return defaults
    for token in tokens:
        if token.isdigit():
            index = int(token) - 1
            if not 0 <= index < len(SUPPORTED_EDITORS):
                raise ValueError(
                    f"Editor number {token} is out of range. "
                    f"Choose 1-{len(SUPPORTED_EDITORS)}, names, or all."
                )
            editor = SUPPORTED_EDITORS[index]
        else:
            editor = normalize_editor_type(token)
        if editor not in seen:
            selected.append(editor)
            seen.add(editor)
    return selected


def prompt_editors(
    default: Any = DEFAULT_EDITOR,
    input_fn=None,
) -> List[str]:
    """
    Ask which local editors to enable for ``rig dev``.

    Accepts numbers, names, or ``all``, like the git project picker.
    Enter keeps ``default``.
    """
    ask = input_fn or input
    try:
        defaults = coerce_editor_types(default) or [DEFAULT_EDITOR]
    except ValueError:
        defaults = [DEFAULT_EDITOR]
    print("Editors:")
    for index, editor in enumerate(SUPPORTED_EDITORS, start=1):
        print(f"  {index}. {editor} ({EDITOR_LABELS[editor]})")
    default_label = ",".join(defaults)
    answer = ask(
        "Editors to include (numbers, names, or all) "
        f"[{default_label}]: "
    )
    return parse_editor_selection(answer, default=defaults)


def prompt_default_editor(
    selected: Sequence[str],
    default: Any = None,
    input_fn=None,
) -> str:
    """
    Ask which selected editor ``rig dev`` should open.

    A single selected editor is returned without a prompt. Numbers match the
    catalog shown by ``prompt_editors`` (not a renumbered sub-list).
    """
    try:
        editors = coerce_editor_types(list(selected)) or [DEFAULT_EDITOR]
    except ValueError:
        editors = [DEFAULT_EDITOR]
    if len(editors) == 1:
        return editors[0]
    preferred = editors[0]
    if isinstance(default, str) and default.strip():
        try:
            candidate = normalize_editor_type(default)
        except ValueError:
            candidate = None
        if candidate in editors:
            preferred = candidate
    by_number = {
        str(index): editor
        for index, editor in enumerate(SUPPORTED_EDITORS, start=1)
        if editor in editors
    }
    print("Default editor for rig dev:")
    for number, editor in by_number.items():
        print(f"  {number}. {editor} ({EDITOR_LABELS[editor]})")
    ask = input_fn or input
    answer = ask(f"Default editor [{preferred}]: ").strip()
    if not answer:
        return preferred
    if answer.isdigit():
        numbered = by_number.get(answer)
        if not numbered:
            raise ValueError(
                f"Default editor number {answer} is not one of the selected editors "
                f"({', '.join(editors)})"
            )
        return numbered
    chosen = normalize_editor_type(answer)
    if chosen not in editors:
        raise ValueError(
            f"Default editor {chosen!r} was not selected. "
            f"Choose one of: {', '.join(editors)}"
        )
    return chosen


def configured_editors(project) -> Tuple[List[str], Optional[str]]:
    """Enabled local editors and the default ``rig dev`` target."""
    if project is None or not getattr(project, "config", None):
        return [], None
    return resolve_configured_editors(project.config)


def project_editor_preference(project_dir: Optional[str] = None) -> Optional[str]:
    """Return the default editor from ``rig.yml``, else None (auto-detect)."""
    project = ProjectConfig(os.path.abspath(project_dir or os.getcwd()))
    if not project.load() or not project.config:
        return None
    _editors, default = resolve_configured_editors(project.config)
    if default:
        return default
    raw = editor_default_field(project.config)
    if raw not in (None, ""):
        log.warning("Ignoring invalid editor %r in rig.yml", raw)
    return None

# Paths written under the project by Galaxy / create that should not be committed.
# ``local_roles`` is the committed role directory and is intentionally absent.
GITIGNORE_MARKER_BEGIN = "# >>> rig managed"
GITIGNORE_MARKER_END = "# <<< rig managed"
GITIGNORE_ENTRIES = (
    ".rig/collections/",
    ".rig/roles/",
    ".rig/inventory.yml",
    ".rig/*.retry",
)
# Galaxy installs into ``roles``. ``local_roles`` is committed and searched first.
LOCAL_ROLES_DIRNAME = "local_roles"
ROLES_DIRNAME = "roles"
# Previous Galaxy install directory. Contents are moved into ``roles``.
LEGACY_GALAXY_ROLES_DIRNAME = "galaxy_roles"


def ssh_option_log_level() -> str:
    """OpenSSH log level. ERROR hides the known-hosts notice; ``-v`` shows it."""
    if log.verbosity() >= 1:
        return "INFO"
    return "ERROR"


def roles_search_path(rig_dir: str) -> str:
    """Ansible role search path: committed roles, then Galaxy installs."""
    return os.pathsep.join(
        os.path.join(rig_dir, name)
        for name in (LOCAL_ROLES_DIRNAME, ROLES_DIRNAME)
    )


def _local_role_link_target(name: str) -> str:
    return os.path.join("..", LOCAL_ROLES_DIRNAME, name)


def ensure_local_roles_dir(rig_dir: str) -> bool:
    """Create ``.rig/local_roles`` and a ``.gitkeep`` when that file is missing."""
    path = os.path.join(rig_dir, LOCAL_ROLES_DIRNAME)
    os.makedirs(path, exist_ok=True)
    keep = os.path.join(path, ".gitkeep")
    if os.path.exists(keep):
        return False
    with open(keep, "a", encoding="utf-8"):
        pass
    return True


def migrate_galaxy_roles_dir(rig_dir: str) -> List[str]:
    """Move installs from the old ``.rig/galaxy_roles`` directory into ``.rig/roles``."""
    legacy = os.path.join(rig_dir, LEGACY_GALAXY_ROLES_DIRNAME)
    dest_root = os.path.join(rig_dir, ROLES_DIRNAME)
    if not os.path.isdir(legacy):
        return []
    os.makedirs(dest_root, exist_ok=True)
    moved: List[str] = []
    for name in sorted(os.listdir(legacy)):
        src = os.path.join(legacy, name)
        if os.path.islink(src) or not os.path.isdir(src):
            continue
        dest = os.path.join(dest_root, name)
        if os.path.lexists(dest):
            continue
        shutil.move(src, dest)
        moved.append(name)
    return moved


def unlink_local_role_shadows(rig_dir: str) -> None:
    """Remove local-role symlinks so Galaxy does not write through them."""
    roles_root = os.path.join(rig_dir, ROLES_DIRNAME)
    if not os.path.isdir(roles_root):
        return
    for name in os.listdir(roles_root):
        dest = os.path.join(roles_root, name)
        if os.path.islink(dest) and os.readlink(dest) == _local_role_link_target(name):
            os.remove(dest)


def link_local_roles(rig_dir: str) -> List[str]:
    """Symlink committed roles into ``.rig/roles`` so they win Ansible's first lookup.

    Ansible searches ``<playbook_dir>/roles`` before ``ANSIBLE_ROLES_PATH``.
    Playbooks live in ``.rig`` and Galaxy installs into that same directory, so
    each committed role is exposed there as a symlink. A Galaxy install with
    the same name is replaced. A hand-written directory is left in place.
    """
    local_root = os.path.join(rig_dir, LOCAL_ROLES_DIRNAME)
    roles_root = os.path.join(rig_dir, ROLES_DIRNAME)
    if not os.path.isdir(local_root):
        return []
    os.makedirs(roles_root, exist_ok=True)
    linked: List[str] = []
    for name in sorted(os.listdir(local_root)):
        if name.startswith("."):
            continue
        src = os.path.join(local_root, name)
        if not os.path.isdir(src):
            continue
        dest = os.path.join(roles_root, name)
        target = _local_role_link_target(name)
        if os.path.islink(dest):
            if os.readlink(dest) == target:
                continue
            os.remove(dest)
        elif os.path.isdir(dest):
            marker = os.path.join(dest, "meta", ".galaxy_install_info")
            if not os.path.isfile(marker):
                continue
            shutil.rmtree(dest)
        elif os.path.lexists(dest):
            continue
        os.symlink(target, dest)
        linked.append(name)
    return linked


def ensure_project_gitignore(project_dir: str) -> List[str]:
    """
    Ensure ``.gitignore`` ignores Galaxy installs and generated inventory.

    Idempotent: creates ``.gitignore`` when missing, or appends/updates a
    marked block so re-running ``rig init`` fills any missing entries.
    Returns the paths that were newly added.
    """
    path = os.path.join(os.path.abspath(project_dir), ".gitignore")
    existing = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read()

    covered = {
        line.strip()
        for line in existing.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    begin = GITIGNORE_MARKER_BEGIN
    end = GITIGNORE_MARKER_END

    if begin in existing and end in existing:
        before, rest = existing.split(begin, 1)
        block, after = rest.split(end, 1)
        block_entries = [
            line.strip()
            for line in block.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        missing = [entry for entry in GITIGNORE_ENTRIES if entry not in block_entries]
        if not missing:
            return []
        # Keep any custom lines already inside the managed block.
        extras = [entry for entry in block_entries if entry not in GITIGNORE_ENTRIES]
        managed = list(GITIGNORE_ENTRIES) + extras
        new_block = "\n".join([begin, *managed, end])
        updated = before.rstrip("\n") + "\n\n" + new_block + "\n" + after.lstrip("\n")
        if not updated.endswith("\n"):
            updated += "\n"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(updated)
        return missing

    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in covered]
    if not missing:
        return []

    block = "\n".join([begin, *missing, end]) + "\n"
    content = (existing.rstrip("\n") + "\n\n" + block) if existing.strip() else block
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return missing


# ``host.provider`` is the cloud. ``host.type`` is the kind of machine.
# ``host.server_type`` is the Hetzner size.
HOST_KIND_VM = "vm"
HOST_KINDS = frozenset({HOST_KIND_VM})
DEFAULT_SERVER_TYPE = "cax11"
CLOUD_PROVIDER_HETZNER = "hetzner"
# Recognized names. Only Hetzner can provision today.
RECOGNIZED_CLOUD_PROVIDERS = frozenset({CLOUD_PROVIDER_HETZNER, "aws", "gcp"})
IMPLEMENTED_CLOUD_PROVIDERS = frozenset({CLOUD_PROVIDER_HETZNER})
CLOUD_PROVIDER_ALIASES = {
    "hetzner": CLOUD_PROVIDER_HETZNER,
    "hetzner-cloud": CLOUD_PROVIDER_HETZNER,
    "hcloud": CLOUD_PROVIDER_HETZNER,
    "aws": "aws",
    "amazon": "aws",
    "gcp": "gcp",
    "google": "gcp",
}


def normalize_cloud_provider(value: Optional[str]) -> str:
    """Normalize a cloud provider name. Empty means Hetzner."""
    raw = (value or "").strip().lower().replace(" ", "-").replace("_", "-")
    if not raw:
        return CLOUD_PROVIDER_HETZNER
    provider = CLOUD_PROVIDER_ALIASES.get(raw, raw)
    if provider not in RECOGNIZED_CLOUD_PROVIDERS:
        known = ", ".join(sorted(RECOGNIZED_CLOUD_PROVIDERS))
        raise ValueError(
            f"Unsupported cloud provider {value!r}. Choose one of: {known}"
        )
    return provider


def host_block(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Normalized ``host`` settings.

    ``provider`` defaults to ``hetzner`` and ``type`` defaults to ``vm``.
    A legacy ``vm`` block, or a ``type`` that is really a Hetzner server size,
    is accepted. ``region`` is read as ``availability_zone``.
    """
    config = config or {}
    raw = config.get("host")
    source = raw if isinstance(raw, dict) else config.get("vm")
    block = dict(source) if isinstance(source, dict) else {}
    kind = block.get("type")
    kind_name = kind.strip().lower() if isinstance(kind, str) else ""
    if kind_name in HOST_KINDS:
        block["type"] = kind_name
    elif kind not in (None, ""):
        if "server_type" not in block:
            block["server_type"] = kind
        block["type"] = HOST_KIND_VM
    else:
        block["type"] = HOST_KIND_VM
    block["provider"] = normalize_cloud_provider(
        block.get("provider") if isinstance(block.get("provider"), str) else None
    )
    if not block.get("server_type"):
        block["server_type"] = DEFAULT_SERVER_TYPE
    if not block.get("availability_zone"):
        region = block.pop("region", None)
        block["availability_zone"] = region or "hel1"
    else:
        block.pop("region", None)
    if not block.get("image"):
        block["image"] = "ubuntu-24.04"
    return block


def configured_agents(project) -> Tuple[List[str], str]:
    """Installed coding agents and the default attach target for a project."""
    from cli.agents import resolve_configured_agents

    if project is None:
        return resolve_configured_agents({})
    return resolve_configured_agents(
        (project.config or {}).get("agent") or {},
        coding_agent_type=project.get_vars("coding_agent_type"),
        coding_agent_types=project.get_vars("coding_agent_types"),
    )


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
    host_cfg = host_block(cfg)
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
    installed_agents, default_agent = configured_agents(project)

    vars_out: Dict[str, Any] = {
        "rig_env_name": env.name if env else "",
        "rig_workspace_name": workspace_name,
        "rig_cloud_provider": host_cfg.get("provider") or CLOUD_PROVIDER_HETZNER,
        "rig_vm_name": (env.vm_name if env else None) or (env.name if env else ""),
        "rig_vm_type": (
            (env.vm_type if env else None)
            or host_cfg.get("server_type")
            or DEFAULT_SERVER_TYPE
        ),
        "rig_availability_zone": (
            (env.region if env else None)
            or host_cfg.get("availability_zone")
            or "hel1"
        ),
        "rig_vm_image": str(
            (env.image if env else None) or host_cfg.get("image") or "ubuntu-24.04"
        ),
        "rig_vm_ip": (env.vm_ip if env else None) or "",
        "rig_vm_id": str(env.vm_id) if env and env.vm_id is not None else "",
        "rig_agent_type": default_agent,
        "rig_agent_types": list(installed_agents),
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


def normalize_firewall_mode_name(raw: Any) -> Optional[str]:
    """Normalize a mode string. ``None`` when the value is empty."""
    if raw is None:
        return None
    mode = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if not mode:
        return None
    aliases = {
        "none": "open",
        "off": "open",
        "unrestricted": "open",
        "replace": "dynamic_replace",
        "dynamic": "dynamic_add",
        "add": "dynamic_add",
    }
    return aliases.get(mode, mode)


def resolve_firewall_mode(firewall_cfg: Optional[Dict[str, Any]] = None) -> str:
    """
    Resolve ``firewall.mode``.

    Modes: ``open`` | ``static`` | ``dynamic_replace`` | ``dynamic_add`` (default).

    Legacy ``lock_to_client_ip`` maps to ``dynamic_add`` (true) or ``open`` (false)
    when ``mode`` is unset.
    """
    cfg = firewall_cfg or {}
    raw = cfg.get("mode")
    if raw is not None and str(raw).strip():
        mode = normalize_firewall_mode_name(raw)
        if mode not in FIREWALL_MODES:
            log.warning(
                "Unknown firewall.mode %r; using %s",
                raw,
                DEFAULT_FIREWALL_MODE,
            )
            return DEFAULT_FIREWALL_MODE
        return mode  # type: ignore[return-value]
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
    open_fallback: bool = True,
) -> List[str]:
    """
    Build inbound ``source_ips`` for the env firewall.

    - ``open``: world access
    - ``static``: ``allowed_ips`` only (open fallback if empty)
    - ``dynamic_replace``: current client IP + ``allowed_ips`` (replaces previous)
    - ``dynamic_add`` (default): keep ``existing_ips``, add client IP + ``allowed_ips``

    ``open_fallback`` is false for a port that explicitly sets ``allowed_ips: []``
    under ``static``, so that port stays closed instead of opening to the world.
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
        resolved = _dedupe_cidrs(extras)
        if resolved:
            return resolved
        return [] if not open_fallback else list(_OPEN_SOURCE_IPS)

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


def _port_mode(raw: Any) -> Optional[str]:
    """Return a port mode, or None to inherit ``firewall.mode``."""
    if raw is None or not str(raw).strip():
        return None
    mode = normalize_firewall_mode_name(raw)
    if mode not in FIREWALL_MODES:
        log.warning("Unknown firewall port mode %r; using firewall.mode", raw)
        return None
    return mode


def _port_allowed_ips(raw: Any) -> Optional[List[str]]:
    """Return an explicit allow-list, or None when the key should be ignored."""
    if raw is None:
        return []
    if isinstance(raw, (str, int)):
        text = str(raw).strip()
        return [text] if text else []
    if isinstance(raw, list):
        return [str(item) for item in raw if item is not None and str(item).strip()]
    log.warning("Ignoring firewall port allowed_ips of type %s", type(raw).__name__)
    return None


def normalize_firewall_ports(
    ports: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Normalize ``firewall.ports`` entries.

    Accepted forms:
      - ``8080`` / ``"8080"`` / ``"8000-8010"`` → TCP, inherit firewall mode
      - ``{port: 8080}`` / ``{port: 53, protocol: udp, description: "..."}``
      - ``{port: 443, mode: open}`` overrides the source policy
      - ``{port: 9090, allowed_ips: ["198.51.100.10/32"]}`` replaces ``firewall.allowed_ips``

    ``mode`` None and ``allowed_ips`` None both mean inherit. An explicit
    ``allowed_ips: []`` is an empty list, not inherit.
    """
    normalized: List[Dict[str, Any]] = []
    seen = set()
    for item in ports or []:
        protocol = "tcp"
        port: Optional[str] = None
        description = ""
        mode: Optional[str] = None
        allowed_ips: Optional[List[str]] = None
        if isinstance(item, dict):
            raw_port = item.get("port")
            if raw_port is None:
                continue
            port = str(raw_port).strip()
            protocol = str(item.get("protocol") or "tcp").strip().lower() or "tcp"
            description = str(item.get("description") or "").strip()
            if "mode" in item:
                mode = _port_mode(item.get("mode"))
            if "allowed_ips" in item:
                allowed_ips = _port_allowed_ips(item.get("allowed_ips"))
        else:
            port = str(item).strip()
        if not port:
            continue
        if protocol not in ("tcp", "udp"):
            log.warning("Skipping firewall port with unsupported protocol %r", protocol)
            continue
        key = (protocol, port)
        if key in seen:
            log.warning("Skipping duplicate firewall port %s/%s", protocol, port)
            continue
        seen.add(key)
        normalized.append(
            {
                "protocol": protocol,
                "port": port,
                "description": description or f"Allow {protocol.upper()} {port}",
                "mode": mode,
                "allowed_ips": allowed_ips,
            }
        )
    return normalized


def firewall_config_needs_client_ip(
    firewall_cfg: Optional[Dict[str, Any]] = None,
    ports: Optional[List[Any]] = None,
) -> bool:
    """True when SSH or any declared port uses a dynamic mode."""
    cfg = firewall_cfg or {}
    if firewall_needs_client_ip(resolve_firewall_mode(cfg)):
        return True
    declared = ports if ports is not None else cfg.get("ports")
    for entry in normalize_firewall_ports(declared):
        if entry["protocol"] == "tcp" and entry["port"] == "22":
            continue
        mode = entry.get("mode") or resolve_firewall_mode(cfg)
        if firewall_needs_client_ip(mode):
            return True
    return False


def sources_for_port(
    entry: Dict[str, Any],
    *,
    firewall_cfg: Optional[Dict[str, Any]],
    client_ip: Optional[str],
    existing_ips: Optional[List[str]],
    inherited_sources: List[str],
) -> List[str]:
    """
    Source IPs for one app port.

    An empty list means the port is not opened. Ports that omit both ``mode``
    and ``allowed_ips`` use the same sources as SSH.
    """
    if not entry.get("mode") and entry.get("allowed_ips") is None:
        return list(inherited_sources)

    cfg = firewall_cfg or {}
    effective_mode = entry.get("mode") or resolve_firewall_mode(cfg)
    if entry.get("allowed_ips") is None:
        allowed = list(cfg.get("allowed_ips") or [])
        explicit_empty_static = False
    else:
        allowed = list(entry["allowed_ips"])
        explicit_empty_static = effective_mode == "static" and not allowed
    sources = firewall_source_ips(
        {"mode": effective_mode, "allowed_ips": allowed},
        client_ip=client_ip,
        existing_ips=existing_ips,
        open_fallback=not explicit_empty_static,
    )
    if not sources:
        log.warning(
            "Firewall port %s/%s has no source IPs; not opening it",
            entry["protocol"],
            entry["port"],
        )
    return sources


def default_firewall_rules(
    source_ips: List[str],
    extra_rules: Optional[List[Dict[str, Any]]] = None,
    ports: Optional[List[Any]] = None,
    firewall_cfg: Optional[Dict[str, Any]] = None,
    client_ip: Optional[str] = None,
    existing_port_ips: Optional[Dict[Tuple[str, str], List[str]]] = None,
) -> List[Dict[str, Any]]:
    """SSH + ICMP + optional app ports used for create and set_rules refresh.

    SSH and ICMP always use ``source_ips`` (the firewall-level policy). Each
    port uses that list unless it sets ``mode`` or ``allowed_ips``.
    """
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
    existing = existing_port_ips or {}
    for entry in normalize_firewall_ports(ports):
        # Never duplicate the built-in SSH rule.
        if entry["protocol"] == "tcp" and entry["port"] == "22":
            continue
        key = (entry["protocol"], entry["port"])
        port_sources = sources_for_port(
            entry,
            firewall_cfg=firewall_cfg,
            client_ip=client_ip,
            existing_ips=list(existing.get(key) or []),
            inherited_sources=source_ips,
        )
        if not port_sources:
            continue
        rules.append(
            {
                "direction": "in",
                "protocol": entry["protocol"],
                "port": entry["port"],
                "source_ips": port_sources,
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
        firewall_cfg: Optional[Dict[str, Any]] = None,
        client_ip: Optional[str] = None,
        existing_port_ips: Optional[Dict[Tuple[str, str], List[str]]] = None,
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
                firewall_cfg=firewall_cfg,
                client_ip=client_ip,
                existing_port_ips=existing_port_ips,
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
        firewall_cfg: Optional[Dict[str, Any]] = None,
        client_ip: Optional[str] = None,
        existing_port_ips: Optional[Dict[Tuple[str, str], List[str]]] = None,
    ) -> Dict[str, Any]:
        """Replace firewall rules (Hetzner ``set_rules`` action)."""
        data = {
            'rules': default_firewall_rules(
                list(allowed_ips),
                extra_rules=extra_rules,
                ports=ports,
                firewall_cfg=firewall_cfg,
                client_ip=client_ip,
                existing_port_ips=existing_port_ips,
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

    def state_object_path(self, name: Optional[str] = None) -> str:
        """Bucket path of the state index, or of one environment record."""
        if name:
            key = f"{self.state_prefix}/environments/{name}.yml"
        else:
            key = f"{self.state_prefix}/state.yml"
        return f"{self.state_bucket}/{key}"

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

            log.debug(
                "State synced to %s and %s",
                self.state_object_path(),
                self.state_object_path(env_name),
            )
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

        # Host details. provider is the cloud; type is the kind (vm).
        host = host_block(self.config)
        self.provider = host.get("provider") or CLOUD_PROVIDER_HETZNER
        self.vm_name = self.name
        self.host_type = host.get("type") or HOST_KIND_VM
        self.vm_type = host.get("server_type") or DEFAULT_SERVER_TYPE
        self.region = host.get("availability_zone") or "hel1"
        self.image = host.get("image") or "ubuntu-24.04"
        if self.provider not in IMPLEMENTED_CLOUD_PROVIDERS:
            available = ", ".join(sorted(IMPLEMENTED_CLOUD_PROVIDERS))
            raise ValueError(
                f"Cloud provider {self.provider!r} is not available yet "
                f"(supported now: {available})"
            )

        self.hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
        self.manager = RemoteDevManager(self.hetzner_token, storage_manager)

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
                system_content_hash,
                system_hash_inputs_from_project,
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
        digest = workspace_content_hash(project_dir, project=project)
        sys_inputs = system_hash_inputs_from_project(project)
        system_hash = system_content_hash(
            project=project,
            var_map=sys_inputs.get("var_map"),
            project_dir=project_dir,
        )
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
            system_hash=system_hash,
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
            ports = firewall_config.get("ports") or []
            client_ip = None
            if firewall_config_needs_client_ip(firewall_config, ports):
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
                firewall_cfg=firewall_config,
                client_ip=client_ip,
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
        Run provision → configure → authorize → materialize against the remote VM.

        Snapshot-covered provision/configure may be skipped; authorize always
        runs and is never snapshotted (secrets stay off generic images).
        """
        return self.apply_playbooks(
            project_dir=project_dir,
            stream=stream,
            playbooks=STAGES_CREATE,
            soft_fail_materialize=True,
            force_system=force_system,
            force_workspace=force_workspace,
            action="create",
        )

    def apply_playbooks(
        self,
        project_dir: Optional[str] = None,
        stream: bool = True,
        playbooks: tuple = ("authorize", "materialize"),
        soft_fail_materialize: bool = False,
        soft_fail_workspace: bool = False,  # back-compat alias
        force_system: bool = False,
        force_workspace: bool = False,
        action: Optional[str] = None,
    ) -> bool:
        """
        Re-run selected lifecycle playbooks against an existing remote VM.

        Stages: ``provision``, ``configure``, ``authorize``, ``materialize``
        (order preserved). ``authorize`` is never skipped via snapshots.
        Snapshot is taken only after ``configure``. Used by ``rig create`` and
        ``rig apply``.
        """
        if soft_fail_workspace:
            soft_fail_materialize = True
        if not self.vm_ip or not self.ssh_key_name:
            log.error("VM is not ready for provisioning (missing IP or SSH key)")
            return False

        wanted = tuple(p for p in playbooks if p in STAGES_CREATE)
        if not wanted:
            log.error(
                "No playbooks selected (use provision, configure, authorize, "
                "and/or materialize)"
            )
            return False

        skip = set(self.skip_playbooks or ())
        # Legacy skip names from older snapshots / boot decisions.
        if "system" in skip:
            skip.add("provision")
        if "services" in skip or "workspace" in skip:
            # Old workspace skip meant system+workspace; map to build layer only.
            if "workspace" in skip and "system" in (self.skip_playbooks or ()):
                skip.update(SNAPSHOT_COVERED)
            skip.discard("workspace")
            skip.discard("services")
        skip.discard("system")
        for stage in ALWAYS_RUN:
            skip.discard(stage)
        if force_system:
            for stage in SNAPSHOT_COVERED:
                skip.discard(stage)
        if force_workspace:
            skip.discard("materialize")
        if skip:
            skipped = [p for p in wanted if p in skip]
            if skipped:
                log.debug(
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

        installed_agents, _default_agent = configured_agents(project)
        log.debug(
            "Applying %s on %s as root@%s (agents=%s)",
            "+".join(wanted),
            project_dir,
            self.vm_ip,
            ",".join(installed_agents),
        )

        # Ensure legacy /root/<repo> clone destinations are under /workspace.
        try:
            from cli.git import GitManager
            GitManager(project).normalize_workspace_dests()
        except Exception as exc:
            log.debug("Could not normalize clone destinations: %s", exc)

        # HTTPS clones without a PAT hang on an interactive credential prompt.
        try:
            git_cfg = project.get_vars("git") or {}
            clones = git_cfg.get("clone_repos") or []
            has_github_pat = bool(project.get_vars("github_pat"))
            has_gitlab_pat = bool(project.get_vars("gitlab_pat"))
            missing = []
            for item in clones:
                if not isinstance(item, dict):
                    continue
                repo = str(item.get("repo") or "")
                if not repo.startswith("https://"):
                    continue
                pat_var = item.get("password_var") or ""
                if pat_var and project.get_vars(str(pat_var)):
                    continue
                if "gitlab.com" in repo and has_gitlab_pat:
                    continue
                if "gitlab.com" not in repo and has_github_pat:
                    continue
                missing.append(item.get("dest") or repo)
            if missing and "materialize" in wanted:
                log.warning(
                    "HTTPS clone(s) have no PAT (%s). "
                    "Private repos will fail auth. Store one with: "
                    "rig vars encrypt github_pat  "
                    "(then set password_var on each git.clone_repos entry, "
                    "or rely on github_pat / gitlab_pat fallback)",
                    ", ".join(str(m) for m in missing),
                )
        except Exception as exc:
            log.debug("Could not preflight git clone credentials: %s", exc)

        ansible = AnsibleManager(project)
        if not ansible.configure(project_dir):
            return False
        # Refresh managed playbooks so provision/authorize/destroy fixes ship.
        project.write_provision_playbook(force=True)
        project.write_authorize_playbook(force=True)
        project.write_destroy_playbook(force=True)
        if not ansible.setup_bootstrap(self.vm_ip, ssh_key_path):
            return False
        ansible.rig_vars = build_rig_ansible_vars(
            self,
            project,
            action=action or "apply",
        )

        path_by_kind = {
            "provision": ansible.playbook_path,
            "configure": ansible.configure_playbook_path,
            "authorize": ansible.authorize_playbook_path,
            "materialize": ansible.materialize_playbook_path,
        }

        materialize_failed = False
        for kind in wanted:
            path = path_by_kind.get(kind)
            if not path or not os.path.exists(path):
                log.error("%s playbook not found: %s", kind.capitalize(), path)
                return False
            log.info("Running %s playbook %s", kind, path)
            if not ansible._run_one_playbook(path, stream=stream):
                if kind == "materialize" and soft_fail_materialize:
                    log.warning(
                        "Materialize playbook failed (often an invalid git PAT); "
                        "continuing"
                    )
                    materialize_failed = True
                    continue
                log.error("%s playbook failed", kind.capitalize())
                return False
            # Snapshot only after configure (build layer); never after authorize.
            if kind == SNAPSHOT_AFTER:
                if not self._snapshot_after_playbook(kind, project):
                    log.warning("Continuing without a successful %s snapshot", kind)

        if materialize_failed:
            log.warning("Playbooks finished (materialize had errors)")
        return True

    def _snapshot_after_playbook(
        self,
        kind: str,
        project: "ProjectConfig",
    ) -> bool:
        """Create the system (build-layer) Hetzner snapshot after configure."""
        if kind != SNAPSHOT_AFTER:
            return True
        if not self.vm_id:
            self.resolve_resources()
        if not self.vm_id:
            log.warning("Cannot create snapshot: VM id unknown for '%s'", self.name)
            return False

        try:
            from cli.snapshots import (
                package_version,
                system_content_hash,
                system_hash_inputs_from_project,
                system_snapshot_labels,
                system_snapshot_name,
            )
        except ImportError as exc:
            log.warning("Snapshot helpers unavailable: %s", exc)
            return False

        version = package_version()
        inputs = system_hash_inputs_from_project(project)
        digest = system_content_hash(
            project=project,
            var_map=inputs.get("var_map"),
            project_dir=project.project_dir,
        )
        description = system_snapshot_name(version, content_hash=digest)
        labels = system_snapshot_labels(
            version,
            content_hash=digest,
            agent_type=inputs.get("agent_type"),
        )

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
        if firewall_config_needs_client_ip(firewall_config, ports):
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
            current_port_ips: Dict[Tuple[str, str], List[str]] = {}
            for rule in rules:
                if rule.get("direction") != "in":
                    continue
                proto = str(rule.get("protocol") or "")
                port = str(rule.get("port") or "")
                if proto == "tcp" and port == "22":
                    ssh_sources = list(rule.get("source_ips") or [])
                elif proto in ("tcp", "udp") and port and port != "22":
                    current_port_ips[(proto, port)] = list(rule.get("source_ips") or [])

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

            desired_rules = default_firewall_rules(
                desired,
                ports=ports,
                firewall_cfg=firewall_config,
                client_ip=client_ip,
                existing_port_ips=current_port_ips,
            )
            desired_port_ips = {
                (str(rule.get("protocol")), str(rule.get("port"))): list(rule.get("source_ips") or [])
                for rule in desired_rules
                if rule.get("protocol") in ("tcp", "udp") and rule.get("port") not in (None, "", "22")
            }
            sources_unchanged = set(ssh_sources) == set(desired)
            ports_unchanged = {
                key: tuple(sorted(value)) for key, value in current_port_ips.items()
            } == {
                key: tuple(sorted(value)) for key, value in desired_port_ips.items()
            }
            if sources_unchanged and ports_unchanged:
                log.debug(
                    "Firewall already allows %s (ports=%s)",
                    ", ".join(desired),
                    sorted(f"{proto}/{num}" for proto, num in desired_port_ips) or "none",
                )
                return True

            log.info(
                "Updating firewall (%s): %s → %s; ports %s → %s",
                mode,
                ", ".join(ssh_sources) or "unset",
                ", ".join(desired),
                sorted(f"{proto}/{num}" for proto, num in current_port_ips) or "none",
                sorted(f"{proto}/{num}" for proto, num in desired_port_ips) or "none",
            )
            self.manager.set_firewall_rules(
                self.firewall_id,
                desired,
                extra_rules=extra_rules,
                ports=ports,
                firewall_cfg=firewall_config,
                client_ip=client_ip,
                existing_port_ips=current_port_ips,
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

    def run_destroy_playbooks(
        self,
        project_dir: Optional[str] = None,
        stream: bool = True,
    ) -> bool:
        """
        Run ``finalize`` then ``destroy`` playbooks before deleting the VM.

        Soft-fails when a playbook is missing, SSH is unavailable, or Ansible
        fails — destroy should still proceed so the VM is not left orphaned.
        """
        project_dir = os.path.abspath(project_dir or os.getcwd())
        if not self.vm_ip:
            log.debug("Skipping destroy playbooks (no VM IP)")
            return False

        ssh_key_path = f"/tmp/{self.ssh_key_name}"
        if not os.path.exists(ssh_key_path):
            log.warning(
                "Skipping destroy playbooks (SSH key missing at %s)",
                ssh_key_path,
            )
            return False

        project = ProjectConfig(project_dir)
        if not project.load() or not project.config:
            log.debug("Skipping destroy playbooks (no rig.yml in %s)", project_dir)
            return False

        ansible = AnsibleManager(project)
        if not ansible.configure(project_dir):
            log.warning("Skipping destroy playbooks (Ansible configure failed)")
            return False

        project.write_destroy_playbook(force=True)
        path_by_kind = {
            "finalize": ansible.finalize_playbook_path,
            "destroy": ansible.destroy_playbook_path,
        }
        if not ansible.setup_bootstrap(self.vm_ip, ssh_key_path):
            log.warning("Destroy playbooks skipped (could not reach VM)")
            return False
        ansible.rig_vars = build_rig_ansible_vars(self, project, action="destroy")

        ok = True
        for kind in STAGES_DESTROY:
            path = path_by_kind.get(kind)
            if not path or not os.path.exists(path):
                log.debug("Skipping %s playbook (not found)", kind)
                continue
            log.info("Running %s playbook %s", kind, path)
            if not ansible._run_one_playbook(path, stream=stream):
                log.warning("%s playbook failed; continuing with VM deletion", kind.capitalize())
                ok = False
        return ok

    def cleanup(self, project_dir: Optional[str] = None):
        """Clean up the remote environment (finalize → destroy playbooks, then delete VM)."""
        log.info("Destroying remote environment '%s'", self.name)
        project_dir = os.path.abspath(project_dir or os.getcwd())
        self.resolve_resources()
        # Refresh client IP allow-list before finalize/destroy (needs SSH to the VM).
        self.ensure_firewall_access(project_dir=project_dir)
        self.run_destroy_playbooks(project_dir=project_dir, stream=True)

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
                log.debug("State synced to %s", self.manager.state_object_path())
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
                log.debug("State synced to %s", self.manager.state_object_path())

            return True

        except subprocess.CalledProcessError as e:
            print(f"Error installing agent: {e}")
            return False

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
        if agent == "cursor":
            return (
                'export PATH="$HOME/.local/bin:$PATH"; '
                "set -a; "
                "[ -f /root/.cursor/credentials.env ] && . /root/.cursor/credentials.env; "
                "set +a; "
                f"mkdir -p {REMOTE_WORKSPACE_ROOT} && "
                f"cd {REMOTE_WORKSPACE_ROOT} && exec agent{suffix}"
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

        ``agent_args`` are appended to the remote ``opencode`` / ``claude`` /
        ``agent`` (Cursor) invocation (e.g. ``--continue`` to resume).

        After the agent exits, captures a printed session / resume id (when
        present) under ``/tmp/.rig/sessions/<env>`` so a later bare
        ``rig attach`` can reopen the same session.

        Returns the SSH process exit code (0 on success).
        """
        if not self.vm_ip or not self.ssh_key_name:
            log.error("VM not ready for attachment")
            return 1

        if not agent_type:
            project = ProjectConfig(os.path.abspath(project_dir or os.getcwd()))
            project.load()
            _installed, agent_type = configured_agents(project)

        ssh_key_path = f"/tmp/{self.ssh_key_name}"
        remote_cmd = self.agent_remote_command(agent_type, extra_args=agent_args)
        ssh_cmd = self._interactive_ssh_command(remote_cmd)

        log.info("Attaching to %s on %s", agent_type, self.name)
        log.debug("SSH key=%s", ssh_key_path)
        log.debug("Remote command=%s", remote_cmd)

        from cli.last_session import (
            extract_session_from_agent_args,
            parse_session_id,
            remember_last_session,
        )

        attach_log = os.path.join("/tmp/.rig", f"attach-{self.name}.log")
        try:
            os.makedirs("/tmp/.rig", mode=0o700, exist_ok=True)
        except OSError:
            pass

        exit_code = 1
        try:
            exit_code = self._run_attach_ssh(ssh_cmd, attach_log)
        except FileNotFoundError:
            log.error("ssh client not found on PATH")
            return 1
        except KeyboardInterrupt:
            log.info("Detached from agent session")
            exit_code = 130

        session_id = extract_session_from_agent_args(agent_args)
        if not session_id:
            try:
                with open(attach_log, "r", encoding="utf-8", errors="replace") as handle:
                    session_id = parse_session_id(handle.read())
            except OSError:
                session_id = None
        if session_id:
            remember_last_session(self.name, session_id, agent_type=str(agent_type))
            log.info("Remembered agent session %s for '%s'", session_id, self.name)
            log.debug("Resume later with: rig attach  (or --resume/--session %s)", session_id)
        return int(exit_code)

    def _interactive_ssh_command(self, remote_cmd: str) -> List[str]:
        """SSH argv for an interactive session as root."""
        ssh_key_path = f"/tmp/{self.ssh_key_name}"
        return [
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
            "-o",
            f"LogLevel={ssh_option_log_level()}",
            f"root@{self.vm_ip}",
            remote_cmd,
        ]

    def open_ssh(self, remote_command: Optional[str] = None) -> int:
        """Open an SSH shell on the VM, or run one remote command."""
        if not self.vm_ip or not self.ssh_key_name:
            log.error("VM not ready for SSH")
            return 1

        if remote_command:
            remote_cmd = remote_command
        else:
            remote_cmd = (
                f"mkdir -p {REMOTE_WORKSPACE_ROOT} && "
                f"cd {REMOTE_WORKSPACE_ROOT} && exec bash -l"
            )
        ssh_cmd = self._interactive_ssh_command(remote_cmd)
        log.info("Connecting to %s", self.name)
        log.debug("SSH key=%s", f"/tmp/{self.ssh_key_name}")
        log.debug("Remote command=%s", remote_cmd)

        session_log = os.path.join("/tmp/.rig", f"ssh-{self.name}.log")
        try:
            os.makedirs("/tmp/.rig", mode=0o700, exist_ok=True)
        except OSError:
            pass
        try:
            return self._run_attach_ssh(ssh_cmd, session_log)
        except FileNotFoundError:
            log.error("ssh client not found on PATH")
            return 1
        except KeyboardInterrupt:
            log.info("Disconnected from %s", self.name)
            return 130

    @staticmethod
    def _run_attach_ssh(ssh_cmd: List[str], attach_log: str) -> int:
        """
        Run interactive SSH, preferably under ``script`` so exit banners are
        captured while the TTY stays usable.
        """
        script_bin = shutil.which("script")
        if script_bin:
            try:
                if sys.platform == "darwin":
                    # BSD script: script [-q] [file] command...
                    completed = subprocess.run(
                        [script_bin, "-q", attach_log, *ssh_cmd]
                    )
                else:
                    # util-linux: script -q -c 'cmd' file
                    completed = subprocess.run(
                        [
                            script_bin,
                            "-q",
                            "-c",
                            " ".join(shlex.quote(part) for part in ssh_cmd),
                            attach_log,
                        ]
                    )
                return int(completed.returncode or 0)
            except FileNotFoundError:
                pass

        # Fallback: no session capture without a typescript helper.
        completed = subprocess.run(ssh_cmd)
        return int(completed.returncode or 0)

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
    def resolve_editor_command(
        prefer: Optional[str] = None,
        fallbacks: Optional[Sequence[str]] = None,
    ) -> Optional[str]:
        """
        Pick a local editor CLI.

        ``fallbacks`` defaults to every supported editor. Pass ``[]`` to require
        ``prefer`` and skip other binaries.
        """
        candidates: List[str] = []
        if prefer:
            candidates.append(prefer)
        extra = list(SUPPORTED_EDITORS) if fallbacks is None else list(fallbacks)
        for name in extra:
            if name not in candidates:
                candidates.append(name)
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
        editor_fallbacks: Optional[Sequence[str]] = None,
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

        editor_bin = self.resolve_editor_command(
            prefer=editor, fallbacks=editor_fallbacks
        )
        if not editor_bin:
            url = self.code_server_url(
                project_dir=project_dir, folder=remote_path
            )
            if editor and editor_fallbacks == []:
                log.error("Editor '%s' not found", editor)
            else:
                tried = editor_fallbacks if editor_fallbacks is not None else SUPPORTED_EDITORS
                log.error(
                    "No local editor CLI found (tried: %s)",
                    ", ".join(tried) or editor or "code",
                )
            log.error("Install the editor shell command, or run: rig dev --browser")
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
        installed_agents, agent_type = configured_agents(project)
        return {
            "vm_ip": self.vm_ip,
            "ssh_key_path": f"/tmp/{self.ssh_key_name}",
            "agent_type": agent_type,
            "agent_types": installed_agents,
            "remote_command": self.agent_remote_command(agent_type),
            "agent_status": "installed" if self.vm_ip else "not installed",
        }


if __name__ == '__main__':
    # Example usage
    token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not token:
        print("Error: HETZNER_CLOUD_API_TOKEN not set")
        sys.exit(1)

    # Example configuration
    config = {
        'host': {
            'provider': 'hetzner',
            'type': 'vm',
            'server_type': 'cax11',
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


# Lifecycle stages (flat playbooks under .rig/).
# Create: provision → configure → [snapshot] → authorize → materialize
# Destroy: finalize → destroy
STAGES_CREATE: Tuple[str, ...] = (
    "provision",
    "configure",
    "authorize",
    "materialize",
)
STAGES_DESTROY: Tuple[str, ...] = ("finalize", "destroy")
SNAPSHOT_AFTER = "configure"
SNAPSHOT_COVERED: Tuple[str, ...] = ("provision", "configure")
ALWAYS_RUN = frozenset({"authorize"})

# Auto-generated provision playbook (.rig/provision.yml).
# Referenced from root rig.yml playbooks.provision.
PROVISION_PLAYBOOK = r"""---
# Provision playbook: base packages, VS Code (code-server), coding agent.
- name: Provision remote system
  hosts: remote_vm
  become: true
  gather_facts: true
  vars:
    coding_agent_type: "{{ coding_agent_type | default('opencode') }}"
    _rig_agent_types: "{{ coding_agent_types | default([coding_agent_type]) }}"
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
      # Password + systemd start live in authorize.yml (secrets; not snapshotted).

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
      when: "'opencode' in _rig_agent_types"

    - name: Symlink OpenCode onto PATH
      file:
        src: /root/.opencode/bin/opencode
        dest: /usr/local/bin/opencode
        state: link
        force: true
      when: "'opencode' in _rig_agent_types"

    - name: Ensure OpenCode bin is on PATH for login shells
      lineinfile:
        path: /root/.bashrc
        line: 'export PATH="$HOME/.opencode/bin:$PATH"'
        create: true
      when: "'opencode' in _rig_agent_types"

    - name: Ensure OpenCode config directory exists
      file:
        path: /root/.config/opencode
        state: directory
        mode: "0700"
      when: "'opencode' in _rig_agent_types"

    - name: Deploy OpenCode config from .rig/.opencode
      copy:
        src: "{{ playbook_dir }}/.opencode/opencode.json"
        dest: /root/.config/opencode/opencode.json
        mode: "0600"
      when: "'opencode' in _rig_agent_types"

    - name: Deploy OpenCode MCP config from .rig/.opencode
      copy:
        src: "{{ playbook_dir }}/.opencode/mcp.json"
        dest: /root/.config/opencode/mcp.json
        mode: "0600"
      when: "'opencode' in _rig_agent_types"

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
      when: "'opencode' in _rig_agent_types"

    - name: Deploy AGENTS.md as OpenCode global rules
      copy:
        src: "{{ playbook_dir }}/AGENTS.md"
        dest: /root/.config/opencode/AGENTS.md
        mode: "0644"
      when: "'opencode' in _rig_agent_types"

    # --- Coding agent: Claude Code ---
    # Do not use creates= alone: verify the binary actually runs (same as OpenCode).
    - name: Install Claude Code CLI
      shell: |
        set -euo pipefail
        bin=/root/.local/bin/claude
        if [ -x "$bin" ] && [ -s "$bin" ] && "$bin" --version >/dev/null 2>&1; then
          exit 0
        fi
        rm -f "$bin"
        curl -fsSL https://claude.ai/install.sh | bash
        # Installer may place the binary under ~/.local/bin or elsewhere on PATH.
        if [ ! -x "$bin" ] || [ ! -s "$bin" ]; then
          found="$(command -v claude || true)"
          if [ -n "$found" ] && [ -x "$found" ]; then
            mkdir -p "$(dirname "$bin")"
            ln -sfn "$found" "$bin"
          fi
        fi
        test -s "$bin"
        test -x "$bin"
        "$bin" --version
      args:
        executable: /bin/bash
      environment:
        HOME: /root
      when: "'claude' in _rig_agent_types"

    - name: Ensure Claude local bin is on PATH
      lineinfile:
        path: /root/.bashrc
        line: 'export PATH="$HOME/.local/bin:$PATH"'
        create: true
      when: "'claude' in _rig_agent_types"

    - name: Symlink Claude onto PATH
      file:
        src: /root/.local/bin/claude
        dest: /usr/local/bin/claude
        state: link
        force: true
      when: "'claude' in _rig_agent_types"

    - name: Ensure Claude config directory exists
      file:
        path: /root/.claude
        state: directory
        mode: "0700"
      when: "'claude' in _rig_agent_types"

    - name: Deploy Claude settings from .rig/.claude
      copy:
        src: "{{ playbook_dir }}/.claude/settings.json"
        dest: /root/.claude/settings.json
        mode: "0600"
      when: "'claude' in _rig_agent_types"

    - name: Deploy Claude MCP config as project .mcp.json
      copy:
        src: "{{ playbook_dir }}/.claude/mcp.json"
        dest: /root/.mcp.json
        mode: "0600"
      when: "'claude' in _rig_agent_types"

    - name: Deploy AGENTS.md as Claude global rules (CLAUDE.md)
      copy:
        src: "{{ playbook_dir }}/AGENTS.md"
        dest: /root/.claude/CLAUDE.md
        mode: "0644"
      when: "'claude' in _rig_agent_types"

    - name: Place AGENTS.md in workspace for agent discovery
      copy:
        src: "{{ playbook_dir }}/AGENTS.md"
        dest: "{{ workspace_root | default('/workspace') }}/AGENTS.md"
        mode: "0644"

    # --- Coding agent: Cursor CLI (`agent`) ---
    # Do not use creates= alone: verify the binary actually runs.
    - name: Install Cursor CLI
      shell: |
        set -euo pipefail
        bin=/root/.local/bin/agent
        if [ -x "$bin" ] && [ -s "$bin" ] && "$bin" --version >/dev/null 2>&1; then
          exit 0
        fi
        curl -fsSL https://cursor.com/install | bash
        test -s "$bin"
        test -x "$bin"
        "$bin" --version
      args:
        executable: /bin/bash
      environment:
        HOME: /root
      when: "'cursor' in _rig_agent_types"

    - name: Ensure Cursor local bin is on PATH
      lineinfile:
        path: /root/.bashrc
        line: 'export PATH="$HOME/.local/bin:$PATH"'
        create: true
      when: "'cursor' in _rig_agent_types"

    - name: Symlink Cursor agent onto PATH
      file:
        src: /root/.local/bin/agent
        dest: /usr/local/bin/agent
        state: link
        force: true
      when: "'cursor' in _rig_agent_types"

    - name: Ensure Cursor config directory exists
      file:
        path: /root/.cursor
        state: directory
        mode: "0700"
      when: "'cursor' in _rig_agent_types"

    - name: Deploy Cursor CLI config from .rig/.cursor
      copy:
        src: "{{ playbook_dir }}/.cursor/cli-config.json"
        dest: /root/.cursor/cli-config.json
        mode: "0600"
      when: "'cursor' in _rig_agent_types"

    - name: Deploy Cursor MCP config from .rig/.cursor
      copy:
        src: "{{ playbook_dir }}/.cursor/mcp.json"
        dest: /root/.cursor/mcp.json
        mode: "0600"
      when: "'cursor' in _rig_agent_types"
"""

# User image customization (.rig/configure.yml). Cached with provision; never secrets.
CONFIGURE_PLAYBOOK = """---
# Configure playbook: user image/service customizations (cached in system snapshot).
# Keep this secret-free — credentials belong in authorize.yml.
- name: Configure remote services
  hosts: remote_vm
  become: true
  gather_facts: false
  tasks:
    - name: Configure placeholder (customize this playbook)
      debug:
        msg: >-
          No custom configure tasks for {{ rig_env_name | default('env') }}.
          Add secret-free installs/services here (cached with provision).
"""

# Secrets / auth material (.rig/authorize.yml). Always runs; never snapshotted.
AUTHORIZE_PLAYBOOK = r"""---
# Authorize playbook: inject credentials after provision/configure (not snapshotted).
- name: Authorize remote access
  hosts: remote_vm
  become: true
  gather_facts: false
  vars:
    coding_agent_type: "{{ coding_agent_type | default('opencode') }}"
    _rig_agent_types: "{{ coding_agent_types | default([coding_agent_type]) }}"
    code_server_port: "{{ code_server_port | default(8080) }}"
    code_server_user: "{{ code_server_user | default('coder') }}"
  tasks:
    - name: Configure code-server with vaulted browser password
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
      no_log: true
      when: vscode_browser_password is defined and vscode_browser_password | length > 0

    - name: Enable and start code-server for user
      systemd:
        name: "code-server@{{ code_server_user }}"
        enabled: true
        state: started
        daemon_reload: true
      when: vscode_browser_password is defined and vscode_browser_password | length > 0

    - name: Resolve per-agent API keys
      set_fact:
        rig_opencode_api_key: "{{ opencode_api_key | default('') }}"
        rig_claude_api_key: "{{ claude_api_key | default('') }}"
        rig_cursor_api_key: "{{ cursor_api_key | default('') }}"
      no_log: true

    - name: Use legacy coding_agent_api_key for a single OpenCode install
      set_fact:
        rig_opencode_api_key: "{{ coding_agent_api_key }}"
      no_log: true
      when:
        - _rig_agent_types | length == 1
        - (_rig_agent_types[0] | string) == "opencode"
        - rig_opencode_api_key | length == 0
        - coding_agent_api_key is defined
        - coding_agent_api_key | length > 0

    - name: Use legacy coding_agent_api_key for a single Claude Code install
      set_fact:
        rig_claude_api_key: "{{ coding_agent_api_key }}"
      no_log: true
      when:
        - _rig_agent_types | length == 1
        - (_rig_agent_types[0] | string) == "claude"
        - rig_claude_api_key | length == 0
        - coding_agent_api_key is defined
        - coding_agent_api_key | length > 0

    - name: Use legacy coding_agent_api_key for a single Cursor CLI install
      set_fact:
        rig_cursor_api_key: "{{ coding_agent_api_key }}"
      no_log: true
      when:
        - _rig_agent_types | length == 1
        - (_rig_agent_types[0] | string) == "cursor"
        - rig_cursor_api_key | length == 0
        - coding_agent_api_key is defined
        - coding_agent_api_key | length > 0

    - name: Write OpenCode credentials env file
      copy:
        dest: /root/.config/opencode/credentials.env
        mode: "0600"
        content: |
          CODING_AGENT_API_KEY={{ rig_opencode_api_key }}
          ANTHROPIC_API_KEY={{ rig_opencode_api_key }}
          OPENCODE_API_KEY={{ rig_opencode_api_key }}
      no_log: true
      when:
        - "'opencode' in _rig_agent_types"
        - rig_opencode_api_key | length > 0

    - name: Write Claude credentials env file
      copy:
        dest: /root/.claude/credentials.env
        mode: "0600"
        content: |
          CODING_AGENT_API_KEY={{ rig_claude_api_key }}
          ANTHROPIC_API_KEY={{ rig_claude_api_key }}
      no_log: true
      when:
        - "'claude' in _rig_agent_types"
        - rig_claude_api_key | length > 0

    - name: Write Cursor credentials env file
      copy:
        dest: /root/.cursor/credentials.env
        mode: "0600"
        content: |
          CODING_AGENT_API_KEY={{ rig_cursor_api_key }}
          CURSOR_API_KEY={{ rig_cursor_api_key }}
      no_log: true
      when:
        - "'cursor' in _rig_agent_types"
        - rig_cursor_api_key | length > 0

    - name: Install coding-agent profile.d env exports
      copy:
        dest: /etc/profile.d/rig-coding-agent.sh
        mode: "0644"
        content: |
          # Managed by rig authorize.yml — load the default agent's key only.
          # `rig attach --agent` sources that agent's credentials.env itself so
          # several installed agents do not overwrite one shared variable.
          _rig_default="{{ coding_agent_type | default('opencode') }}"
          if [ "$_rig_default" = "claude" ] && [ -f /root/.claude/credentials.env ]; then
            set -a
            # shellcheck disable=SC1091
            . /root/.claude/credentials.env
            set +a
          elif [ "$_rig_default" = "cursor" ] && [ -f /root/.cursor/credentials.env ]; then
            set -a
            # shellcheck disable=SC1091
            . /root/.cursor/credentials.env
            set +a
          elif [ -f /root/.config/opencode/credentials.env ]; then
            set -a
            # shellcheck disable=SC1091
            . /root/.config/opencode/credentials.env
            set +a
          fi
      when: >-
        (rig_opencode_api_key | default('') | length > 0)
        or (rig_claude_api_key | default('') | length > 0)
        or (rig_cursor_api_key | default('') | length > 0)
"""

# Materialize playbook (.rig/materialize.yml). Project content after authorize.
MATERIALIZE_PLAYBOOK = """---
# Materialize playbook: clone configured repos and populate /workspace.
# Rig injects runtime vars prefixed with rig_ (e.g. rig_env_name, rig_vm_ip).
- name: Materialize workspace
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
        # Fall back to provider default vault vars when password_var was skipped at init.
        _provider_pat: >-
          {{ (gitlab_pat | default(''))
             if ('gitlab.com' in (item.repo | default('')))
             else (github_pat | default('')) }}
        _pat: >-
          {{ (lookup('vars', _pat_var, default='') if _pat_var else '')
             or _provider_pat }}
        # GitHub expects x-access-token; GitLab expects oauth2.
        _auth_user: "{{ 'oauth2' if ('gitlab.com' in item.repo) else 'x-access-token' }}"
      environment:
        # Never hang on an interactive username/password prompt (missing PAT).
        GIT_TERMINAL_PROMPT: "0"
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

FINALIZE_PLAYBOOK = """---
# Finalize: user-defined destruction hooks before managed destroy
# (``rig destroy``). May target off-VM resources (DNS, volumes, cloud APIs,
# SaaS tenants) as well as the host. Runs with rig_* vars (e.g. rig_env_name).
- name: Finalize workspace
  hosts: remote_vm
  become: true
  gather_facts: false
  tasks:
    - name: Finalize placeholder (customize this playbook)
      debug:
        msg: "No custom finalize tasks for {{ rig_env_name | default('env') }}"
"""

DESTROY_PLAYBOOK = """---
# Destroy playbook: managed teardown of system resources before the VM is deleted.
# Auto-generated; do not put project-specific hooks here (use finalize.yml).
- name: Destroy managed environment
  hosts: remote_vm
  become: true
  gather_facts: false
  tasks:
    - name: Managed destroy placeholder
      debug:
        msg: "No managed destroy tasks for {{ rig_env_name | default('env') }}"
"""


class ProjectConfig:
    """Manages root rig.yml plus .rig/ support files (vars, playbooks)."""

    RIG_DIR = ".rig"
    RIG_YAML = "rig.yml"
    VARS_YAML = "vars.yml"
    PROVISION_YAML = "provision.yml"
    MATERIALIZE_YAML = "materialize.yml"
    AUTHORIZE_YAML = "authorize.yml"
    FINALIZE_YAML = "finalize.yml"
    CONFIGURE_YAML = "configure.yml"
    DESTROY_YAML = "destroy.yml"
    # Back-compat aliases
    INIT_YAML = PROVISION_YAML
    SETUP_YAML = RIG_YAML
    REQUIREMENTS_YML = "requirements.yml"
    # Back-compat alias
    REQUIREMENTS_TXT = REQUIREMENTS_YML
    ROLES_DIR = ROLES_DIRNAME
    LOCAL_ROLES_DIR = LOCAL_ROLES_DIRNAME

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
            "host": {
                "provider": CLOUD_PROVIDER_HETZNER,
                "type": HOST_KIND_VM,
                "server_type": DEFAULT_SERVER_TYPE,
                "availability_zone": "hel1",
                "image": "ubuntu-24.04",
            },
            "firewall": {
                # open | static | dynamic_replace | dynamic_add (default)
                # dynamic_add keeps existing allow-list entries and adds the
                # current CLI public IP when missing (re-checked on access).
                "mode": DEFAULT_FIREWALL_MODE,
                "allowed_ips": [],  # used by static; also merged into dynamic modes
                # Extra inbound ports. Omit mode/allowed_ips to inherit the firewall.
                # ports:
                #   - 8080
                #   - {port: 443, mode: open}
                #   - {port: 9090, mode: static, allowed_ips: ["198.51.100.10/32"]}
                "ports": [],
            },
            "agent": {
                "default": "opencode",
                "enabled": ["opencode"],
                "vscode": True,
                "credentials_var": "coding_agent_api_key",
            },
            # Local IDEs for ``rig dev`` (Remote-SSH). Not installed on the VM.
            "editor": {
                "default": DEFAULT_EDITOR,
                "enabled": [DEFAULT_EDITOR],
            },
            "playbooks": {
                "provision": f"{self.RIG_DIR}/{self.PROVISION_YAML}",
                "configure": f"{self.RIG_DIR}/{self.CONFIGURE_YAML}",
                "authorize": f"{self.RIG_DIR}/{self.AUTHORIZE_YAML}",
                "materialize": f"{self.RIG_DIR}/{self.MATERIALIZE_YAML}",
                "finalize": f"{self.RIG_DIR}/{self.FINALIZE_YAML}",
                "destroy": f"{self.RIG_DIR}/{self.DESTROY_YAML}",
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
                                "provision": f"{self.RIG_DIR}/{self.PROVISION_YAML}",
                                "configure": f"{self.RIG_DIR}/{self.CONFIGURE_YAML}",
                                "authorize": f"{self.RIG_DIR}/{self.AUTHORIZE_YAML}",
                                "materialize": f"{self.RIG_DIR}/{self.MATERIALIZE_YAML}",
                                "finalize": f"{self.RIG_DIR}/{self.FINALIZE_YAML}",
                                "destroy": f"{self.RIG_DIR}/{self.DESTROY_YAML}",
                            },
                            "inventory": f"{self.RIG_DIR}/inventory.yml",
                            "bootstrap": legacy.get("bootstrap") or {},
                        })

            vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
            if os.path.exists(vars_path):
                self.vars_config = self._load_vars_with_vault(vars_path)
            # Merge identity personal vars (PATs, agent key) over shared.
            try:
                from cli.vault_keys import personal_vars_path, filter_wrap_fields_from_yaml_text
                id_path = personal_vars_path(self.project_dir)
                if os.path.exists(id_path):
                    personal = self._load_vars_with_vault(id_path)
                    for key in ("rig_identity_data_key", "rig_shared_data_key"):
                        personal.pop(key, None)
                    self.vars_config = {**(self.vars_config or {}), **personal}
            except Exception:
                pass

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
                import tempfile
                from cli.vault_keys import (
                    DEFAULT_DATA_KEY_PATH,
                    PERSONAL_DATA_KEY_PATH,
                    SHARED_DATA_KEY_PATH,
                )
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    vault_cmd = ['ansible-vault', 'view', tmp_path]
                    vault_ids = []
                    for label, path in (
                        ("personal", PERSONAL_DATA_KEY_PATH),
                        ("shared", SHARED_DATA_KEY_PATH),
                        ("legacy", DEFAULT_DATA_KEY_PATH),
                    ):
                        if os.path.exists(path):
                            vault_ids.extend(["--vault-id", f"{label}@{path}"])
                    if vault_ids:
                        vault_cmd[2:2] = vault_ids
                    elif self.vault_password:
                        vault_cmd[2:2] = ['--vault-password', self.vault_password]
                    else:
                        raise ValueError(
                            "Vault encrypted file requires data keys under /tmp/.rig "
                            "or .rig/.vault_password."
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
                # Regular YAML (may contain inline !vault scalars). Strip key wraps.
                from cli.vault_keys import filter_wrap_fields_from_yaml_text
                cleaned = filter_wrap_fields_from_yaml_text(content)
                return yaml.load(cleaned, Loader=_VarsLoader) or {}
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

    def _set_agent_selection(
        self,
        agent_types: Any,
        default: Any = None,
    ) -> List[str]:
        """Persist the install set, attach default, and credential var names."""
        from cli.agents import apply_agent_selection

        agent_cfg = self.config.setdefault("agent", {})
        return apply_agent_selection(agent_cfg, agent_types, default=default)

    def _migrate_default_names(self) -> None:
        """Fold legacy agent/editor keys into ``default`` and ``enabled``."""
        if not isinstance(self.config, dict):
            return
        agent = self.config.get("agent")
        if isinstance(agent, dict):
            legacy = agent.get("type")
            if agent.get("default") not in (None, ""):
                if isinstance(legacy, str):
                    agent.pop("type", None)
            elif isinstance(legacy, str) and legacy.strip():
                agent["default"] = agent.pop("type")
            if not agent.get("enabled") and agent.get("types"):
                agent["enabled"] = agent.pop("types")
            elif agent.get("enabled"):
                agent.pop("types", None)

        raw_editor = self.config.get("editor")
        block = dict(raw_editor) if isinstance(raw_editor, dict) else {}
        legacy_name = raw_editor if isinstance(raw_editor, str) else None
        top_default = self.config.get("default_editor")
        top_enabled = self.config.get("editors")
        if block.get("default") in (None, ""):
            if top_default not in (None, ""):
                block["default"] = top_default
            elif isinstance(legacy_name, str) and legacy_name.strip():
                block["default"] = legacy_name
        if not block.get("enabled") and top_enabled:
            block["enabled"] = list(top_enabled)
        if block:
            self.config["editor"] = block
        self.config.pop("default_editor", None)
        self.config.pop("editors", None)

    def _migrate_host(self) -> None:
        """Rename ``vm`` to ``host`` and split the server size from ``type``."""
        if not isinstance(self.config, dict):
            return
        if "host" not in self.config and "vm" not in self.config:
            return
        self.config["host"] = host_block(self.config)
        self.config.pop("vm", None)

    def _selected_default_agent(self, selected: List[str]) -> str:
        from cli.agents import agent_default_field

        primary = agent_default_field(self.config.get("agent") or {})
        if isinstance(primary, str) and primary in selected:
            return primary
        return selected[0]

    def init(
        self,
        name: Optional[str] = None,
        ansible_enabled: bool = True,
        agent_type: Optional[str] = None,
        agent_types: Optional[List[str]] = None,
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
                self._migrate_default_names()
                self._migrate_host()
                before = yaml.dump(self.config, sort_keys=True)
                if name and not self.config.get("name"):
                    self.config["name"] = name
                self.config = self._merge_missing(self.config, defaults)
                # Prefer playbooks.system/workspace/authnz/finalizers over legacy bootstrap.playbook
                playbooks = self.config.setdefault("playbooks", {})
                playbooks.setdefault("provision", f"{self.RIG_DIR}/{self.PROVISION_YAML}")
                playbooks.setdefault("configure", f"{self.RIG_DIR}/{self.CONFIGURE_YAML}")
                playbooks.setdefault("authorize", f"{self.RIG_DIR}/{self.AUTHORIZE_YAML}")
                playbooks.setdefault("materialize", f"{self.RIG_DIR}/{self.MATERIALIZE_YAML}")
                playbooks.setdefault("finalize", f"{self.RIG_DIR}/{self.FINALIZE_YAML}")
                playbooks.setdefault("destroy", f"{self.RIG_DIR}/{self.DESTROY_YAML}")
                # Migrate legacy playbook keys if present.
                _legacy = {
                    "system": "provision",
                    "services": "configure",
                    "authnz": "authorize",
                    "secrets": "authorize",
                    "workspace": "materialize",
                    "finalizers": "finalize",
                    "teardown": "destroy",
                }
                for old_key, new_key in _legacy.items():
                    if old_key in playbooks and new_key not in playbooks:
                        playbooks[new_key] = playbooks.pop(old_key)
                    elif old_key in playbooks:
                        playbooks.pop(old_key, None)
                self.config.setdefault("inventory", f"{self.RIG_DIR}/inventory.yml")
                if yaml.dump(self.config, sort_keys=True) != before and self.RIG_YAML not in created:
                    created.append(f"{self.RIG_YAML} (updated)")

            from cli.agents import resolve_configured_agents
            if agent_types is not None or agent_type is not None:
                selected_agents = self._set_agent_selection(
                    agent_types if agent_types is not None else agent_type,
                    default=agent_type,
                )
            else:
                selected_agents, primary = resolve_configured_agents(
                    self.config.get("agent") or {}
                )
                selected_agents = self._set_agent_selection(
                    selected_agents, default=primary
                )

            enabled_editors, chosen_editor = resolve_configured_editors(self.config)
            if enabled_editors:
                apply_editor_selection(
                    self.config, enabled_editors, default=chosen_editor
                )

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
            if ensure_local_roles_dir(self.rig_dir):
                created.append(f"{self.RIG_DIR}/{self.LOCAL_ROLES_DIR}/.gitkeep")

            self._migrate_legacy_playbook_files()
            stage_paths = {
                "provision": os.path.join(self.rig_dir, self.PROVISION_YAML),
                "configure": os.path.join(self.rig_dir, self.CONFIGURE_YAML),
                "authorize": os.path.join(self.rig_dir, self.AUTHORIZE_YAML),
                "materialize": os.path.join(self.rig_dir, self.MATERIALIZE_YAML),
                "finalize": os.path.join(self.rig_dir, self.FINALIZE_YAML),
                "destroy": os.path.join(self.rig_dir, self.DESTROY_YAML),
            }
            missing = {k: not os.path.exists(p) for k, p in stage_paths.items()}

            if not self.save(write_vars=write_vars):
                return False
            # Managed playbooks always refresh; user playbooks only if missing.
            if not self.write_provision_playbook(force=True):
                return False
            if not self.write_configure_playbook(force=False):
                return False
            if not self.write_authorize_playbook(force=True):
                return False
            if not self.write_materialize_playbook(force=False):
                return False
            if not self.write_finalize_playbook(force=False):
                return False
            if not self.write_destroy_playbook(force=True):
                return False

            from cli.agents import ensure_agent_scaffold
            for scaffold_agent in selected_agents:
                for path in ensure_agent_scaffold(
                    self.rig_dir, agent_type=scaffold_agent
                ):
                    created.append(path)
            # Keep vars in sync for provision.yml conditionals.
            if self.vars_config is not None:
                default_agent = self._selected_default_agent(selected_agents)
                self.vars_config["coding_agent_type"] = default_agent
                self.vars_config["coding_agent_types"] = list(selected_agents)
                vars_path = os.path.join(self.rig_dir, self.VARS_YAML)
                if os.path.exists(vars_path) or write_vars:
                    disk = self._load_vars_with_vault(vars_path) if os.path.exists(vars_path) else {}
                    if disk:
                        self.vars_config = {**disk, **self.vars_config}
                    self.vars_config["coding_agent_type"] = default_agent
                    self.vars_config["coding_agent_types"] = list(selected_agents)
                    self._dump_vars(vars_path)

            for stage, was_missing in missing.items():
                if was_missing and os.path.exists(stage_paths[stage]):
                    created.append(
                        f"{self.RIG_DIR}/{os.path.basename(stage_paths[stage])}"
                    )

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

            for entry in ensure_project_gitignore(self.project_dir):
                created.append(f".gitignore ({entry})")

            if created:
                print("Ensured project files: " + ", ".join(dict.fromkeys(created)))
            else:
                print(f"Project already initialized at {self.project_dir}")
            return True
        except Exception as e:
            print(f"Error initializing project: {e}")
            return False

    def _migrate_legacy_playbook_files(self) -> None:
        """Rename legacy flat playbooks to the verb lifecycle names when needed."""
        mapping = (
            ("system.yml", self.PROVISION_YAML),
            ("services.yml", self.CONFIGURE_YAML),
            ("authnz.yml", self.AUTHORIZE_YAML),
            ("secrets.yml", self.AUTHORIZE_YAML),
            ("authenticate.yml", self.AUTHORIZE_YAML),
            ("workspace.yml", self.MATERIALIZE_YAML),
            ("populate.yml", self.MATERIALIZE_YAML),
            ("finalizers.yml", self.FINALIZE_YAML),
            ("cleanup.yml", self.FINALIZE_YAML),
            ("teardown.yml", self.DESTROY_YAML),
            ("bootstrap.yml", self.PROVISION_YAML),
            ("customize.yml", self.CONFIGURE_YAML),
        )
        for old_name, new_name in mapping:
            if old_name == new_name:
                continue
            old_path = os.path.join(self.rig_dir, old_name)
            new_path = os.path.join(self.rig_dir, new_name)
            if os.path.isfile(old_path) and not os.path.exists(new_path):
                try:
                    os.rename(old_path, new_path)
                except OSError:
                    pass

    def _write_playbook_file(self, filename: str, body: str, force: bool = False) -> bool:
        try:
            self._ensure_rig_dir()
            path = os.path.join(self.rig_dir, filename)
            if os.path.exists(path) and not force:
                return True
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body.lstrip("\n"))
            return True
        except Exception as e:
            print(f"Error writing {filename}: {e}")
            return False

    def write_provision_playbook(self, force: bool = False) -> bool:
        """Write auto-generated .rig/provision.yml when missing (or force)."""
        return self._write_playbook_file(self.PROVISION_YAML, PROVISION_PLAYBOOK, force=force)

    def write_configure_playbook(self, force: bool = False) -> bool:
        """Write .rig/configure.yml when missing (or force)."""
        return self._write_playbook_file(self.CONFIGURE_YAML, CONFIGURE_PLAYBOOK, force=force)

    def write_authorize_playbook(self, force: bool = False) -> bool:
        """Write auto-generated .rig/authorize.yml when missing (or force)."""
        return self._write_playbook_file(self.AUTHORIZE_YAML, AUTHORIZE_PLAYBOOK, force=force)

    def write_materialize_playbook(self, force: bool = False) -> bool:
        """Write .rig/materialize.yml when missing (or force)."""
        return self._write_playbook_file(self.MATERIALIZE_YAML, MATERIALIZE_PLAYBOOK, force=force)

    def write_finalize_playbook(self, force: bool = False) -> bool:
        """Write .rig/finalize.yml when missing (or force)."""
        return self._write_playbook_file(self.FINALIZE_YAML, FINALIZE_PLAYBOOK, force=force)

    def write_destroy_playbook(self, force: bool = False) -> bool:
        """Write auto-generated .rig/destroy.yml when missing (or force)."""
        return self._write_playbook_file(self.DESTROY_YAML, DESTROY_PLAYBOOK, force=force)

    def write_init_playbook(self) -> bool:
        """Back-compat alias for write_provision_playbook."""
        return self.write_provision_playbook(force=True)

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

    The environment root is always ``start`` (cwd), including when that path is
    a subdirectory of a git repo. Nearby sibling git projects are still offered
    for inclusion. Outside a git repo, scan start itself.
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
            "root": start,
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
        agent_types: Optional[List[str]] = None,
        editor: Optional[str] = None,
        editors: Optional[List[str]] = None,
    ) -> bool:
        """Idempotently initialize workspace config and register selected repos."""
        workspace_name = name or os.path.basename(self.path.rstrip(os.sep)) or "workspace"
        if not _valid_component_name(workspace_name):
            print(f"Error: invalid workspace name '{workspace_name}'")
            return False

        selected = repos or []
        password_vars = password_vars or {}
        from cli.agents import coerce_agent_types, normalize_agent_type
        chosen_agents = coerce_agent_types(
            agent_types if agent_types is not None else (agent_type or "opencode")
        ) or ["opencode"]
        chosen_agent = chosen_agents[0]
        if isinstance(agent_type, str) and agent_type.strip():
            try:
                candidate = normalize_agent_type(agent_type)
            except ValueError:
                candidate = None
            if candidate in chosen_agents:
                chosen_agent = candidate
        raw_editors: Any = editors if editors is not None else (editor or DEFAULT_EDITOR)
        try:
            chosen_editors = coerce_editor_types(raw_editors) or [DEFAULT_EDITOR]
        except ValueError:
            chosen_editors = [DEFAULT_EDITOR]
        chosen_editor = chosen_editors[0]
        if isinstance(editor, str) and editor.strip():
            try:
                editor_candidate = normalize_editor_type(editor)
            except ValueError:
                editor_candidate = None
            if editor_candidate in chosen_editors:
                chosen_editor = editor_candidate

        project = ProjectConfig(self.path)
        # Pass agents into init so scaffold/config persist before add_repo reloads.
        if not project.init(
            name=workspace_name,
            ansible_enabled=True,
            agent_type=chosen_agent,
            agent_types=chosen_agents,
        ):
            return False

        project.config["kind"] = "workspace"
        project.config["name"] = workspace_name
        project._set_agent_selection(chosen_agents, default=chosen_agent)
        apply_editor_selection(project.config, chosen_editors, default=chosen_editor)
        project.set_vars("coding_agent_type", chosen_agent)
        project.set_vars("coding_agent_types", list(chosen_agents))

        existing_repos = [
            repo for repo in (project.config.get("repos") or [])
            if isinstance(repo, dict) and repo.get("name")
        ]
        existing_by_name = {repo["name"]: dict(repo) for repo in existing_repos}
        by_name: Dict[str, Dict[str, Any]] = {}

        from cli.git import GitManager
        git_manager = GitManager(project)
        # Avoid wiping vaulted vars.yml on re-init when clone list already exists.
        if project.get_vars("git.clone_repos") is None:
            project.set_vars("git.clone_repos", [])
        if project.get_vars("git.ssh_repos") is None:
            project.set_vars("git.ssh_repos", [])

        from cli.encrypt import to_https_git_url

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
            entry: Dict[str, Any] = dict(
                existing_by_name.get(repo_name) or {"name": repo_name}
            )
            if relative and relative not in (".", ""):
                entry["path"] = relative.replace(os.sep, "/")
            if url:
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

        kept_names = set(by_name)
        kept_urls = set()
        for entry in by_name.values():
            url = entry.get("url")
            if not url:
                continue
            kept_urls.add(str(url))
            kept_urls.add(to_https_git_url(str(url)))

        git_config = project.get_vars("git") or {}
        for key in ("clone_repos", "ssh_repos"):
            filtered: List[Dict[str, Any]] = []
            for item in git_config.get(key, []) or []:
                if not isinstance(item, dict):
                    continue
                repo_url = item.get("repo")
                dest = str(item.get("dest") or "")
                dest_name = dest.rstrip("/").split("/")[-1] if dest else ""
                if repo_url in kept_urls or dest_name in kept_names:
                    filtered.append(item)
            git_config[key] = filtered
        project.set_vars("git", git_config)
        git_manager.repos_to_clone = git_config.get("clone_repos", [])
        git_manager.ssh_repos = git_config.get("ssh_repos", [])

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
        # Re-apply after add_repo/load cycles so agent/editor choice is not lost.
        project._set_agent_selection(chosen_agents, default=chosen_agent)
        apply_editor_selection(project.config, chosen_editors, default=chosen_editor)
        project.set_vars("coding_agent_type", chosen_agent)
        project.set_vars("coding_agent_types", list(chosen_agents))

        if not project.save(write_vars=False):
            return False
        # Refresh auto-generated playbooks so provider CLI / secrets tasks match.
        if not project.write_provision_playbook(force=True):
            return False
        if not project.write_authorize_playbook(force=True):
            return False

        print(f"Initialized environment '{workspace_name}' at {self.path}")
        if by_name:
            print("Included projects: " + ", ".join(sorted(by_name)))
        if providers:
            print("Git providers: " + ", ".join(providers))
        if len(chosen_agents) == 1:
            print(f"Coding agent: {chosen_agent}")
        else:
            print(
                "Coding agents: "
                + ", ".join(chosen_agents)
                + f" (attach default: {chosen_agent})"
            )
        if len(chosen_editors) == 1:
            print(f"Editor: {chosen_editor}")
        else:
            print(
                "Editors: "
                + ", ".join(chosen_editors)
                + f" (dev default: {chosen_editor})"
            )
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
    from cli.agents import (
        prompt_coding_agents,
        prompt_default_agent,
        resolve_configured_agents,
    )
    from cli.encrypt import (
        collect_repo_pats,
        prompt_and_store_coding_agent_credentials,
        prompt_and_store_coding_agents_credentials,
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
        print(
            "Existing rig.yml found — reinitializing; selected projects replace "
            "the current repo set (other files stay additive)."
        )

    # Identity + master-key wraps before any vaulted secret prompts.
    # If wraps already exist, unlock into /tmp/.rig (no re-bootstrap).
    try:
        from cli.vault_keys import (
            bootstrap_identity_keys,
            personal_vars_path,
            read_wrap_fields,
            resolve_identity,
            tmp_data_keys_present,
            unlock_data_keys,
        )
        from cli.config import RigConfig as _RigConfig

        cfg = _RigConfig(path=config_path)
        ident = resolve_identity(config=cfg)
        wraps = read_wrap_fields(personal_vars_path(root, ident))
        if wraps:
            if not cfg.get_identity():
                cfg.set_identity(ident)
            if not tmp_data_keys_present():
                unlock_data_keys(
                    root,
                    identity=ident,
                    input_fn=input_fn,
                    secret_fn=secret_fn,
                    require_wraps=True,
                )
        else:
            bootstrap_identity_keys(
                root,
                identity=ident if cfg.get_identity() else None,
                input_fn=input_fn,
                secret_fn=secret_fn,
                config=cfg,
            )
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return False

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

    existing_agents, existing_default = resolve_configured_agents(
        existing.get("agent") or {}
    )
    try:
        agent_types = prompt_coding_agents(
            default=existing_agents, input_fn=input_fn
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return False
    if isinstance(agent_types, str):
        from cli.agents import coerce_agent_types
        agent_types = coerce_agent_types(agent_types) or ["opencode"]
    try:
        preferred = existing_default if existing_default in agent_types else agent_types[0]
        agent_type = prompt_default_agent(
            agent_types, default=preferred, input_fn=input_fn
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return False

    existing_editors, existing_editor = resolve_configured_editors(existing)
    if not existing_editors:
        existing_editors = [DEFAULT_EDITOR]
        existing_editor = DEFAULT_EDITOR
    try:
        editors = prompt_editors(default=existing_editors, input_fn=input_fn)
    except ValueError as exc:
        print(f"Error: {exc}")
        return False
    if isinstance(editors, str):
        try:
            editors = coerce_editor_types(editors) or [DEFAULT_EDITOR]
        except ValueError:
            editors = [DEFAULT_EDITOR]
    try:
        preferred_editor = (
            existing_editor if existing_editor in editors else editors[0]
        )
        editor = prompt_default_editor(
            editors, default=preferred_editor, input_fn=input_fn
        )
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
        agent_types=agent_types,
        editor=editor,
        editors=editors,
    ):
        return False

    try:
        store_repo_pats(
            pats_to_store,
            project_dir=root,
            data_key_path=data_key_path,
            input_fn=input_fn,
            secret_fn=secret_fn,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Warning: could not store git PATs: {exc}")
    stored_by_agent: Dict[str, bool] = {}
    try:
        if len(agent_types) == 1:
            stored_by_agent[agent_types[0]] = bool(
                prompt_and_store_coding_agent_credentials(
                    project_dir=root,
                    data_key_path=data_key_path,
                    input_fn=input_fn,
                    secret_fn=secret_fn,
                    agent_type=agent_types[0],
                )
            )
        else:
            stored_by_agent = {
                str(key): bool(value)
                for key, value in prompt_and_store_coding_agents_credentials(
                    agent_types,
                    project_dir=root,
                    data_key_path=data_key_path,
                    input_fn=input_fn,
                    secret_fn=secret_fn,
                ).items()
            }
    except (ValueError, RuntimeError) as exc:
        print(f"Warning: could not store coding agent API keys: {exc}")

    try:
        from cli.encrypt import (
            VSCODE_BROWSER_PASSWORD_VAR,
            ensure_vscode_browser_password,
        )
        if ensure_vscode_browser_password(
            project_dir=root,
            data_key_path=data_key_path,
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
        from cli.agents import sync_opencode_config
        if "opencode" in agent_types:
            has_opencode_key = bool(stored_by_agent.get("opencode"))
            written = sync_opencode_config(
                os.path.join(root, ".rig"),
                has_api_key=has_opencode_key,
                force=has_opencode_key,
            )
            if has_opencode_key and written:
                key_name = (
                    "opencode_api_key"
                    if len(agent_types) > 1
                    else "coding_agent_api_key"
                )
                print(f"OpenCode config set to use {key_name}")
            elif not has_opencode_key:
                print(
                    "OpenCode config uses free models "
                    "(set an OpenCode API key to use a paid provider)"
                )
    except ValueError:
        pass
    return True


# =============================================================================
# ANSIBLE INTEGRATION CLASS
# =============================================================================

_PLAYBOOK_SECTION_RE = re.compile(r"^(?:PLAY RECAP|PLAY|TASK)\b")
_PLAYBOOK_FAILURE_RE = re.compile(
    r"^(?:fatal:|failed:|unreachable:)|FAILED!|UNREACHABLE!"
)


def ansible_verbosity_args() -> List[str]:
    """Ansible ``-v`` flags for the current rig verbosity.

    ``-v`` streams the normal ansible task log. Each further ``v`` is passed
    through (``-vv`` → ansible ``-v``, up to ansible ``-vvvvvv``).
    """
    extra = log.verbosity() - 1
    if extra <= 0:
        return []
    return ["-" + ("v" * min(extra, 6))]


def playbook_failure_excerpt(output: str) -> str:
    """Keep the failed task blocks and the recap from an ansible run.

    Successful tasks stay out of the default log. Output that has no task
    markers is returned whole so an unrecognized failure is still visible.
    """
    if not output or not output.strip():
        return ""
    lines = output.splitlines(keepends=True)
    sections: List[List[str]] = []
    current: List[str] = []
    for line in lines:
        if _PLAYBOOK_SECTION_RE.match(line) and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)

    chosen: List[List[str]] = []
    saw_header = False
    for section in sections:
        head = section[0].lstrip()
        if _PLAYBOOK_SECTION_RE.match(head):
            saw_header = True
        if head.startswith("PLAY RECAP"):
            chosen.append(section)
            continue
        if any(
            _PLAYBOOK_FAILURE_RE.search(part.lstrip())
            or part.lstrip().startswith("[ERROR]")
            or part.lstrip().startswith("ERROR!")
            for part in section
        ):
            chosen.append(section)
    if not chosen or not saw_header:
        return output
    # A recap with no recognized task still belongs to the last task.
    if all(section[0].lstrip().startswith("PLAY RECAP") for section in chosen):
        tasks = [section for section in sections if section[0].lstrip().startswith("TASK")]
        if tasks:
            chosen.insert(0, tasks[-1])
    return "".join("".join(section) for section in chosen)


class AnsibleManager:
    """Manages Ansible playbook execution for project bootstrap"""

    def __init__(self, project_config: Optional["ProjectConfig"] = None):
        self.project_config: Optional["ProjectConfig"] = project_config
        self.playbook_path: Optional[str] = None
        self.configure_playbook_path: Optional[str] = None
        self.authorize_playbook_path: Optional[str] = None
        self.materialize_playbook_path: Optional[str] = None
        self.finalize_playbook_path: Optional[str] = None
        self.destroy_playbook_path: Optional[str] = None
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

            # Playbook paths from root rig.yml lifecycle stages.
            project_dir = self.project_config.project_dir
            playbooks = self.project_config.config.get("playbooks") or {}

            def _stage_rel(stage: str, filename: str, *legacy_keys: str) -> str:
                if playbooks.get(stage):
                    return playbooks[stage]
                for key in legacy_keys:
                    if playbooks.get(key):
                        return playbooks[key]
                return f".rig/{filename}"

            provision_rel = _stage_rel(
                "provision", ProjectConfig.PROVISION_YAML, "system", "bootstrap"
            )
            configure_rel = _stage_rel(
                "configure", ProjectConfig.CONFIGURE_YAML, "services", "customize"
            )
            authorize_rel = _stage_rel(
                "authorize",
                ProjectConfig.AUTHORIZE_YAML,
                "authnz",
                "secrets",
                "authenticate",
            )
            materialize_rel = _stage_rel(
                "materialize",
                ProjectConfig.MATERIALIZE_YAML,
                "workspace",
                "populate",
            )
            finalize_rel = _stage_rel(
                "finalize", ProjectConfig.FINALIZE_YAML, "finalizers", "cleanup"
            )
            destroy_rel = _stage_rel(
                "destroy", ProjectConfig.DESTROY_YAML, "teardown"
            )
            inventory_rel = (
                self.project_config.config.get("inventory")
                or f".rig/inventory.yml"
            )

            def _resolve(rel: str) -> str:
                return rel if os.path.isabs(rel) else os.path.join(project_dir, rel)

            self.playbook_path = _resolve(provision_rel)
            self.configure_playbook_path = _resolve(configure_rel)
            self.authorize_playbook_path = _resolve(authorize_rel)
            self.materialize_playbook_path = _resolve(materialize_rel)
            self.finalize_playbook_path = _resolve(finalize_rel)
            self.destroy_playbook_path = _resolve(destroy_rel)
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

            log.debug("Inventory created at %s", self.inventory_path)
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

            log.debug("Requirements created at %s", requirements_path)
            return True
        except Exception as e:
            print(f"Error creating requirements: {e}")
            return False

    def setup_roles(self) -> bool:
        """Ensure the committed role directory and the Galaxy install directories."""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False
            rig_dir = self.project_config.rig_dir
            for name in (LOCAL_ROLES_DIRNAME, ROLES_DIRNAME):
                os.makedirs(os.path.join(rig_dir, name), exist_ok=True)
            ensure_local_roles_dir(rig_dir)
            migrate_galaxy_roles_dir(rig_dir)
            link_local_roles(rig_dir)
            log.debug(
                "Roles search path: %s",
                roles_search_path(rig_dir),
            )
            return True
        except Exception as e:
            log.error("Error setting up roles: %s", e)
            return False

    def setup_playbook(self, custom_tasks: Optional[list] = None) -> bool:
        """Ensure lifecycle playbooks exist; optional custom tasks become materialize.yml."""
        try:
            if self.project_config is None:
                print("Error: project_config is not set")
                return False

            # Managed playbooks — refresh so fixes ship on apply/create.
            if not self.project_config.write_provision_playbook(force=True):
                return False
            if not self.project_config.write_configure_playbook(force=False):
                return False
            if not self.project_config.write_authorize_playbook(force=True):
                return False
            if not self.project_config.write_destroy_playbook(force=True):
                return False

            if not self.configure(self.project_config.project_dir):
                return False

            if custom_tasks:
                materialize_path = self.materialize_playbook_path
                if not materialize_path:
                    return False
                playbook = [{
                    'name': 'Materialize workspace',
                    'hosts': 'remote_vm',
                    'gather_facts': False,
                    'remote_user': 'root',
                    'tasks': custom_tasks,
                }]
                with open(materialize_path, 'w', encoding='utf-8') as handle:
                    yaml.dump(playbook, handle, default_flow_style=False, sort_keys=False)
                # One-off / test runs execute the materialize playbook directly.
                self.playbook_path = materialize_path
            else:
                if not self.project_config.write_materialize_playbook(force=False):
                    return False

            log.debug("Provision playbook: %s", self.playbook_path)
            log.debug("Materialize playbook: %s", self.materialize_playbook_path)
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

            rig_dir = self.project_config.rig_dir
            roles_path = os.path.join(rig_dir, ROLES_DIRNAME)
            collections_path = os.path.join(rig_dir, 'collections')
            os.makedirs(roles_path, exist_ok=True)
            os.makedirs(collections_path, exist_ok=True)
            migrate_galaxy_roles_dir(rig_dir)
            unlink_local_role_shadows(rig_dir)
            self.ansible_env['ANSIBLE_ROLES_PATH'] = roles_search_path(rig_dir)
            self.ansible_env['ANSIBLE_COLLECTIONS_PATH'] = collections_path

            try:
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
            finally:
                link_local_roles(rig_dir)

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
        for path in (self.playbook_path, self.materialize_playbook_path):
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
            (
                "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                "-o IdentitiesOnly=yes "
                f"-o LogLevel={ssh_option_log_level()}"
            )
        ]

        if self.project_config is None:
            log.error("project_config is not set")
            return False

        # Shared + personal vars (personal wins). Strip master-key wraps for ansible.
        from cli.vault_keys import (
            PERSONAL_DATA_KEY_PATH,
            SHARED_DATA_KEY_PATH,
            personal_vars_path,
            unlock_data_keys,
            write_filtered_vars_copy,
        )

        project_dir = self.project_config.project_dir
        try:
            # Prefer master-key wraps; fall back for legacy/test workspaces.
            unlock_data_keys(project_dir, require_wraps=False)
        except ValueError as exc:
            log.error("%s", exc)
            return False

        shared_vars = os.path.join(self.project_config.rig_dir, ProjectConfig.VARS_YAML)
        identity_vars = personal_vars_path(project_dir)
        tmp_dir = os.path.join("/tmp", ".rig", "ansible-vars")
        os.makedirs(tmp_dir, mode=0o700, exist_ok=True)
        vars_path = shared_vars
        if os.path.exists(shared_vars):
            filtered_shared = write_filtered_vars_copy(
                shared_vars,
                os.path.join(tmp_dir, "vars.yml"),
            )
            if filtered_shared:
                cmd.extend(["-e", f"@{filtered_shared}"])
                vars_path = filtered_shared
        if os.path.exists(identity_vars):
            filtered_identity = write_filtered_vars_copy(
                identity_vars,
                os.path.join(tmp_dir, os.path.basename(identity_vars)),
            )
            # Wraps-only identity files filter to empty; skip -e @file (Ansible
            # rejects empty extra-vars files as invalid dictionaries).
            if filtered_identity:
                cmd.extend(["-e", f"@{filtered_identity}"])
                vars_path = filtered_identity

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

        if os.path.exists(SHARED_DATA_KEY_PATH):
            cmd.extend(["--vault-id", f"shared@{SHARED_DATA_KEY_PATH}"])
        if os.path.exists(PERSONAL_DATA_KEY_PATH):
            cmd.extend(["--vault-id", f"personal@{PERSONAL_DATA_KEY_PATH}"])
        if vault_password and not (
            os.path.exists(SHARED_DATA_KEY_PATH) or os.path.exists(PERSONAL_DATA_KEY_PATH)
        ):
            cmd.extend(["--vault-password", vault_password])
        elif self.vault_password and not (
            os.path.exists(SHARED_DATA_KEY_PATH) or os.path.exists(PERSONAL_DATA_KEY_PATH)
        ):
            cmd.extend(["--vault-password", self.vault_password])

        rig_dir = self.project_config.rig_dir
        collections_path = os.path.join(rig_dir, 'collections')
        link_local_roles(rig_dir)
        self.ansible_env['ANSIBLE_ROLES_PATH'] = roles_search_path(rig_dir)
        self.ansible_env['ANSIBLE_COLLECTIONS_PATH'] = collections_path
        cmd.extend(ansible_verbosity_args())

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

        def _failure_detail(text: str) -> str:
            excerpt = self._redact_secrets(playbook_failure_excerpt(text)).strip()
            return f"\n{excerpt}" if excerpt else ""

        if stream:
            # Default runs stay on lifecycle lines. -v streams each task.
            show_tasks = log.verbosity() >= 1
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
                if show_tasks:
                    print(line, end="")
                collected.append(line)
            returncode = process.wait(timeout=1800)
            output = "".join(collected)
            if returncode != 0:
                log.error(
                    "Playbook failed (%s): %s%s",
                    returncode,
                    playbook_path,
                    _failure_detail(output),
                )
                return False
            if _output_indicates_no_hosts(output):
                log.error(
                    "Playbook matched no hosts (inventory/vars issue): %s%s",
                    playbook_path,
                    _failure_detail(output),
                )
                return False
            log.debug("Playbook succeeded: %s", playbook_path)
            return True

        result = subprocess.run(cmd, env=self.ansible_env, capture_output=True, text=True, timeout=600)
        combined = f"{result.stdout or ''}\n{result.stderr or ''}"
        if result.returncode == 0 and not _output_indicates_no_hosts(combined):
            log.debug("Playbook succeeded: %s", playbook_path)
            if log.verbosity() >= 1 and result.stdout:
                log.debug("STDOUT:\n%s", self._redact_secrets(result.stdout))
            return True

        log.error(
            "Playbook failed (return code %s): %s%s",
            result.returncode,
            playbook_path,
            _failure_detail(combined),
        )
        if _output_indicates_no_hosts(combined):
            log.error("No hosts matched — check inventory.yml parses and lists remote_vm")
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
