"""Hetzner VM snapshot naming and content hashing for rig cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from typing import Any, Optional

from cli import DISTRIBUTION_NAME

SYSTEM_LABEL = "system"
WORKSPACE_LABEL = "workspace"
RIG_LABEL_KEY = "rig"
RIG_WORKSPACE_LABEL_KEY = "rig_workspace"
RIG_VERSION_LABEL_KEY = "rig_version"
RIG_HASH_LABEL_KEY = "rig_hash"
# Optional metadata labels (not used for matching; hash is authoritative).
RIG_AGENT_LABEL_KEY = "rig_agent"

# ---------------------------------------------------------------------------
# Variable maps: only these project/vars keys influence snapshot identity.
# Secrets (PATs, API keys, vault blobs) are applied by authorize.yml and must
# NOT appear here — changing secrets must not invalidate system snapshots;
# re-run authorize instead.
# ---------------------------------------------------------------------------

# Values that change what system.yml installs (agent CLIs, gh/glab, code-server).
SYSTEM_SNAPSHOT_VAR_KEYS: tuple[str, ...] = (
    "coding_agent_type",
    "coding_agent_types",
    "git_providers",
    "code_server_port",
    "rig_user",
    "workspace_root",
    "install_docker",
)

# Values that change what workspace.yml configures (clone layout, root path).
# ``git`` is the clone_repos structure (repo URLs / dests / password_var *names*
# only — not secret values).
WORKSPACE_SNAPSHOT_VAR_KEYS: tuple[str, ...] = (
    "workspace_root",
    "git",
)


def package_version() -> str:
    """Installed package version (fallback ``0.0.0``)."""
    try:
        from importlib.metadata import version as pkg_version

        return pkg_version(DISTRIBUTION_NAME)
    except Exception:
        try:
            from importlib_metadata import version as pkg_version_fallback

            return pkg_version_fallback(DISTRIBUTION_NAME)
        except Exception:
            return "0.0.0"


def sanitize_snapshot_token(value: str, *, max_len: int = 48) -> str:
    """Keep Hetzner-friendly description tokens (alnum, dash, dot, underscore)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return (cleaned or "unnamed")[:max_len]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _project_merged_vars(project) -> dict[str, Any]:
    """Merge rig.yml + vars for snapshot map extraction (no personal vault files)."""
    cfg = dict(getattr(project, "config", None) or {})
    vars_cfg = dict(getattr(project, "vars_config", None) or {})
    merged = {**vars_cfg, **{k: v for k, v in cfg.items() if k not in ("playbooks",)}}
    # Prefer agent.default / agent.enabled over the vars copies when both exist.
    agent = cfg.get("agent") or {}
    if isinstance(agent, dict):
        default_agent = agent.get("default")
        if default_agent in (None, "") or isinstance(default_agent, (list, tuple)):
            default_agent = agent.get("type")
        if default_agent and not isinstance(default_agent, (list, tuple)):
            merged["coding_agent_type"] = default_agent
        enabled_agents = agent.get("enabled") or agent.get("types")
        if enabled_agents:
            merged["coding_agent_types"] = list(enabled_agents)
    if "git_providers" not in merged and cfg.get("git_providers"):
        merged["git_providers"] = cfg["git_providers"]
    return merged


def extract_snapshot_var_map(
    project,
    keys: Iterable[str],
) -> dict[str, Any]:
    """Pull the declared snapshot keys from a loaded project."""
    merged = _project_merged_vars(project)
    out: dict[str, Any] = {}
    for key in keys:
        if key == "rig_user":  # the workspace user lives in rig.yml (host.user), not the vars
            from cli.workspace_user import workspace_user

            host = (getattr(project, "config", None) or {}).get("host")
            out[key] = workspace_user(host if isinstance(host, dict) else None)
            continue
        if key not in merged:
            continue
        value = merged[key]
        # Stabilize list order for providers / similar bags of strings.
        if key in ("git_providers", "coding_agent_types") and isinstance(value, list):
            value = sorted({str(p).strip().lower() for p in value if str(p).strip()})
        out[key] = value
    return out


def system_hash_inputs_from_project(project) -> dict[str, Any]:
    """Backward-compatible alias: system var map (+ agent for labels)."""
    var_map = extract_snapshot_var_map(project, SYSTEM_SNAPSHOT_VAR_KEYS)
    types = var_map.get("coding_agent_types")
    if isinstance(types, list) and len(types) > 1:
        agent_label = "+".join(str(item) for item in types)
    else:
        agent_label = str(var_map.get("coding_agent_type") or "opencode")
    return {
        "var_map": var_map,
        "agent_type": agent_label,
    }


