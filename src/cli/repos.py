"""``rig repo``: add, remove and list the Workspace's Repos in ``rig.yml``.

``rig.yml`` ``repos:`` is the source of truth. The clone lists the playbooks
read (``git.clone_repos``) and ``git_providers`` are derived from it on every
run, so this module writes nothing else, apart from an optional PAT var.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from cli.encrypt import (
    PROVIDER_ENV_VARS,
    collect_repo_pats,
    detect_git_provider,
    list_all_vault_vars,
    provider_pat_var_name,
    resolve_project_dir,
    resolve_repo_pat_var_names,
    store_repo_pats,
    to_https_git_url,
)


def repo_name_from_url(url: str) -> str:
    """Last path segment of *url*, without a ``.git`` suffix."""
    tail = url.strip().rstrip("/").replace(":", "/").split("/")[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _load(project_dir: str | None):
    from cli.remote_dev import ProjectConfig

    project = ProjectConfig(resolve_project_dir(project_dir))
    project.load()
    return project


def _repos(project) -> list[dict[str, Any]]:
    return [r for r in (project.config.get("repos") or []) if isinstance(r, dict) and r.get("name")]


def _vault_names(project_dir: str | None) -> set[str]:
    try:
        return {name for name, _scope in list_all_vault_vars(project_dir)}
    except Exception:  # no vars files yet
        return set()


def _store_pat(name: str, url: str, project_dir: str | None) -> str | None:
    """Make a PAT available for *url*; return a repo-specific ``password_var`` if one was needed."""
    provider = detect_git_provider(url)
    if not provider:
        print(f"No PAT stored for '{name}' (unsupported git host; GitHub/GitLab only)")
        return None
    if provider_pat_var_name(provider) in _vault_names(project_dir):
        return None  # the provider default PAT already covers it
    repo = {"name": name, "url": url}
    try:
        if sys.stdin.isatty():
            assignments, defaults = collect_repo_pats([repo])
        else:
            value = os.getenv(PROVIDER_ENV_VARS[provider], "").strip()
            assignments, defaults = ({name: value}, {provider: value}) if value else ({}, {})
        if not assignments:
            print(f"No PAT stored for '{name}'; private clones need one (rig vars encrypt)")
            return None
        password_vars, to_store = resolve_repo_pat_var_names(assignments, [repo], default_pats=defaults)
        if not store_repo_pats(to_store, project_dir=project_dir):
            return None
    except (ValueError, RuntimeError) as exc:
        print(f"Warning: PAT not stored: {exc}", file=sys.stderr)
        return None
    var = password_vars.get(name)
    return None if var == provider_pat_var_name(provider) else var


def cmd_add(
    url: str,
    *,
    name: str | None = None,
    dest: str | None = None,
    no_pat: bool = False,
    project_dir: str | None = None,
) -> int:
    from cli.remote_dev import _valid_component_name

    https_url = to_https_git_url(url.strip())
    if https_url != url.strip():
        print(f"Converted remote to HTTPS: {https_url}")
    name = name or repo_name_from_url(https_url)
    if not _valid_component_name(name):
        print(f"Error: invalid repo name '{name}' (use --name)", file=sys.stderr)
        return 1
    project = _load(project_dir)
    for repo in _repos(project):
        if repo["name"] == name or repo.get("url") == https_url:
            print(f"Error: Repo '{repo['name']}' ({repo.get('url')}) is already in rig.yml", file=sys.stderr)
            return 1
    entry: dict[str, Any] = {"name": name, "url": https_url}
    if dest:
        entry["dest"] = dest
    if not no_pat:
        password_var = _store_pat(name, https_url, project_dir)
        if password_var:
            entry["password_var"] = password_var
    project.config["repos"] = [*(project.config.get("repos") or []), entry]
    if not project.save(write_vars=False):
        print("Error: could not write rig.yml", file=sys.stderr)
        return 1
    print(f"Added Repo '{name}' to rig.yml. Run `rig apply` to clone it (or pass --apply).")
    return 0


def cmd_remove(name: str, *, project_dir: str | None = None) -> int:
    project = _load(project_dir)
    known = [r["name"] for r in _repos(project)]
    if name not in known:
        print(f"Error: no Repo named '{name}' (known: {', '.join(known) or 'none'})", file=sys.stderr)
        return 1
    project.config["repos"] = [
        r for r in (project.config.get("repos") or []) if not (isinstance(r, dict) and r.get("name") == name)
    ]
    if not project.save(write_vars=False):
        print("Error: could not write rig.yml", file=sys.stderr)
        return 1
    print(f"Removed Repo '{name}' from rig.yml.")
    print("Running Environments keep their clone and any PAT var; delete the clone on the VM yourself if unwanted.")
    return 0


def cmd_list(*, project_dir: str | None = None) -> int:
    project = _load(project_dir)
    repos = _repos(project)
    if not repos:
        print("No Repos in rig.yml")
        return 0
    vault = _vault_names(project_dir)
    for repo in repos:
        url = str(repo.get("url") or "")
        provider = detect_git_provider(url)
        default_var = provider_pat_var_name(provider) if provider else None
        if repo.get("password_var"):
            pat = str(repo["password_var"])
        elif default_var and default_var in vault:
            pat = default_var
        else:
            pat = "-"
        print(f"{repo['name']}\t{url}\t{repo.get('dest') or '-'}\t{pat}")
    return 0
