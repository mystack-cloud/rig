"""Dev Containers: discovery and selection of the Dev target `rig dev` opens."""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


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


def _is_dev_container_path(config: str) -> bool:
    """Is *config* (relative to a Repo root) a Dev Container within the scan depth?"""
    match = _CONFIG_RE.match(config)
    return match is not None and match.group("sub") not in _SKIPPED_DIRS


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
            if _is_dev_container_path(config):
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
            raise DevContainerError(f"No Dev Container matches '{requested}' (found: {_listing(candidates) or 'none'})")
        candidates = in_repo
    elif not candidates:
        return None
    elif len(candidates) == 1 and not select:
        return candidates[0]

    if not interactive:
        raise DevContainerError(
            f"Several Dev Containers found: {_listing(candidates)}. Pick one with --target <repo>[:<name>]"
        )
    default = candidates[0]
    if remembered is not None and remembered in candidates:
        default = remembered
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


def _strip_jsonc(text: str) -> str:
    """Drop ``//`` and ``/* */`` comments and trailing commas (devcontainer.json is JSONC)."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def parse_workspace_folder(config_text: str, host_path: str) -> str | None:
    """``workspaceFolder`` from a devcontainer.json's text, or None when unset or unusable.

    Resolves ``${localWorkspaceFolder}`` and ``${localWorkspaceFolderBasename}``;
    anything else that is not an absolute path is ignored.
    """
    try:
        data = json.loads(_strip_jsonc(config_text))
    except ValueError:
        return None
    value = data.get("workspaceFolder") if isinstance(data, dict) else None
    if not isinstance(value, str):
        return None
    value = value.replace("${localWorkspaceFolderBasename}", posixpath.basename(host_path))
    value = value.replace("${localWorkspaceFolder}", host_path)
    return value if value.startswith("/") and "${" not in value else None


def read_workspace_folder(env, repo_dest: str, target: DevTarget) -> str | None:
    """The ``workspaceFolder`` *target*'s devcontainer.json sets, read from *env*'s VM over SSH.

    None when it is unset or the file can't be read, so the caller falls back to the default.
    """
    config = posixpath.join(repo_dest, target.config)
    try:
        result = env.run_remote(f"cat {shlex.quote(config)}")
    except Exception:  # ssh missing, timeout, ...
        return None
    if result.returncode != 0:
        return None
    return parse_workspace_folder(result.stdout, _host_path(repo_dest, target))


def _host_path(repo_dest: str, target: DevTarget) -> str:
    # "" for the repo root, "sub/" for a subfolder
    prefix = target.config.split(".devcontainer")[0]
    return posixpath.join(repo_dest, prefix).rstrip("/")


def devcontainer_folder_uri(alias: str, repo_dest: str, target: DevTarget, workspace_folder: str | None = None) -> str:
    """``--folder-uri`` that opens *target* in its container over Remote-SSH.

    The folder holding the ``.devcontainer`` definition is the container's host
    path. The editor opens the config's ``workspaceFolder`` when it sets one,
    else the Dev Containers default ``/workspaces/<folder name>``.
    """
    config = posixpath.join(repo_dest, target.config)
    host_path = _host_path(repo_dest, target)
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
    folder = workspace_folder or f"/workspaces/{posixpath.basename(host_path)}"
    return f"vscode-remote://dev-container+{encoded}@ssh-remote+{alias}{folder}"


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


_LOCAL_PATTERNS = (".devcontainer.json", ".devcontainer/devcontainer.json", ".devcontainer/*/devcontainer.json")


def find_local_devcontainers(repos: Sequence[dict]) -> list[DevTarget]:
    """Dev Containers in Repos checked out on this machine (each has ``name`` and ``path``)."""
    found: list[DevTarget] = []
    for repo in repos:
        name, path = repo.get("name"), repo.get("path")
        if not name or not path or not os.path.isdir(path):
            continue
        root = Path(path)
        for prefix in ("", "*/"):
            for pattern in _LOCAL_PATTERNS:
                for match in root.glob(prefix + pattern):
                    config = match.relative_to(root).as_posix()
                    if match.is_file() and _is_dev_container_path(config):
                        found.append(DevTarget(str(name), config))
    return sorted(set(found), key=lambda t: (t.repo, t.config))


def prompt_devcontainers(
    found: Sequence[DevTarget],
    current: bool | None,
    input_fn: Callable[[str], str] = input,
) -> bool | None:
    """Whether to enable Dev Containers for the Workspace, asked by ``rig init``.

    Asks only when some were found and ``editor.devcontainers`` was never set;
    Enter means yes. An earlier answer is kept. None means leave the flag unset.
    """
    if current is not None:
        return current
    if not found:
        return None
    repos = ", ".join(sorted({t.repo for t in found}))
    answer = input_fn(
        f"Dev Containers found in {repos}. Enable Dev Container support "
        "(installs Docker, rebuilds the snapshot once)? [Y/n]: "
    )
    return not answer.strip().lower().startswith("n")