def _hash_bytes(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


BUNDLED_ROLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles")

# Role task files for later Stages. They are never part of a cached image, so editing
# them must not discard it.
_UNCACHED_ROLE_TASKS = ("authorize", "materialize", "destroy")


def _role_files(role_dir: str, *, cached_only: bool) -> list[str]:
    """Relative paths of a role's files, sorted; optionally without the uncached Stages' tasks."""
    found: list[str] = []
    for base, _dirs, names in os.walk(role_dir):
        for name in names:
            rel = os.path.relpath(os.path.join(base, name), role_dir).replace(os.sep, "/")
            stem = rel.split("/", 1)[1].split(".", 1)[0] if rel.startswith("tasks/") else ""
            if cached_only and stem.startswith(_UNCACHED_ROLE_TASKS):
                continue
            found.append(rel)
    return sorted(found)


def bundled_roles_digest(project_dir: str | None = None) -> bytes:
    """
    Digest of the bundled roles whose tasks end up in the cached image.

    A role counts when it has ``tasks/install.yml`` (the provision entry point).
    Its authorize/materialize/destroy task files are left out. A Workspace role in
    ``.rig/local_roles`` with the same name shadows the bundled one, so it is hashed too.
    """
    digest = hashlib.sha256()
    if not os.path.isdir(BUNDLED_ROLES_DIR):
        return b""
    for role in sorted(os.listdir(BUNDLED_ROLES_DIR)):
        role_dir = os.path.join(BUNDLED_ROLES_DIR, role)
        if not os.path.isfile(os.path.join(role_dir, "tasks", "install.yml")):
            continue
        sources = [("bundled", role_dir)]
        if project_dir:
            shadow = os.path.join(project_dir, ".rig", "local_roles", role)
            if os.path.isdir(shadow):
                sources.append(("local", shadow))
        for origin, root in sources:
            for rel in _role_files(root, cached_only=origin == "bundled"):
                digest.update(f"{origin}:{role}/{rel}".encode())
                digest.update(b"\0")
                with open(os.path.join(root, rel), "rb") as handle:
                    digest.update(handle.read())
                digest.update(b"\0")
    return digest.digest()


def system_content_hash(
    project=None,
    *,
    var_map: dict[str, Any] | None = None,
    playbook_body: str | None = None,
    project_dir: str | None = None,
    # Legacy kwargs kept for older call sites / tests:
    agent_type: str | None = None,
    editor: str | None = None,
    git_providers: Iterable[str] | None = None,
) -> str:
    """
    Hash for system snapshots: var map + ``provision.yml`` + ``configure.yml``
    + ``requirements.yml`` (which pins Galaxy role/collection versions).

    The role directories (``.rig/local_roles``, ``.rig/roles``) are deliberately
    not hashed: roles are mostly used from ``materialize.yml``, which no
    snapshot caches, so editing one must not discard the cached image.
    ``editor`` is ignored (local IDE only; not installed by system.yml).
    """
    if var_map is None and project is not None:
        var_map = extract_snapshot_var_map(project, SYSTEM_SNAPSHOT_VAR_KEYS)
    if var_map is None:
        var_map = {}
        if agent_type:
            var_map["coding_agent_type"] = agent_type
        if git_providers is not None:
            var_map["git_providers"] = list(git_providers)

    body = playbook_body
    configure_body = ""
    root = project_dir
    if root is None and project is not None:
        root = getattr(project, "project_dir", None)
    if body is None:
        if root:
            body = _read_text(os.path.join(root, ".rig", "provision.yml"))
            if not body:
                body = _read_text(os.path.join(root, ".rig", "system.yml"))
            configure_body = _read_text(os.path.join(root, ".rig", "configure.yml"))
            if not configure_body:
                configure_body = _read_text(os.path.join(root, ".rig", "services.yml"))
        if not body:
            try:
                from cli.remote_dev import PROVISION_PLAYBOOK

                body = PROVISION_PLAYBOOK
            except Exception:
                body = ""
        if not configure_body:
            try:
                from cli.remote_dev import CONFIGURE_PLAYBOOK

                configure_body = CONFIGURE_PLAYBOOK
            except Exception:
                configure_body = ""

    requirements = _read_text(os.path.join(root, ".rig", "requirements.yml")) if root else ""
    skip_tags: list[str] = []
    if project is not None:
        from cli.remote_dev import configured_skip_tags

        skip_tags = sorted(set(configured_skip_tags(getattr(project, "config", None))))
    parts = [
        b"system-vars",
        _canonical_json(var_map).encode("utf-8"),
        b"provision-playbook",
        (body or "").encode("utf-8"),
        b"configure-playbook",
        (configure_body or "").encode("utf-8"),
    ]
    if requirements:
        parts.extend([b"requirements", requirements.encode("utf-8")])
    parts.extend([b"bundled-roles", bundled_roles_digest(root)])
    if skip_tags:
        # A skip list builds a different image, so it gets its own cache entry.
        parts.extend([b"skip-tags", ",".join(skip_tags).encode("utf-8")])
    return _hash_bytes(*parts)


def iter_workspace_hash_paths(project_dir: str) -> list[str]:
    """
    Files that feed the workspace snapshot hash (for tests / debugging).

    Includes ``materialize.yml`` and shared ``vars.yml`` only (legacy helper;
    materialize-tier snapshots are no longer created). Personal identity vars
    and ``authorize.yml`` / ``provision.yml`` are excluded.
    """
    project_dir = os.path.abspath(project_dir)
    paths: list[str] = []
    for rel in (".rig/materialize.yml", ".rig/vars.yml"):
        path = os.path.join(project_dir, rel)
        if os.path.isfile(path):
            paths.append(path)
    return paths


def workspace_content_hash(project_dir: str, project=None) -> str:
    """
    Legacy hash helper for materialize content + shared vars.

    New creates no longer mint workspace-tier snapshots; kept for cache listing
    of older ``workspace-*`` images.
    """
    project_dir = os.path.abspath(project_dir)
    if project is None:
        try:
            from cli.remote_dev import ProjectConfig

            project = ProjectConfig(project_dir)
            project.load()
        except Exception:
            project = None

    var_map: dict[str, Any] = {}
    if project is not None:
        var_map = extract_snapshot_var_map(project, WORKSPACE_SNAPSHOT_VAR_KEYS)
        # Name from rig.yml is part of workspace identity for clones/layout.
        cfg = getattr(project, "config", None) or {}
        if cfg.get("name"):
            var_map = {**var_map, "name": cfg["name"]}
        if cfg.get("repos") is not None:
            var_map = {**var_map, "repos": cfg["repos"]}

    materialize_yml = _read_text(os.path.join(project_dir, ".rig", "materialize.yml"))
    if not materialize_yml:
        materialize_yml = _read_text(os.path.join(project_dir, ".rig", "workspace.yml"))
    vars_yml = _read_text(os.path.join(project_dir, ".rig", "vars.yml"))

    return _hash_bytes(
        b"workspace-vars",
        _canonical_json(var_map).encode("utf-8"),
        b"materialize-playbook",
        materialize_yml.encode("utf-8"),
        b"shared-vars",
        vars_yml.encode("utf-8"),
    )


def system_snapshot_name(
    version: str | None = None,
    content_hash: str | None = None,
    **kwargs: Any,
) -> str:
    """Snapshot description after configure (build layer): ``rig-{version}-{hash}``."""
    ver = sanitize_snapshot_token(version or package_version(), max_len=32)
    digest = content_hash or system_content_hash(**kwargs)
    digest = sanitize_snapshot_token(digest, max_len=16)
    return f"rig-{ver}-{digest}"


def workspace_snapshot_name(workspace_name: str, content_hash: str) -> str:
    """Snapshot description after workspace.yml: ``workspace-{name}-{hash}``."""
    name = sanitize_snapshot_token(workspace_name, max_len=40)
    digest = sanitize_snapshot_token(content_hash, max_len=16)
    return f"workspace-{name}-{digest}"


def system_snapshot_labels(
    version: str | None = None,
    content_hash: str | None = None,
    *,
    agent_type: str | None = None,
    **kwargs: Any,
) -> dict[str, str]:
    ver = sanitize_snapshot_token(version or package_version(), max_len=32)
    digest = content_hash or system_content_hash(agent_type=agent_type, **kwargs)
    labels = {
        RIG_LABEL_KEY: SYSTEM_LABEL,
        RIG_VERSION_LABEL_KEY: ver,
        RIG_HASH_LABEL_KEY: sanitize_snapshot_token(digest, max_len=16),
    }
    if agent_type:
        labels[RIG_AGENT_LABEL_KEY] = sanitize_snapshot_token(str(agent_type), max_len=16)
    return labels


def workspace_snapshot_labels(workspace_name: str, content_hash: str) -> dict[str, str]:
    return {
        RIG_LABEL_KEY: WORKSPACE_LABEL,
        RIG_WORKSPACE_LABEL_KEY: sanitize_snapshot_token(workspace_name, max_len=40),
        RIG_HASH_LABEL_KEY: sanitize_snapshot_token(content_hash, max_len=16),
    }


def is_system_snapshot(
    image: dict[str, Any],
    version: str | None = None,
    content_hash: str | None = None,
) -> bool:
    labels = image.get("labels") or {}
    description = image.get("description") or ""
    if labels.get(RIG_LABEL_KEY) == SYSTEM_LABEL:
        if version is None and content_hash is None:
            return True
        if version is not None and labels.get(RIG_VERSION_LABEL_KEY) != sanitize_snapshot_token(version, max_len=32):
            return False
        if content_hash is not None and labels.get(RIG_HASH_LABEL_KEY) != sanitize_snapshot_token(
            content_hash, max_len=16
        ):
            return False
        return True
    expected = system_snapshot_name(version, content_hash=content_hash) if version and content_hash else None
    if expected:
        return description == expected
    return bool(re.match(r"^rig-[A-Za-z0-9._-]+$", description))


def is_workspace_snapshot(
    image: dict[str, Any],
    workspace_name: str | None = None,
) -> bool:
    labels = image.get("labels") or {}
    description = image.get("description") or ""
    if labels.get(RIG_LABEL_KEY) == WORKSPACE_LABEL:
        if workspace_name is None:
            return True
        return labels.get(RIG_WORKSPACE_LABEL_KEY) == sanitize_snapshot_token(workspace_name, max_len=40)
    if workspace_name:
        prefix = f"workspace-{sanitize_snapshot_token(workspace_name, max_len=40)}-"
        return description.startswith(prefix)
    return description.startswith("workspace-")


def filter_snapshots(
    images: Iterable[dict[str, Any]],
    *,
    system: bool = False,
    workspace_name: str | None = None,
    version: str | None = None,
) -> list[dict[str, Any]]:
    """Filter snapshot images by kind (legacy helper for broad selects)."""
    selected: list[dict[str, Any]] = []
    for image in images:
        if image.get("type") and image.get("type") != "snapshot":
            continue
        if system:
            if is_system_snapshot(image, version=None):
                selected.append(image)
        elif workspace_name is not None:
            if is_workspace_snapshot(image, workspace_name=workspace_name):
                selected.append(image)
    return selected


_TRAILING_HASH_RE = re.compile(r"-([A-Za-z0-9]{8,16})$")


def _trailing_hash(description: str) -> str | None:
    match = _TRAILING_HASH_RE.search(description or "")
    return match.group(1) if match else None


def _parse_system_description(description: str) -> tuple[str | None, str | None]:
    """Return ``(version, hash)`` from ``rig-{version}`` or ``rig-{version}-{hash}``."""
    if not (description or "").startswith("rig-"):
        return None, None
    rest = description[4:]
    digest = _trailing_hash(description)
    if digest and rest.endswith(f"-{digest}"):
        return rest[: -(len(digest) + 1)] or None, digest
    return rest or None, None


def _parse_workspace_description(
    description: str,
) -> tuple[str | None, str | None]:
    """Return ``(workspace_name, hash)`` from ``workspace-{name}-{hash}``."""
    if not (description or "").startswith("workspace-"):
        return None, None
    rest = description.removeprefix("workspace-")
    digest = _trailing_hash(description)
    if digest and rest.endswith(f"-{digest}"):
        return rest[: -(len(digest) + 1)] or None, digest
    return rest or None, None


def describe_snapshot(image: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a Hetzner image into ``kind`` / identity fields for cache logic.

    Returns keys: ``kind`` (``system``|``workspace``|``other``), ``version``,
    ``workspace``, ``hash``, ``description``, ``id``.
    """
    labels = image.get("labels") or {}
    description = str(image.get("description") or "")
    kind = "other"
    version = labels.get(RIG_VERSION_LABEL_KEY)
    workspace = labels.get(RIG_WORKSPACE_LABEL_KEY)
    digest = labels.get(RIG_HASH_LABEL_KEY)

    if labels.get(RIG_LABEL_KEY) == SYSTEM_LABEL or description.startswith("rig-"):
        kind = "system"
        parsed_ver, parsed_hash = _parse_system_description(description)
        version = version or parsed_ver
        digest = digest or parsed_hash
    elif labels.get(RIG_LABEL_KEY) == WORKSPACE_LABEL or description.startswith("workspace-"):
        kind = "workspace"
        parsed_ws, parsed_hash = _parse_workspace_description(description)
        workspace = workspace or parsed_ws
        digest = digest or parsed_hash

    return {
        "kind": kind,
        "version": sanitize_snapshot_token(version, max_len=32) if version else None,
        "workspace": (sanitize_snapshot_token(workspace, max_len=40) if workspace else None),
        "hash": sanitize_snapshot_token(digest, max_len=16) if digest else None,
        "description": description or str(image.get("id") or ""),
        "id": image.get("id"),
        "image": image,
    }


def find_unused_snapshots(
    images: Iterable[dict[str, Any]],
    *,
    current_version: str | None = None,
    current_system_hash: str | None = None,
    current_workspace_name: str | None = None,
    current_workspace_hash: str | None = None,
    include_system: bool = True,
    include_workspace: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """
    Split rig snapshots into ``keep`` (current) and ``unused`` (stale).

    Unused means a different version and/or hash than the current project would
    boot from. Workspace unused are limited to ``current_workspace_name`` when
    set; system unused cover any older version or non-matching system hash.
    """
    keep: list[dict[str, Any]] = []
    unused: list[dict[str, Any]] = []
    cur_ver = sanitize_snapshot_token(current_version, max_len=32) if current_version else None
    cur_sys = sanitize_snapshot_token(current_system_hash, max_len=16) if current_system_hash else None
    cur_ws = sanitize_snapshot_token(current_workspace_name, max_len=40) if current_workspace_name else None
    cur_ws_hash = sanitize_snapshot_token(current_workspace_hash, max_len=16) if current_workspace_hash else None

    for image in images:
        if image.get("type") and image.get("type") != "snapshot":
            continue
        meta = describe_snapshot(image)
        kind = meta["kind"]
        if kind == "system" and include_system:
            is_current = bool(cur_ver and cur_sys and meta.get("version") == cur_ver and meta.get("hash") == cur_sys)
            if is_current:
                meta["reason"] = "current system snapshot"
                keep.append(meta)
            else:
                reasons = []
                if cur_ver and meta.get("version") and meta["version"] != cur_ver:
                    reasons.append(f"version {meta['version']} != {cur_ver}")
                elif not meta.get("version"):
                    reasons.append("legacy system snapshot")
                if cur_sys and meta.get("hash") and meta["hash"] != cur_sys:
                    reasons.append(f"hash {meta['hash']} != {cur_sys}")
                elif cur_sys and not meta.get("hash"):
                    reasons.append("missing system hash")
                elif not cur_sys:
                    reasons.append("system snapshot")
                meta["reason"] = "; ".join(reasons) or "not current"
                unused.append(meta)
        elif kind == "workspace" and include_workspace:
            if cur_ws and meta.get("workspace") != cur_ws:
                continue
            is_current = bool(
                cur_ws and cur_ws_hash and meta.get("workspace") == cur_ws and meta.get("hash") == cur_ws_hash
            )
            if is_current:
                meta["reason"] = "current workspace snapshot"
                keep.append(meta)
            else:
                reasons = []
                if cur_ws_hash and meta.get("hash") and meta["hash"] != cur_ws_hash:
                    reasons.append(f"hash {meta['hash']} != {cur_ws_hash}")
                elif cur_ws_hash and not meta.get("hash"):
                    reasons.append("missing workspace hash")
                else:
                    reasons.append("not current workspace snapshot")
                meta["reason"] = "; ".join(reasons)
                unused.append(meta)

    return {"keep": keep, "unused": unused}


def _snapshot_usable(image: dict[str, Any]) -> bool:
    status = (image.get("status") or "available").lower()
    return status == "available"


def find_snapshot_by_description(
    images: Iterable[dict[str, Any]],
    description: str,
) -> dict[str, Any] | None:
    for image in images:
        if image.get("type") and image.get("type") != "snapshot":
            continue
        if image.get("description") != description:
            continue
        if not _snapshot_usable(image):
            continue
        return image
    return None


def resolve_boot_snapshot(
    images: Iterable[dict[str, Any]],
    *,
    workspace_name: str,
    content_hash: str,
    version: str | None = None,
    system_hash: str | None = None,
) -> dict[str, Any]:
    """
    Pick a boot snapshot for faster create.

    Preference:
    1. System snapshot ``rig-{version}-{system_hash}`` → skip provision+configure
    2. Neither → boot from the configured base image

    Legacy workspace snapshots are ignored for boot (secrets-safe model).
    ``authorize`` is never skipped (credentials always re-applied after boot).
    """
    images = list(images)
    # content_hash / workspace_name kept for call-site compatibility.
    _ = (workspace_name, content_hash)

    if system_hash:
        system_desc = system_snapshot_name(version, content_hash=system_hash)
        system_hit = find_snapshot_by_description(images, system_desc)
        if system_hit:
            return {
                "source": "system",
                "image": system_hit,
                "description": system_desc,
                "skip_playbooks": ("provision", "configure"),
            }

    return {
        "source": "base",
        "image": None,
        "description": None,
        "skip_playbooks": (),
    }
