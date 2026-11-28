"""Helpers for tests that inspect the bundled roles."""

import os

import yaml

ROLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "cli", "roles")


def role_names():
    return sorted(n for n in os.listdir(ROLES_DIR) if os.path.isdir(os.path.join(ROLES_DIR, n)))


def role_text(*, stage="install"):
    """All text of every bundled role's ``tasks/<stage>.yml`` (empty when a role has none)."""
    parts = []
    for name in role_names():
        path = os.path.join(ROLES_DIR, name, "tasks", f"{stage}.yml")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                parts.append(handle.read())
    return "\n".join(parts)


def role_tasks(role, stage="install"):
    """Flattened leaf tasks of one role entry point, each with its effective tag set."""
    path = os.path.join(ROLES_DIR, role, "tasks", f"{stage}.yml")
    with open(path, encoding="utf-8") as handle:
        return _flatten(yaml.safe_load(handle) or [], set())


def _flatten(tasks, inherited):
    out = []
    for task in tasks:
        tags = set(inherited) | set(task.get("tags") or [])
        if "block" in task:
            out.extend(_flatten(task["block"], tags))
        else:
            out.append({**task, "_tags": tags})
    return out


def authorize_tasks():
    """The authorize tasks of every bundled role, flat: each role's outer block is opened, inner blocks stay."""
    out = []
    for name in role_names():
        path = os.path.join(ROLES_DIR, name, "tasks", "authorize.yml")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or []
        for task in data:
            out.extend(task["block"] if "block" in task and "tags" in task and task["tags"][0] == name else [task])
    return out


def authorize_play():
    """Stands in for the old inline authorize play: ``authorize_play()["tasks"]``."""
    return {"tasks": authorize_tasks()}


def authorize_text():
    """The generated authorize playbook plus every role's authorize tasks, as one string."""
    from cli.remote_dev import AUTHORIZE_PLAYBOOK

    return AUTHORIZE_PLAYBOOK + role_text(stage="authorize")
