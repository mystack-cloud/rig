"""Hetzner VM snapshot naming and workspace content hashing for rig cache."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, Iterable, List, Optional

SYSTEM_LABEL = "system"
WORKSPACE_LABEL = "workspace"
RIG_LABEL_KEY = "rig"
RIG_WORKSPACE_LABEL_KEY = "rig_workspace"
RIG_VERSION_LABEL_KEY = "rig_version"
RIG_HASH_LABEL_KEY = "rig_hash"


def package_version() -> str:
    """Installed ``rig`` package version (fallback ``0.0.0``)."""
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version("rig")
    except Exception:
        try:
            from importlib_metadata import version as pkg_version_fallback
            return pkg_version_fallback("rig")
        except Exception:
            return "0.0.0"


def sanitize_snapshot_token(value: str, *, max_len: int = 48) -> str:
    """Keep Hetzner-friendly description tokens (alnum, dash, dot, underscore)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return (cleaned or "unnamed")[:max_len]


def system_snapshot_name(version: Optional[str] = None) -> str:
    """Snapshot description after system.yml: ``rig-{version}``."""
    ver = sanitize_snapshot_token(version or package_version(), max_len=32)
    return f"rig-{ver}"


def workspace_snapshot_name(workspace_name: str, content_hash: str) -> str:
    """Snapshot description after workspace.yml: ``workspace-{name}-{hash}``."""
    name = sanitize_snapshot_token(workspace_name, max_len=40)
    digest = sanitize_snapshot_token(content_hash, max_len=16)
    return f"workspace-{name}-{digest}"


# Ephemeral / generated paths must not affect workspace snapshot identity.
_HASH_SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "roles",  # ansible-galaxy installs
    "collections",
}
_HASH_SKIP_FILE_NAMES = {
    "inventory.yml",  # rewritten every create with VM IP/key
    "system.yml",  # auto-generated; system snapshot is keyed by rig version
}
_HASH_SKIP_SUFFIXES = (".pyc", ".pyo", ".retry")


def iter_workspace_hash_paths(project_dir: str) -> List[str]:
    """
    Files that define workspace identity for snapshot reuse.

    Includes ``rig.yml`` and durable ``.rig/`` inputs (vars, workspace playbook,
    agent scaffolds, requirements). Excludes inventory, auto-generated
    ``system.yml``, and Galaxy role/collection installs.
    """
    project_dir = os.path.abspath(project_dir)
    paths: List[str] = []

    rig_yml = os.path.join(project_dir, "rig.yml")
    if os.path.isfile(rig_yml):
        paths.append(rig_yml)

    rig_dir = os.path.join(project_dir, ".rig")
    if os.path.isdir(rig_dir):
        for root, dirs, files in os.walk(rig_dir):
            dirs[:] = sorted(d for d in dirs if d not in _HASH_SKIP_DIR_NAMES)
            for filename in sorted(files):
                if filename in _HASH_SKIP_FILE_NAMES:
                    continue
                if filename.endswith(_HASH_SKIP_SUFFIXES):
                    continue
                paths.append(os.path.join(root, filename))

    return paths


def workspace_content_hash(project_dir: str) -> str:
    """
    Stable short hash of workspace-defining files under ``rig.yml`` / ``.rig/``.

    Paths are hashed in sorted order so the digest is reproducible across runs
    when the project definition has not changed.
    """
    project_dir = os.path.abspath(project_dir)
    digest = hashlib.sha256()
    paths = iter_workspace_hash_paths(project_dir)

    try:
        from cli import log as log
        for path in paths:
            rel = os.path.relpath(path, project_dir).replace(os.sep, "/")
            log.debug("workspace hash input: %s", rel)
    except Exception:
        pass

    for path in paths:
        rel = os.path.relpath(path, project_dir).replace(os.sep, "/")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            with open(path, "rb") as handle:
                while True:
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")

    return digest.hexdigest()[:12]


def system_snapshot_labels(version: Optional[str] = None) -> Dict[str, str]:
    ver = sanitize_snapshot_token(version or package_version(), max_len=32)
    return {
        RIG_LABEL_KEY: SYSTEM_LABEL,
        RIG_VERSION_LABEL_KEY: ver,
    }


def workspace_snapshot_labels(workspace_name: str, content_hash: str) -> Dict[str, str]:
    return {
        RIG_LABEL_KEY: WORKSPACE_LABEL,
        RIG_WORKSPACE_LABEL_KEY: sanitize_snapshot_token(workspace_name, max_len=40),
        RIG_HASH_LABEL_KEY: sanitize_snapshot_token(content_hash, max_len=16),
    }


def is_system_snapshot(image: Dict[str, Any], version: Optional[str] = None) -> bool:
    labels = image.get("labels") or {}
    description = image.get("description") or ""
    if labels.get(RIG_LABEL_KEY) == SYSTEM_LABEL:
        if version is None:
            return True
        return labels.get(RIG_VERSION_LABEL_KEY) == sanitize_snapshot_token(
            version, max_len=32
        )
    expected = system_snapshot_name(version) if version else None
    if expected:
        return description == expected
    return bool(re.match(r"^rig-[A-Za-z0-9._-]+$", description))


def is_workspace_snapshot(
    image: Dict[str, Any],
    workspace_name: Optional[str] = None,
) -> bool:
    labels = image.get("labels") or {}
    description = image.get("description") or ""
    if labels.get(RIG_LABEL_KEY) == WORKSPACE_LABEL:
        if workspace_name is None:
            return True
        return labels.get(RIG_WORKSPACE_LABEL_KEY) == sanitize_snapshot_token(
            workspace_name, max_len=40
        )
    if workspace_name:
        prefix = f"workspace-{sanitize_snapshot_token(workspace_name, max_len=40)}-"
        return description.startswith(prefix)
    return description.startswith("workspace-")


def filter_snapshots(
    images: Iterable[Dict[str, Any]],
    *,
    system: bool = False,
    workspace_name: Optional[str] = None,
    version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter snapshot images for cache clear."""
    selected: List[Dict[str, Any]] = []
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


def _snapshot_usable(image: Dict[str, Any]) -> bool:
    status = (image.get("status") or "available").lower()
    return status == "available"


def find_snapshot_by_description(
    images: Iterable[Dict[str, Any]],
    description: str,
) -> Optional[Dict[str, Any]]:
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
    images: Iterable[Dict[str, Any]],
    *,
    workspace_name: str,
    content_hash: str,
    version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pick a boot snapshot for faster create.

    Preference:
    1. Exact workspace snapshot ``workspace-{name}-{hash}`` → skip system+workspace
    2. System snapshot ``rig-{version}`` → skip system only
    3. Neither → boot from the configured base image
    """
    images = list(images)
    workspace_desc = workspace_snapshot_name(workspace_name, content_hash)
    workspace_hit = find_snapshot_by_description(images, workspace_desc)
    if workspace_hit:
        return {
            "source": "workspace",
            "image": workspace_hit,
            "description": workspace_desc,
            "skip_playbooks": ("system", "workspace"),
        }

    system_desc = system_snapshot_name(version)
    system_hit = find_snapshot_by_description(images, system_desc)
    if system_hit:
        return {
            "source": "system",
            "image": system_hit,
            "description": system_desc,
            "skip_playbooks": ("system",),
        }

    return {
        "source": "base",
        "image": None,
        "description": None,
        "skip_playbooks": (),
    }
