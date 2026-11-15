"""Per-workspace, per-profile state bucket names recorded in ``.rig/vars.yml``.

Layout in ``vars.yml``::

    rig_state_buckets:
      default: rig-1a2b3c4d
      staging: rig-9f8e7d6c

See docs/adr/0001-per-profile-generated-state-bucket.md.
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    import ruamel.yaml as yaml  # type: ignore

STATE_BUCKETS_VAR = "rig_state_buckets"
STATE_BUCKET_PREFIX = "rig-"


class StateBucketError(RuntimeError):
    """A state bucket could not be resolved, created, or persisted."""


class StateBucketNotConfigured(StateBucketError):
    """No state bucket is configured for this workspace and profile."""


def new_state_bucket() -> str:
    """Generate a unique state bucket name (``rig-{8 hex}``)."""
    return f"{STATE_BUCKET_PREFIX}{uuid.uuid4().hex[:8]}"


def find_workspace_dir(start: Optional[str] = None) -> str:
    """Nearest ancestor of ``start`` (default cwd) containing ``.rig/``, else ``start``."""
    current = os.path.abspath(start or os.getcwd())
    probe = current
    while True:
        if os.path.isdir(os.path.join(probe, ".rig")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return current
        probe = parent


def shared_vars_file(workspace_dir: str) -> str:
    return os.path.join(os.path.abspath(workspace_dir), ".rig", "vars.yml")


class _VaultTolerantLoader(yaml.SafeLoader):
    """Safe loader that reads ``!vault`` scalars as plain strings."""


_VaultTolerantLoader.add_constructor(
    "!vault", lambda loader, node: loader.construct_scalar(node)
)


def read_state_bucket(workspace_dir: str, profile: str) -> Optional[str]:
    """Return the recorded bucket for ``profile``, or None. Never writes."""
    path = shared_vars_file(workspace_dir)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.load(handle, Loader=_VaultTolerantLoader) or {}
    buckets = data.get(STATE_BUCKETS_VAR) if isinstance(data, dict) else None
    if not isinstance(buckets, dict):
        return None
    value = buckets.get(profile)
    return str(value).strip() or None if value else None


def write_state_bucket(workspace_dir: str, profile: str, bucket: str) -> str:
    """Record ``bucket`` for ``profile`` in ``vars.yml``, preserving other content.

    Returns the file path. Raises StateBucketError if it cannot be written.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", profile):
        raise StateBucketError(f"Profile name {profile!r} cannot be used as a vars.yml key")
    path = shared_vars_file(workspace_dir)
    try:
        existing = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                existing = handle.read()
        entry = f"  {profile}: {bucket}\n"
        block = re.search(
            rf"^{STATE_BUCKETS_VAR}:[ \t]*\n((?:[ \t]+.*\n?|[ \t]*\n)*)",
            existing,
            re.MULTILINE,
        )
        if block:
            body = block.group(1)
            line_re = re.compile(rf"^[ \t]+{re.escape(profile)}:.*\n?", re.MULTILINE)
            if line_re.search(body):
                body = line_re.sub(entry, body, count=1)
            else:
                body = (body if body.endswith("\n") or not body else body + "\n") + entry
            updated = existing[: block.start(1)] + body + existing[block.end(1):]
        else:
            prefix = existing if not existing or existing.endswith("\n") else existing + "\n"
            updated = f"{prefix}{STATE_BUCKETS_VAR}:\n{entry}"
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(updated)
    except OSError as exc:
        raise StateBucketError(
            f"Could not record the state bucket in {path}: {exc}. "
            "Fix the permissions or set STATE_BUCKET."
        ) from exc
    return path
