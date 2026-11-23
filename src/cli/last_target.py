"""Remember the last Dev target per environment under /tmp (cleared on reboot)."""

from __future__ import annotations

import os
import re

from cli.devcontainers import DevTarget

DEFAULT_TARGET_DIR = "/tmp/.rig/targets"


def _safe_env_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip())
    return cleaned.strip("-._") or "env"


def target_path(env_name: str, directory: str = DEFAULT_TARGET_DIR) -> str:
    return os.path.join(directory, _safe_env_name(env_name))


def remember_target(
    env_name: str,
    target: DevTarget,
    directory: str = DEFAULT_TARGET_DIR,
) -> None:
    """Persist *target* as the last Dev target for *env_name*."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = target_path(env_name, directory=directory)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"repo={target.repo}\nconfig={target.config}\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_target(
    env_name: str,
    directory: str = DEFAULT_TARGET_DIR,
) -> DevTarget | None:
    """Return the remembered Dev target for *env_name*, or None."""
    try:
        with open(target_path(env_name, directory=directory), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    repo, config = values.get("repo"), values.get("config")
    if not repo or not config:
        return None
    return DevTarget(repo=repo, config=config)
