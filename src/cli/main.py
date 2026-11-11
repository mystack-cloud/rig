#!/usr/bin/env python3
"""
Main entry point for the remote development environment CLI
"""

import argparse
import sys
import os
from typing import List, Optional

# Add the src directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from cli.storage import StorageManager
from cli.config import RigConfig
from cli.encrypt import interactive_decrypt, interactive_encrypt
from cli import DISTRIBUTION_NAME
from cli import log as log
from cli.last_env import clear_last_env, remember_last_env, resolve_env_name
from cli.last_session import (
    agent_args_request_session,
    clear_last_session,
    read_last_session,
    resume_args_for_agent,
)
from cli.remote_dev import (
    IMPLEMENTED_CLOUD_PROVIDERS,
    REMOTE_WORKSPACE_ROOT,
    ProjectConfig,
    RemoteDevEnvironment,
    host_block,
    interactive_env_init,
    normalize_cloud_provider,
)


def _load_credentials() -> bool:
    """Load ~/.rig/config into the environment when vars are unset."""
    try:
        RigConfig().apply_to_environ()
        return True
    except Exception as exc:
        log.warning("Could not load ~/.rig/config: %s", exc)
        return False

# Import package version. The two libraries export the same function name;
# bind them separately so the type checker does not see a redefinition.
try:
    from importlib.metadata import version as package_version
    __version__ = package_version(DISTRIBUTION_NAME)
except ImportError:
    try:
        from importlib_metadata import version as package_version_fallback
        __version__ = package_version_fallback(DISTRIBUTION_NAME)
    except ImportError:
        __version__ = "0.1.0"


def _cmd_create(args: argparse.Namespace) -> int:
    _load_credentials()

    storage_manager = None
    try:
        storage_manager = StorageManager()
    except ValueError:
        log.warning("Storage manager not available; state will not be saved")

    try:
        project = ProjectConfig(os.getcwd())
        project.load()
        host_cfg = host_block(project.config or {})
        if getattr(args, "provider", None):
            host_cfg["provider"] = normalize_cloud_provider(args.provider)
        provider = host_cfg.get("provider")
        if provider not in IMPLEMENTED_CLOUD_PROVIDERS:
            available = ", ".join(sorted(IMPLEMENTED_CLOUD_PROVIDERS))
            log.error(
                "Cloud provider '%s' is not available yet (supported now: %s)",
                provider,
                available,
            )
            return 1
        hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
        if not hetzner_token:
            log.error(
                "Hetzner credentials missing. "
                "Run 'rig init' or set HETZNER_CLOUD_API_TOKEN"
            )
            return 1
        if getattr(args, "type", None):
            host_cfg["server_type"] = args.type
        if getattr(args, "availability_zone", None):
            host_cfg["availability_zone"] = args.availability_zone
            host_cfg.pop("region", None)
        if getattr(args, "image", None):
            host_cfg["image"] = args.image

        env = RemoteDevEnvironment(
            name=args.name,
            config={"host": host_cfg},
            storage_manager=storage_manager,
        )
        force_system = bool(
            getattr(args, "force_system", False)
            or getattr(args, "force_playbooks", False)
        )
        force_workspace = bool(
            getattr(args, "force_workspace", False)
            or getattr(args, "force_playbooks", False)
        )
        if not env.setup(project_dir=os.getcwd()):
            return 1

        # Provision via Ansible (system.yml then workspace.yml) with live output.
        # Matching snapshots skip covered playbooks unless --force-* is set.
        if not env.provision(
            project_dir=os.getcwd(),
            stream=True,
            force_system=force_system,
            force_workspace=force_workspace,
        ):
            return 1

        log.info("Environment '%s' created", env.name)
        remember_last_env(env.name)
        if getattr(args, "no_attach", False):
            log.info("Skipped agent attach; reconnect with: rig attach / rig dev")
            log.debug("Reconnect: rig attach %s", env.name)
            return 0

        return env.attach_to_agent(project_dir=os.getcwd())
    except Exception as e:
        log.error("Error creating environment: %s", e)
        return 1


