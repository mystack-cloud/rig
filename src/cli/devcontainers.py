"""Dev Containers: discovery and selection of the Dev target `rig dev` opens."""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DevTarget:
    """A Repo plus one Dev Container in it.

    ``config`` is the ``devcontainer.json`` path relative to the Repo root.
    """

    repo: str
    config: str

    @property
    def name(self) -> str:
        """The container's name within its Repo ('' for the default one).

        A subfolder prefixes the name: ``svc/x`` is ``svc/.devcontainer/x/``.
        """
        parts = self.config.split("/")
        sub = parts[0] if len(parts) > 1 and not parts[0].startswith(".devcontainer") else ""
        rest = parts[1:] if sub else parts
        named = rest[1] if len(rest) == 3 else ""  # .devcontainer/<name>/devcontainer.json
        return "/".join(p for p in (sub, named) if p)

    @property
    def label(self) -> str:
        return f"{self.repo}:{self.name}" if self.name else self.repo


class DevContainerError(Exception):
    """A Dev target could not be chosen."""


# `<repo>/[<subfolder>/]` then one of the three locations the Dev Containers spec defines.
_CONFIG_RE = re.compile(
    r"^(?:(?P<sub>[^/]+)/)?"
    r"(?:\.devcontainer/(?:[^/]+/)?devcontainer\.json|\.devcontainer\.json)$"
)
_SKIPPED_DIRS = ("node_modules", ".git")


def _scan_command(dest: str) -> str:
    skipped = " -o ".join(f"-name {d}" for d in _SKIPPED_DIRS)
    return (
        f"find {shlex.quote(dest)} -maxdepth 4 \\( {skipped} \\) -prune -o "
        r"-type f \( -name devcontainer.json -o -name .devcontainer.json \) -print"
    )


def find_devcontainers(
    run_remote: Callable[[str], str],
    repos: Sequence[tuple[str, str]],
) -> list[DevTarget]:
    """List the Dev Containers in each ``(repo name, remote path)``.

    Looks in the Repo root and its immediate subfolders only.
    """
    found: list[DevTarget] = []
    for name, dest in repos:
        for line in run_remote(_scan_command(dest)).splitlines():
            line = line.strip()
            if not line.startswith(dest.rstrip("/") + "/"):
                continue
            config = os.path.relpath(line, dest)
            match = _CONFIG_RE.match(config)
            if not match or match.group("sub") in _SKIPPED_DIRS:
                continue
            found.append(DevTarget(name, config))
    return sorted(found, key=lambda t: (t.repo, t.config))


def _listing(candidates: Sequence[DevTarget]) -> str:
    return ", ".join(t.label for t in candidates)


def choose_target(
    candidates: Sequence[DevTarget],
    remembered: DevTarget | None,
    requested: str | None,
    select: bool,
    interactive: bool,
    input_fn: Callable[[str], str] = input,
) -> DevTarget | None:
    """Pick the Dev target to open, or None when there is no Dev Container.

    ``requested`` is ``--target repo[:name]``. ``select`` forces the prompt.
    """
    if requested:
        exact = [t for t in candidates if t.label == requested] if ":" in requested else []
        in_repo = [t for t in candidates if t.repo == requested]
        if exact:
            return exact[0]
        if len(in_repo) == 1:
            return in_repo[0]
        if not in_repo:
            raise DevContainerError(
                f"No Dev Container matches '{requested}' (found: {_listing(candidates) or 'none'})"
            )
        candidates = in_repo
    elif not candidates:
        return None
    elif len(candidates) == 1 and not select:
        return candidates[0]

    if not interactive:
        raise DevContainerError(
            f"Several Dev Containers found: {_listing(candidates)}. "
            "Pick one with --target <repo>[:<name>]"
        )
    default = remembered if remembered in candidates else candidates[0]
    for index, target in enumerate(candidates, 1):
        print(f"  {index}) {target.label}")
    while True:
        answer = input_fn(f"Open which Dev Container? [{default.label}] ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(candidates):
            return candidates[int(answer) - 1]
        named = [t for t in candidates if t.label == answer]
        if named:
            return named[0]
        print(f"Not a choice: {answer}")


def devcontainer_folder_uri(alias: str, repo_dest: str, target: DevTarget) -> str:
    """``--folder-uri`` that opens *target* in its container over Remote-SSH.

    The folder holding the ``.devcontainer`` definition is the container's host
    path, and the editor mounts it at ``/workspaces/<folder name>``.
    """
    config = posixpath.join(repo_dest, target.config)
    # "" for the repo root, "sub/" for a subfolder
    prefix = target.config.split(".devcontainer")[0]
    host_path = posixpath.join(repo_dest, prefix).rstrip("/")
    payload = {
        "hostPath": host_path,
        "configFile": {
            "$mid": 1,
            "path": config,
            "scheme": "vscode-remote",
            "authority": f"ssh-remote+{alias}",
        },
    }
    encoded = json.dumps(payload, separators=(",", ":")).encode().hex()
    return (
        f"vscode-remote://dev-container+{encoded}@ssh-remote+{alias}"
        f"/workspaces/{posixpath.basename(host_path)}"
    )


class DevContainerScanError(DevContainerError):
    """The Environment could not be searched for Dev Containers."""


def devcontainers_enabled(config: dict | None) -> bool:
    """``editor.devcontainers`` in rig.yml (off by default)."""
    editor = (config or {}).get("editor")
    return isinstance(editor, dict) and editor.get("devcontainers") is True


def repo_destinations(config: dict | None, root: str) -> list[tuple[str, str]]:
    """``(name, remote path)`` of every Repo in rig.yml."""
    found = []
    for item in (config or {}).get("repos") or []:
        if isinstance(item, dict) and item.get("name"):
            name = str(item["name"])
            found.append((name, str(item.get("dest") or posixpath.join(root, name))))
    return found


def scan_environment(env, repos: Sequence[tuple[str, str]]) -> list[DevTarget]:
    """Find the Dev Containers in *repos* on *env*'s VM over SSH."""

    def run_remote(command: str) -> str:
        try:
            result = env.run_remote(command)
        except Exception as exc:  # ssh missing, timeout, ...
            raise DevContainerScanError(f"could not reach the Environment: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or f"exit {result.returncode}"
            raise DevContainerScanError(f"scan failed: {detail}")
        return result.stdout

    return find_devcontainers(run_remote, repos)
