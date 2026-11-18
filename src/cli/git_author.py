"""Git author (``user.name`` / ``user.email``) for commits made on a rig VM.

The VM starts with no git identity, so the first ``git commit`` (by the
developer or by a coding agent) fails with "Author identity unknown". rig
derives one from the developer's rig *identity* (``rig_identity``) and hands
it to the playbooks as ``rig_git_author_name`` / ``rig_git_author_email``.

Resolution order (first non-empty wins, per field):

1. ``git_author_name`` / ``git_author_email`` in ``.rig/vars.yml`` or the
   identity's personal ``.rig/vars.<identity>.yml`` (personal wins) — the place
   to put a real name and a GitHub-linked email;
2. derived from the identity: the identity itself as the name and
   ``<identity>@rig.local`` as the email.

The derived email is deliberately not a ``users.noreply.github.com`` address:
GitHub links that form to whichever account owns the handle, which could
attribute the commits to somebody else.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

NAME_VAR = "git_author_name"
EMAIL_VAR = "git_author_email"
DERIVED_EMAIL_DOMAIN = "rig.local"


def _clean(value: Any) -> str:
    """One-line, trimmed string; anything that is not text counts as unset."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def derive_git_author(identity: str) -> Dict[str, str]:
    """Author derived from the rig identity alone."""
    name = _clean(identity)
    return {
        "name": name,
        "email": f"{name}@{DERIVED_EMAIL_DOMAIN}" if name else "",
    }


def resolve_git_author(
    identity: str,
    get_var: Optional[Callable[[str], Any]] = None,
) -> Dict[str, str]:
    """Resolve ``{"name": ..., "email": ...}``; either may override separately.

    ``get_var`` looks a key up in the project vars (``project.get_vars``).
    """
    derived = derive_git_author(identity)
    name = email = ""
    if get_var is not None:
        try:
            name = _clean(get_var(NAME_VAR))
            email = _clean(get_var(EMAIL_VAR))
        except Exception:
            name = email = ""
    return {
        "name": name or derived["name"],
        "email": email or derived["email"],
    }
