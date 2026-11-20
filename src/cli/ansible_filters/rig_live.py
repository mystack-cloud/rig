"""Claude Code hooks for live agents (Ansible filter plugin, standard library only).

``rig exec`` may queue a prompt into a running Claude Code, but typing into a TUI
that waits at an approval dialog would answer the dialog. These hooks record what
Claude is doing in a state file that ``rig-agent-queue`` reads first:

    idle      SessionStart, Stop, Notification(idle_prompt)
    working   UserPromptSubmit, PreToolUse, PostToolUse
    waiting   PermissionRequest, Notification(permission_prompt | agent_needs_input)
    ended     SessionEnd

Claude has no event for "the user answered the dialog"; the next tool event
marks the work as resumed.
"""

from __future__ import annotations

import copy
from typing import Any

STATE_HELPER = "/usr/local/bin/rig-agent-state"
AGENT = "claude"

# (event, matcher or None, state)
HOOKS: tuple[tuple[str, str | None, str], ...] = (
    ("SessionStart", None, "idle"),
    ("UserPromptSubmit", None, "working"),
    ("PreToolUse", None, "working"),
    ("PostToolUse", None, "working"),
    ("PermissionRequest", None, "waiting"),
    ("Notification", "permission_prompt", "waiting"),
    ("Notification", "agent_needs_input", "waiting"),
    ("Notification", "idle_prompt", "idle"),
    ("Stop", None, "idle"),
    ("SessionEnd", None, "ended"),
)


def _command(state: str) -> str:
    return f"{STATE_HELPER} {AGENT} {state}"


def _is_rig_handler(handler: Any) -> bool:
    return isinstance(handler, dict) and str(handler.get("command", "")).startswith(STATE_HELPER)


def merge_hooks(settings: Any) -> dict[str, Any]:
    """Return ``settings`` with rig's state hooks installed.

    Hooks the user added are kept. Earlier rig entries are removed first, so
    running this again (or with a changed event list) does not duplicate them.
    """
    out = copy.deepcopy(settings) if isinstance(settings, dict) else {}
    hooks = out.get("hooks")
    hooks = hooks if isinstance(hooks, dict) else {}

    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            handlers = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(handlers, list):
                kept.append(group)
                continue
            remaining = [h for h in handlers if not _is_rig_handler(h)]
            if remaining:
                kept.append({**group, "hooks": remaining})
        hooks[event] = kept

    for event, matcher, state in HOOKS:
        rig_group: dict[str, Any] = {"hooks": [{"type": "command", "command": _command(state)}]}
        if matcher:
            rig_group = {"matcher": matcher, **rig_group}
        hooks.setdefault(event, []).append(rig_group)

    out["hooks"] = {event: groups for event, groups in hooks.items() if groups}
    return out


def rig_claude_hooks(settings: Any) -> dict[str, Any]:
    """``{{ existing_settings | rig_claude_hooks }}``"""
    return merge_hooks(settings)


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {"rig_claude_hooks": rig_claude_hooks}
