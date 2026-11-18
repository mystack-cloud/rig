"""Remember the last coding-agent session id under /tmp (cleared on reboot)."""

from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_SESSION_DIR = "/tmp/.rig/sessions"

# Strip common VT sequences / CR so script(1) logs parse cleanly.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r")

# Prefer explicit resume/session flags from exit banners, then bare ids.
_SESSION_PATTERNS = (
    re.compile(
        r"--resume(?:=|\s+)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        re.IGNORECASE,
    ),
    re.compile(r"--resume(?:=|\s+)([A-Za-z0-9][A-Za-z0-9_-]{5,})"),
    re.compile(r"(?:--session|-s)(?:=|\s+)(ses_[A-Za-z0-9_-]+)"),
    re.compile(r"\bses_[A-Za-z0-9_-]+\b"),
    re.compile(
        r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        re.IGNORECASE,
    ),
)


def _safe_env_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip())
    return cleaned.strip("-._") or "env"


def session_path(env_name: str, directory: str = DEFAULT_SESSION_DIR) -> str:
    return os.path.join(directory, _safe_env_name(env_name))


def remember_last_session(
    env_name: str,
    session_id: str,
    agent_type: str | None = None,
    directory: str = DEFAULT_SESSION_DIR,
) -> None:
    """Persist the last session id for *env_name*."""
    if not env_name or not session_id:
        return
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = session_path(env_name, directory=directory)
    lines = [f"session={session_id.strip()}\n"]
    if agent_type:
        lines.insert(0, f"agent={agent_type.strip()}\n")
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    try:
        os.chmod(path, 0o600)
        os.chmod(directory, 0o700)
    except OSError:
        pass


def read_last_session(
    env_name: str,
    directory: str = DEFAULT_SESSION_DIR,
) -> tuple[str, str | None] | None:
    """
    Return ``(session_id, agent_type_or_None)`` for *env_name*, if known.
    """
    path = session_path(env_name, directory=directory)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None

    session_id: str | None = None
    agent_type: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key in {"session", "id", "session_id"} and value:
                session_id = value
            elif key == "agent" and value:
                agent_type = value
        elif session_id is None:
            session_id = line
    if not session_id:
        return None
    return session_id, agent_type


def clear_last_session(
    env_name: str | None = None,
    directory: str = DEFAULT_SESSION_DIR,
) -> None:
    """Remove stored session for one env, or all sessions when *env_name* is None."""
    if env_name is None:
        try:
            for name in os.listdir(directory):
                try:
                    os.remove(os.path.join(directory, name))
                except OSError:
                    pass
        except OSError:
            pass
        return
    try:
        os.remove(session_path(env_name, directory=directory))
    except OSError:
        pass


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def parse_session_id(text: str) -> str | None:
    """Extract the most likely session / thread id from agent exit output."""
    cleaned = strip_ansi(text)
    if not cleaned.strip():
        return None
    last: str | None = None
    for pattern in _SESSION_PATTERNS:
        for match in pattern.finditer(cleaned):
            value = match.group(1) if match.lastindex else match.group(0)
            if value:
                last = value
    return last


def extract_session_from_agent_args(agent_args: list[str] | None) -> str | None:
    """Return a session id explicitly present in forwarded agent argv."""
    args = list(agent_args or [])
    for index, token in enumerate(args):
        if token in {"--session", "-s", "--resume"} and index + 1 < len(args):
            value = args[index + 1]
            if value and not str(value).startswith("-"):
                return str(value)
        for prefix in ("--session=", "--resume=", "-s="):
            if token.startswith(prefix) and len(token) > len(prefix):
                return token[len(prefix) :]
    return None


def agent_args_request_session(agent_args: list[str] | None) -> bool:
    """True when argv already asks to continue/resume/select a session."""
    args = list(agent_args or [])
    for index, token in enumerate(args):
        if token in {"--continue", "-c", "--resume", "--session", "-s"}:
            return True
        if token.startswith("--resume=") or token.startswith("--session=") or token.startswith("-s="):
            return True
        # bare ``--resume`` with optional id already covered above
        if token == "--" and index + 1 < len(args):
            # remainder after -- still scanned in loop
            continue
    return False


def resume_args_for_agent(agent_type: str | None, session_id: str) -> list[str]:
    """Build CLI flags to reopen *session_id* for the given agent."""
    agent = (agent_type or "opencode").strip().lower()
    if agent in {"opencode", "open-code"}:
        return ["--session", session_id]
    # Claude Code and Cursor CLI both use --resume <id>
    return ["--resume", session_id]
