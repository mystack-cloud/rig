"""The workspace user: the one non-root account rig sets up and works as on an Environment.

It is the account you SSH in as, agents run as, and code-server and Dev
Containers use. Ansible still connects as root. Set it with ``host.user`` in
``rig.yml``; the default is ``dev``.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_WORKSPACE_USER = "dev"
# Kept for callers that only need the default.
WORKSPACE_USER = DEFAULT_WORKSPACE_USER
WORKSPACE_HOME = f"/home/{DEFAULT_WORKSPACE_USER}"

_VALID_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def workspace_user(host: dict[str, Any] | None) -> str:
    """The workspace user named by ``host.user`` (default ``dev``).

    Raises ValueError for root or a name that is not a valid Linux user name.
    """
    value = (host or {}).get("user")
    if value is None or str(value).strip() == "":
        return DEFAULT_WORKSPACE_USER
    name = str(value).strip()
    if name == "root":
        raise ValueError("host.user cannot be 'root': the workspace user must be a non-root account")
    if not _VALID_USER.match(name):
        raise ValueError(f"host.user {name!r} is not a valid Linux user name (lowercase letters, digits, '_' and '-')")
    return name


def workspace_home(user: str = DEFAULT_WORKSPACE_USER) -> str:
    return f"/home/{user}"
