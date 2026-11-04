"""Remember the last handled remote env name under /tmp (cleared on reboot)."""

from __future__ import annotations

import os
from typing import Optional

DEFAULT_LAST_ENV_PATH = "/tmp/.rig/last-env"


def remember_last_env(name: str, path: str = DEFAULT_LAST_ENV_PATH) -> None:
    """Persist *name* so later commands can omit it."""
    if not name:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{name.strip()}\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_last_env(path: str = DEFAULT_LAST_ENV_PATH) -> Optional[str]:
    """Return the last remembered environment name, or None."""
    try:
        with open(path, encoding="utf-8") as handle:
            name = handle.read().strip()
    except OSError:
        return None
    return name or None


def clear_last_env(
    name: Optional[str] = None,
    path: str = DEFAULT_LAST_ENV_PATH,
) -> None:
    """
    Remove the remembered name.

    If *name* is given, only clear when it matches the stored value
    (so destroying another env does not forget the current one).
    """
    if name is not None:
        current = read_last_env(path=path)
        if current is None or current != name:
            return
    try:
        os.remove(path)
    except OSError:
        pass


def resolve_env_name(
    *candidates: Optional[str],
    path: str = DEFAULT_LAST_ENV_PATH,
) -> Optional[str]:
    """First non-empty candidate, else the remembered last env."""
    for value in candidates:
        if value and str(value).strip():
            return str(value).strip()
    return read_last_env(path=path)