def _merge_agent_args(
    continue_: bool = False,
    session=None,
    resume=None,
    remainder=None,
) -> list:
    """Merge convenience flags with remainder args for the remote agent."""
    agent_args: list = []
    if continue_:
        agent_args.append("--continue")
    if session:
        agent_args.extend(["--session", session])
    if resume is not None:
        # ``--resume`` alone opens the picker; ``--resume ID`` picks a session.
        agent_args.append("--resume")
        if resume:
            agent_args.append(resume)
    extra = list(remainder or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    agent_args.extend(extra)
    return agent_args


def _attach_name_and_remainder(
    args: argparse.Namespace,
    remainder_attr: str = "agent_args",
):
    """
    Split env name from trailing argv.

    Argparse can put flag-like tokens into the optional ``name`` positional
    when users write ``rig attach -- --continue``; move those into remainder.
    """
    name = getattr(args, "name", None)
    remainder = list(getattr(args, remainder_attr, None) or [])
    if name and str(name).startswith("-"):
        remainder = [name] + remainder
        name = None
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    return name, remainder


def _cmd_attach(args: argparse.Namespace) -> int:
    _load_credentials()
    hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not hetzner_token:
        log.error("Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN")
        return 1

    positional_name, remainder = _attach_name_and_remainder(args)
    name = resolve_env_name(
        positional_name,
        getattr(args, "name_flag", None),
    )
    if not name:
        log.error("Environment name is required (no last environment remembered)")
        log.info("Usage: rig attach [name] [-c|--continue] [-- …agent args]")
        return 1

    agent_args = _merge_agent_args(
        continue_=getattr(args, "agent_continue", False),
        session=getattr(args, "agent_session", None),
        resume=getattr(args, "agent_resume", None),
        remainder=remainder,
    )

    storage_manager = None
    try:
        storage_manager = StorageManager()
    except ValueError:
        log.debug("Storage manager not available; resolving resources via Hetzner API")

    try:
        env = RemoteDevEnvironment(name=name, storage_manager=storage_manager)
        if not env.resolve_resources() or not env.vm_ip:
            log.error("Could not resolve environment '%s' (no VM IP found)", name)
            return 1

        ssh_key_path = f"/tmp/{env.ssh_key_name}"
        if not os.path.exists(ssh_key_path):
            log.error("SSH private key not found at %s", ssh_key_path)
            log.error("Re-run create on this machine, or restore the key before attaching")
            return 1

        project = ProjectConfig(os.getcwd())
        project.load()
        from cli.remote_dev import configured_agents
        from cli.agents import normalize_agent_type

        configured, default_agent = configured_agents(project)
        requested = getattr(args, "agent", None)
        if requested:
            try:
                agent_type = normalize_agent_type(requested)
            except ValueError as exc:
                log.error("%s", exc)
                return 1
            if agent_type not in configured:
                log.error(
                    "Coding agent '%s' is not enabled for this workspace "
                    "(enabled: %s)",
                    agent_type,
                    ", ".join(configured),
                )
                return 1
        else:
            agent_type = default_agent

        # Bare ``rig attach`` reopens the last remembered session when present.
        # With several agents installed, resume the one that owns that session
        # unless --agent picked a different one.
        if not agent_args_request_session(agent_args):
            stored = read_last_session(env.name)
            if stored:
                session_id, stored_agent = stored
                resume_agent = stored_agent or agent_type
                if (
                    not requested
                    and stored_agent
                    and stored_agent in configured
                ):
                    agent_type = stored_agent
                    resume_agent = stored_agent
                if requested and stored_agent and stored_agent != agent_type:
                    resume_agent = None
                elif stored_agent and stored_agent != agent_type:
                    resume_agent = None
                if resume_agent:
                    injected = resume_args_for_agent(resume_agent, session_id)
                    agent_args = injected + list(agent_args)
                    log.info(
                        "Resuming last %s session %s",
                        resume_agent,
                        session_id,
                    )
        if len(configured) > 1:
            log.info(
                "Workspace coding agents: %s (attaching to %s)",
                ", ".join(configured),
                agent_type,
            )

        env.ensure_firewall_access(project_dir=os.getcwd())
        remember_last_env(env.name)
        if agent_args:
            log.debug("Agent args: %s", " ".join(agent_args))
        return env.attach_to_agent(
            project_dir=os.getcwd(),
            agent_type=str(agent_type),
            agent_args=agent_args,
        )
    except Exception as e:
        log.error("Error attaching to environment: %s", e)
        return 1


def _cmd_ssh(args: argparse.Namespace) -> int:
    """Open a shell on an existing environment, or run one remote command."""
    _load_credentials()
    hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not hetzner_token:
        log.error("Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN")
        return 1

    tokens = list(getattr(args, "ssh_args", None) or [])
    positional_name = None
    remainder: list = []
    if tokens and tokens[0] == "--":
        remainder = tokens[1:]
    elif tokens:
        positional_name = tokens[0]
        remainder = tokens[1:]
        if remainder and remainder[0] == "--":
            remainder = remainder[1:]
    name = resolve_env_name(
        positional_name,
        getattr(args, "name_flag", None),
    )
    if not name:
        log.error("Environment name is required (no last environment remembered)")
        log.info("Usage: rig ssh [name] [-- command]")
        return 1

    storage_manager = None
    try:
        storage_manager = StorageManager()
    except ValueError:
        log.debug("Storage manager not available; resolving resources via Hetzner API")

    try:
        env = RemoteDevEnvironment(name=name, storage_manager=storage_manager)
        if not env.resolve_resources() or not env.vm_ip:
            log.error("Could not resolve environment '%s' (no VM IP found)", name)
            return 1

        ssh_key_path = f"/tmp/{env.ssh_key_name}"
        if not os.path.exists(ssh_key_path):
            log.error("SSH private key not found at %s", ssh_key_path)
            log.error("Re-run create on this machine, or restore the key before connecting")
            return 1

        env.ensure_firewall_access(project_dir=os.getcwd())
        remember_last_env(env.name)
        import shlex
        remote = shlex.join(remainder) if remainder else None
        return env.open_ssh(remote_command=remote)
    except Exception as e:
        log.error("Error connecting to environment: %s", e)
        return 1


def _cmd_apply(args: argparse.Namespace) -> int:
    """Re-run lifecycle playbooks on an existing environment."""
    _load_credentials()
    hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not hetzner_token:
        log.error("Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN")
        return 1

    name = resolve_env_name(
        getattr(args, "name", None),
        getattr(args, "name_flag", None),
    )
    if not name:
        log.error("Environment name is required (no last environment remembered)")
        log.info(
            "Usage: rig apply [name] "
            "[--provision|--configure|--authorize|--materialize|--finalize|--destroy] "
            "[--tags TAGS]"
        )
        return 1

    # Legacy flag aliases.
    want_provision = getattr(args, "provision", False) or getattr(args, "system", False)
    want_configure = getattr(args, "configure", False)
    want_authorize = getattr(args, "authorize", False) or getattr(args, "authnz", False)
    want_materialize = getattr(args, "materialize", False)
    want_finalize = getattr(args, "finalize", False)
    want_destroy_playbook = getattr(args, "destroy", False)

    selected = []
    if want_provision:
        selected.extend(["provision", "configure", "authorize"])
    if want_configure and "configure" not in selected:
        selected.append("configure")
    authorize_only = (
        want_authorize
        and not want_provision
        and not want_configure
        and not want_materialize
        and not want_finalize
        and not want_destroy_playbook
    )
    if authorize_only:
        selected = ["authorize"]
    elif want_authorize and "authorize" not in selected:
        selected.append("authorize")
    if want_materialize:
        if "authorize" not in selected and not want_provision:
            selected.append("authorize")
        if "materialize" not in selected:
            selected.append("materialize")
    if want_finalize and "finalize" not in selected:
        selected.append("finalize")
    if want_destroy_playbook and "destroy" not in selected:
        selected.append("destroy")
    if not selected:
        # Default apply: re-authorize then materialize.
        selected = ["authorize", "materialize"]

    # Preserve lifecycle order. finalize/destroy run the playbooks only;
    # deleting the VM is ``rig delete``.
    order = ("provision", "configure", "authorize", "materialize", "finalize", "destroy")
    playbooks = tuple(stage for stage in order if stage in selected)

    storage_manager = None
    try:
        storage_manager = StorageManager()
    except ValueError:
        log.debug("Storage manager not available; resolving resources via Hetzner API")

    try:
        env = RemoteDevEnvironment(name=name, storage_manager=storage_manager)
        if not env.resolve_resources() or not env.vm_ip:
            log.error("Could not resolve environment '%s' (no VM IP found)", name)
            return 1

        ssh_key_path = f"/tmp/{env.ssh_key_name}"
        if not os.path.exists(ssh_key_path):
            log.error("SSH private key not found at %s", ssh_key_path)
            log.error("Re-run create on this machine, or restore the key before applying")
            return 1

        env.ensure_firewall_access(project_dir=os.getcwd())
        remember_last_env(env.name)
        ok = env.apply_playbooks(
            project_dir=os.getcwd(),
            stream=True,
            playbooks=playbooks,
            soft_fail_materialize=False,
            action="apply",
            ansible_tags=getattr(args, "tags", None),
        )
        return 0 if ok else 1
    except Exception as e:
        log.error("Error applying playbooks: %s", e)
        return 1


def _cmd_dev(args: argparse.Namespace) -> int:
    _load_credentials()
    hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not hetzner_token:
        log.error("Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN")
        return 1

    name = resolve_env_name(
        getattr(args, "name", None),
        getattr(args, "name_flag", None),
    )
    if not name:
        log.error("Environment name is required (no last environment remembered)")
        log.info("Usage: rig dev [name]   or   rig dev --name <name>")
        return 1

    storage_manager = None
    try:
        storage_manager = StorageManager()
    except ValueError:
        log.debug("Storage manager not available; resolving resources via Hetzner API")

    try:
        env = RemoteDevEnvironment(name=name, storage_manager=storage_manager)
        if not env.resolve_resources() or not env.vm_ip:
            log.error("Could not resolve environment '%s' (no VM IP found)", name)
            return 1

        env.ensure_firewall_access(project_dir=os.getcwd())
        remember_last_env(env.name)
        from cli.remote_dev import configured_editors, normalize_editor_type

        project = ProjectConfig(os.getcwd())
        project.load()
        enabled, default_editor = configured_editors(project)
        requested = getattr(args, "editor", None)
        editor: Optional[str] = None
        editor_fallbacks: Optional[List[str]] = None
        if requested:
            try:
                editor = normalize_editor_type(requested)
            except ValueError as exc:
                log.error("%s", exc)
                return 1
            if enabled and editor not in enabled:
                log.error(
                    "Editor '%s' is not enabled for this workspace (enabled: %s)",
                    editor,
                    ", ".join(enabled),
                )
                return 1
            editor_fallbacks = []
        else:
            editor = default_editor
            editor_fallbacks = list(enabled) if enabled else None
        if len(enabled) > 1 and editor:
            log.info(
                "Workspace editors: %s (opening %s)",
                ", ".join(enabled),
                editor,
            )
        return env.open_vscode(
            remote_path=getattr(args, "path", None) or REMOTE_WORKSPACE_ROOT,
            project_dir=os.getcwd(),
            browser=bool(getattr(args, "browser", False)),
            editor=editor,
            editor_fallbacks=editor_fallbacks,
        )
    except Exception as e:
        log.error("Error opening VS Code: %s", e)
        return 1


def _cmd_list(_args: argparse.Namespace) -> int:
    _load_credentials()
    hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not hetzner_token:
        log.error("Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN")
        return 1

    storage_manager = None
    try:
        storage_manager = StorageManager()
    except ValueError:
        pass

    try:
        env = RemoteDevEnvironment(storage_manager=storage_manager)
        environments = env.list_environments()
        if not environments:
            log.info("No remote environments")
            return 0
        log.info("Remote environments:")
        for env_info in environments:
            log.info("  - %s (%s)", env_info.get("name"), env_info.get("status"))
        return 0
    except Exception as e:
        log.error("Error listing environments: %s", e)
        return 1


def _cmd_delete(args: argparse.Namespace) -> int:
    _load_credentials()
    hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not hetzner_token:
        log.error("Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN")
        return 1

    names: list = []
    for value in (getattr(args, "names", None) or []):
        if value and value not in names:
            names.append(value)
    # Back-compat: --name can still be passed (once or repeated).
    for value in (getattr(args, "name_flags", None) or []):
        if value and value not in names:
            names.append(value)
    if getattr(args, "name", None):
        if args.name not in names:
            names.append(args.name)

    if not names:
        last = resolve_env_name()
        if last:
            names.append(last)
            log.info("Using last environment '%s'", last)
        else:
            log.error("At least one environment name is required (no last environment remembered)")
            log.info("Usage: rig delete [name ...] [--tags TAGS]   or   rig delete --name <name>")
            return 1

    storage_manager = None
    try:
        storage_manager = StorageManager()
    except ValueError:
        log.debug("Storage manager not available; resolving resources via Hetzner API")

    failures = []
    for name in names:
        try:
            env = RemoteDevEnvironment(name=name, storage_manager=storage_manager)
            env.cleanup(
                project_dir=os.getcwd(),
                ansible_tags=getattr(args, "tags", None),
            )
            clear_last_env(name)
            clear_last_session(name)
            log.info("Environment '%s' deleted", name)
        except Exception as e:
            log.error("Error deleting environment '%s': %s", name, e)
            failures.append(name)

    if failures:
        log.error("Failed to delete: %s", ", ".join(failures))
        return 1
    return 0


def _cache_scope_flags(args: argparse.Namespace) -> tuple:
    """Return ``(include_system, include_workspace)`` from CLI flags."""
    want_system = bool(getattr(args, "system", False))
    want_workspace = bool(getattr(args, "workspace", False))
    if want_system and not want_workspace:
        return True, False
    if want_workspace and not want_system:
        return False, True
    # Neither or both → both kinds.
    return True, True


def _cache_select_snapshots(
    images,
    args: argparse.Namespace,
) -> tuple:
    """
    Build ``(keep, targets, label)`` for cache list/clear.

    Without ``--all``: targets are unused (current omitted into keep).
    With ``--all``: targets include current; keep is empty.
    ``--system`` / ``--workspace`` narrow the kind(s).
    """
    from cli.snapshots import (
        describe_snapshot,
        find_unused_snapshots,
        package_version,
        system_content_hash,
        system_hash_inputs_from_project,
        workspace_content_hash,
    )

    include_system, include_workspace = _cache_scope_flags(args)
    clear_all = bool(getattr(args, "all", False))

    scope_bits = []
    if include_system:
        scope_bits.append("system")
    if include_workspace:
        scope_bits.append("workspace")
    scope = "+".join(scope_bits) or "snapshot"

    if clear_all:
        targets = []
        for image in images:
            if image.get("type") and image.get("type") != "snapshot":
                continue
            meta = describe_snapshot(image)
            kind = meta.get("kind")
            if kind == "system" and include_system:
                meta["reason"] = "all"
                targets.append(meta)
            elif kind == "workspace" and include_workspace:
                # --all --workspace: every workspace snapshot (any project).
                # Narrow to current project name when rig.yml is available.
                meta["reason"] = "all"
                targets.append(meta)
        # Optionally restrict workspace --all to the current project when possible.
        if include_workspace and not include_system:
            project_dir = os.getcwd()
            project = ProjectConfig(project_dir)
            if project.load() and project.config:
                ws_name = project.config.get("name") or os.path.basename(
                    project_dir.rstrip(os.sep)
                )
                from cli.snapshots import sanitize_snapshot_token

                cur = sanitize_snapshot_token(str(ws_name), max_len=40)
                targets = [
                    m
                    for m in targets
                    if m.get("kind") != "workspace" or m.get("workspace") == cur
                ]
        label = f"{scope} snapshot(s)"
        return [], targets, label

    project_dir = os.getcwd()
    project = ProjectConfig(project_dir)
    if not project.load() or not project.config:
        raise RuntimeError(
            f"No rig.yml in {project_dir}; cannot determine current snapshots "
            "(use --all to include every matching snapshot)"
        )

    workspace_name = project.config.get("name") or os.path.basename(
        project_dir.rstrip(os.sep)
    )
    workspace_hash = workspace_content_hash(project_dir, project=project)
    version = package_version()
    sys_inputs = system_hash_inputs_from_project(project)
    system_hash = system_content_hash(
        project=project,
        var_map=sys_inputs.get("var_map"),
        project_dir=project_dir,
    )

    split = find_unused_snapshots(
        images,
        current_version=version,
        current_system_hash=system_hash,
        current_workspace_name=str(workspace_name),
        current_workspace_hash=workspace_hash,
        include_system=include_system,
        include_workspace=include_workspace,
    )
    label = f"unused {scope} snapshot(s)"
    return split["keep"], split["unused"], label


def _cmd_cache(args: argparse.Namespace) -> int:
    """List or clear Hetzner snapshots (unused by default; --all includes current)."""
    action = getattr(args, "action", None) or "clear"
    if action not in ("clear", "list"):
        log.error("Unknown cache action %r (supported: clear, list)", action)
        return 1

    _load_credentials()
    hetzner_token = os.getenv("HETZNER_CLOUD_API_TOKEN")
    if not hetzner_token:
        log.error(
            "Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN"
        )
        return 1

    assume_yes = bool(getattr(args, "yes", False))

    try:
        env = RemoteDevEnvironment(storage_manager=None)
        images = env.manager.list_snapshots()
    except Exception as exc:
        log.error("Failed to list snapshots: %s", exc)
        return 1

    try:
        keep, targets, label = _cache_select_snapshots(images, args)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    if keep:
        log.info("Current (kept) snapshot(s):")
        for meta in keep:
            log.info(
                "  %s (id=%s)",
                meta.get("description"),
                meta.get("id"),
            )

    if not targets and not keep:
        log.info("No matching snapshots")
        return 0

    if targets:
        heading = "All" if getattr(args, "all", False) else "Unused"
        log.info("%s (%s):", heading, len(targets))
        for meta in targets:
            reason = meta.get("reason")
            if reason and reason != "all":
                log.info(
                    "  %s (id=%s) — %s",
                    meta.get("description"),
                    meta.get("id"),
                    reason,
                )
            else:
                log.info(
                    "  %s (id=%s)",
                    meta.get("description"),
                    meta.get("id"),
                )
    elif action == "list":
        log.info("No unused snapshots")
        return 0

    if action == "list":
        return 0

    if not targets:
        log.info("No %s to clear", label)
        return 0

    if not assume_yes:
        try:
            answer = input(
                f"Delete {len(targets)} {label}? [y/N]: "
            ).strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            log.info("Cancelled")
            return 0

    failures = []
    deleted = 0
    for meta in targets:
        image_id = meta.get("id")
        description = meta.get("description") or image_id
        if image_id is None:
            continue
        log.info("Deleting snapshot %s (id=%s)", description, image_id)
        if not env.manager.delete_image(str(image_id)):
            failures.append(str(description))
        else:
            deleted += 1

    if failures:
        log.error("Failed to delete: %s", ", ".join(failures))
        return 1
    log.info("Cleared %s %s", deleted, label)
    return 0


def _cmd_encrypt(args: argparse.Namespace) -> int:
    try:
        personal = bool(getattr(args, "personal", False))
        ciphertext = interactive_encrypt(
            stdin_name=args.stdin_name,
            personal=personal if personal else None,
        )
    except (ValueError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1
    if ciphertext:
        print(ciphertext)
    return 0


def _cmd_decrypt(args: argparse.Namespace) -> int:
    try:
        plaintext = interactive_decrypt(name=args.name)
    except (ValueError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1
    print(plaintext, end="" if plaintext.endswith("\n") else "\n")
    return 0


def _cmd_vars_list(args: argparse.Namespace) -> int:
    from cli.encrypt import list_all_vault_vars

    rows = list_all_vault_vars()
    if not rows:
        print("No vaulted variables found")
        return 0
    for name, scope in rows:
        print(f"{name}\t{scope}")
    return 0


def _cmd_vars_export_shared_key(args: argparse.Namespace) -> int:
    from cli.encrypt import export_shared_data_key

    try:
        key = export_shared_data_key()
    except (ValueError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1
    print(key)
    return 0


def main(argv=None):
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="RIG - Remote Development Environment CLI"
    )
    parser.add_argument("--version", action="version", version=f"rig {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help=(
            "More detail. -v shows each playbook task and debug logs; "
            "-vv, -vvv, and -vvvv pass that verbosity to ansible-playbook"
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Show warnings and errors only",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser(
        "init",
        help="Interactively initialize a multi-repo environment from nearby git projects",
    )

    create_parser = subparsers.add_parser(
        "create",
        help="Create a remote environment, run Ansible playbooks, attach to agent",
    )
    create_parser.add_argument("--name", help="Environment name")
    create_parser.add_argument(
        "--provider",
        default=None,
        help=(
            "Cloud provider (default: from rig.yml host.provider, else hetzner). "
            "hetzner is available; aws and gcp are recognized but not yet"
        ),
    )
    create_parser.add_argument(
        "--type",
        default=None,
        help="Hetzner server type (default: from rig.yml host.server_type, else cax11)",
    )
    create_parser.add_argument(
        "--availability-zone",
        "--region",
        dest="availability_zone",
        default=None,
        help="Hetzner location / AZ (default: from rig.yml host.availability_zone, else hel1)",
    )
    create_parser.add_argument(
        "--image",
        default=None,
        help="Boot image (default: from rig.yml host.image, else ubuntu-24.04)",
    )
    create_parser.add_argument(
        "--no-attach",
        action="store_true",
        help="Skip attaching to the coding agent TTY after provisioning",
    )
    create_parser.add_argument(
        "--force-playbooks",
        action="store_true",
        help=(
            "Run provision/configure/materialize even when booting from a "
            "matching snapshot (authorize always runs)"
        ),
    )
    create_parser.add_argument(
        "--force-system",
        action="store_true",
        help="Force provision+configure even when booting from a system snapshot",
    )
    create_parser.add_argument(
        "--force-workspace",
        action="store_true",
        help="Force materialize even when it would otherwise be skipped",
    )

    apply_parser = subparsers.add_parser(
        "apply",
        help=(
            "Re-run lifecycle playbooks on an existing env "
            "(default: authorize then materialize)"
        ),
    )
    apply_parser.add_argument(
        "name",
        nargs="?",
        help="Environment name (defaults to last handled env in /tmp/.rig/last-env)",
    )
    apply_parser.add_argument(
        "--name",
        dest="name_flag",
        help="Environment name (kept for compatibility)",
    )
    apply_parser.add_argument(
        "--provision",
        action="store_true",
        help="Run provision → configure → authorize",
    )
    apply_parser.add_argument(
        "--configure",
        action="store_true",
        help="Run configure.yml",
    )
    apply_parser.add_argument(
        "--authorize",
        action="store_true",
        help="Run authorize.yml only (re-apply credentials)",
    )
    apply_parser.add_argument(
        "--materialize",
        action="store_true",
        help="Run authorize then materialize (or materialize with other flags)",
    )
    apply_parser.add_argument(
        "--finalize",
        action="store_true",
        help="Run finalize.yml (does not delete the VM)",
    )
    apply_parser.add_argument(
        "--destroy",
        action="store_true",
        help="Run destroy.yml (does not delete the VM)",
    )
    apply_parser.add_argument(
        "--tags",
        action="append",
        default=None,
        metavar="TAGS",
        help=(
            "Ansible --tags for every playbook in this run "
            "(comma-separated; may be repeated)."
        ),
    )
    # Legacy aliases
    apply_parser.add_argument(
        "--system",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    apply_parser.add_argument(
        "--authnz",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    attach_parser = subparsers.add_parser(
        "attach",
        help=(
            "Attach to a remote coding-agent TTY (uses last env if name omitted; "
            "reopens the last remembered agent session when no --resume/--session/-c)"
        ),
    )
    attach_parser.add_argument(
        "name",
        nargs="?",
        help="Environment name (defaults to last handled env in /tmp/.rig/last-env)",
    )
    attach_parser.add_argument(
        "--name",
        dest="name_flag",
        help="Environment name (kept for compatibility)",
    )
    attach_parser.add_argument(
        "-c",
        "--continue",
        dest="agent_continue",
        action="store_true",
        help="Continue the last coding-agent session (forwards --continue)",
    )
    attach_parser.add_argument(
        "-s",
        "--session",
        dest="agent_session",
        metavar="ID",
        help="Continue a specific OpenCode session id (forwards --session)",
    )
    attach_parser.add_argument(
        "--resume",
        dest="agent_resume",
        nargs="?",
        const="",
        metavar="ID",
        help="Resume a Claude Code / Cursor session (forwards --resume [ID])",
    )
    attach_parser.add_argument(
        "--agent",
        dest="agent",
        help=(
            "Coding agent to open when several are configured "
            "(opencode, claude, or cursor). Defaults to agent.default"
        ),
    )
    attach_parser.add_argument(
        "agent_args",
        nargs=argparse.REMAINDER,
        help="Extra args forwarded to opencode/claude/agent (put -- before flags)",
    )

    ssh_parser = subparsers.add_parser(
        "ssh",
        help="Open an SSH shell on the remote env (uses last env if name omitted)",
    )
    ssh_parser.add_argument(
        "--name",
        dest="name_flag",
        help="Environment name (kept for compatibility)",
    )
    ssh_parser.add_argument(
        "ssh_args",
        nargs=argparse.REMAINDER,
        help="[name] [-- command]. Default: login shell in /workspace",
    )

    dev_parser = subparsers.add_parser(
        "dev",
        help="Open VS Code / Cursor on the remote env (uses last env if name omitted)",
        description=(
            "Open VS Code or Cursor against the remote environment via Remote-SSH "
            "(default). Pass --browser to open code-server in a browser instead."
        ),
    )
    dev_parser.add_argument(
        "name",
        nargs="?",
        help="Environment name (defaults to last handled env in /tmp/.rig/last-env)",
    )
    dev_parser.add_argument(
        "--name",
        dest="name_flag",
        help="Environment name (kept for compatibility)",
    )
    dev_parser.add_argument(
        "--path",
        default=REMOTE_WORKSPACE_ROOT,
        help=f"Remote folder to open (default: {REMOTE_WORKSPACE_ROOT})",
    )
    dev_parser.add_argument(
        "--browser",
        action="store_true",
        help="Open code-server in the browser instead of local VS Code/Cursor",
    )
    dev_parser.add_argument(
        "--editor",
        help=(
            "Local editor to open when several are configured "
            "(code, code-insiders, cursor, or codium). "
            "Defaults to editor.default in rig.yml"
        ),
    )

    subparsers.add_parser("list", help="List remote environments")

    cache_parser = subparsers.add_parser(
        "cache",
        help="Manage Hetzner snapshots created after the configure (build) stage",
        description=(
            "List or clear VM snapshots after showing them. "
            "Default clear: unused only (older version or different hash); "
            "current snapshots are kept. "
            "--all includes current. "
            "--system / --workspace narrow the kind "
            "(combine with or without --all)."
        ),
    )
    cache_parser.add_argument(
        "--system",
        action="store_true",
        help="Only system snapshots (rig-{version}-{hash})",
    )
    cache_parser.add_argument(
        "--workspace",
        action="store_true",
        help="Only workspace snapshots (workspace-{name}-{hash}) for this project",
    )
    cache_parser.add_argument(
        "--all",
        action="store_true",
        help="Include current snapshots (default omits them / unused only)",
    )
    cache_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Delete without confirmation (clear only)",
    )
    cache_parser.add_argument(
        "action",
        nargs="?",
        default="clear",
        choices=["clear", "list"],
        help="list = show only; clear = list, confirm, delete (default: clear)",
    )

    delete_parser = subparsers.add_parser(
        "delete",
        help=(
            "Delete remote environments (runs finalize then destroy playbooks first; "
            "uses last env if name omitted)"
        ),
    )
    delete_parser.add_argument(
        "names",
        nargs="*",
        metavar="name",
        help="Environment name(s) to delete (defaults to last handled env in /tmp/.rig/last-env)",
    )
    delete_parser.add_argument(
        "--name",
        action="append",
        dest="name_flags",
        help="Environment name (repeatable; kept for compatibility)",
    )
    delete_parser.add_argument(
        "--tags",
        action="append",
        default=None,
        metavar="TAGS",
        help=(
            "Ansible --tags for the finalize and destroy playbooks "
            "(comma-separated; may be repeated)"
        ),
    )

    vars_parser = subparsers.add_parser(
        "vars",
        help="Manage vaulted values in .rig/vars.yml and vars.{identity}.yml",
    )
    vars_subparsers = vars_parser.add_subparsers(
        dest="vars_command",
        help="Vars actions",
    )

    encrypt_parser = vars_subparsers.add_parser(
        "encrypt",
        help=(
            "Encrypt a value (shared vars.yml, or identity file with --personal / PAT names)"
        ),
    )
    encrypt_parser.add_argument(
        "--stdin-name",
        help="Variable name; read multiline value from stdin until Ctrl+D",
    )
    encrypt_parser.add_argument(
        "--personal",
        action="store_true",
        help="Store in .rig/vars.{identity}.yml (personal vault)",
    )

    decrypt_parser = vars_subparsers.add_parser(
        "decrypt",
        help="Decrypt a variable from shared or personal vars (after master-key unlock)",
    )
    decrypt_parser.add_argument(
        "name",
        nargs="?",
        help="Variable name to decrypt (prompted if omitted)",
    )

    vars_subparsers.add_parser(
        "list",
        help="List vaulted variable names and whether they are shared or identity",
    )
    vars_subparsers.add_parser(
        "export-shared-key",
        help="Print the shared data key (for teammate onboarding; requires unlock)",
    )

    args = parser.parse_args(argv)
    verbosity = int(getattr(args, "verbose", 0) or 0)
    log.configure_logging(
        verbose=verbosity > 0,
        quiet=bool(getattr(args, "quiet", False)),
        verbosity=verbosity,
    )

    if args.command == "init":
        return 0 if interactive_env_init() else 1
    if args.command == "create":
        return _cmd_create(args)
    if args.command == "apply":
        return _cmd_apply(args)
    if args.command == "attach":
        return _cmd_attach(args)
    if args.command == "ssh":
        return _cmd_ssh(args)
    if args.command == "dev":
        return _cmd_dev(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "cache":
        return _cmd_cache(args)
    if args.command == "delete":
        return _cmd_delete(args)
    if args.command == "vars":
        if args.vars_command == "encrypt":
            return _cmd_encrypt(args)
        if args.vars_command == "decrypt":
            return _cmd_decrypt(args)
        if args.vars_command == "list":
            return _cmd_vars_list(args)
        if args.vars_command == "export-shared-key":
            return _cmd_vars_export_shared_key(args)
        vars_parser.print_help()
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
