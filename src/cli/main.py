#!/usr/bin/env python3
"""
Main entry point for the remote development environment CLI
"""

import argparse
import sys
import os

# Add the src directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from cli.storage import StorageManager
from cli.config import RigConfig
from cli.encrypt import interactive_decrypt, interactive_encrypt
from cli import log as log
from cli.last_env import clear_last_env, remember_last_env, resolve_env_name
from cli.remote_dev import (
    REMOTE_WORKSPACE_ROOT,
    ProjectConfig,
    RemoteDevEnvironment,
    interactive_env_init,
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
    __version__ = package_version("rig")
except ImportError:
    try:
        from importlib_metadata import version as package_version_fallback
        __version__ = package_version_fallback("rig")
    except ImportError:
        __version__ = "0.1.0"


def _cmd_create(args: argparse.Namespace) -> int:
    _load_credentials()
    hetzner_token = os.getenv('HETZNER_CLOUD_API_TOKEN')
    if not hetzner_token:
        log.error("Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN")
        return 1

    storage_manager = None
    try:
        storage_manager = StorageManager()
    except ValueError:
        log.warning("Storage manager not available; state will not be saved")

    try:
        project = ProjectConfig(os.getcwd())
        project.load()
        vm_cfg = dict((project.config or {}).get("vm") or {})
        if getattr(args, "type", None):
            vm_cfg["type"] = args.type
        if getattr(args, "availability_zone", None):
            vm_cfg["availability_zone"] = args.availability_zone
            vm_cfg.pop("region", None)
        if getattr(args, "image", None):
            vm_cfg["image"] = args.image
        vm_cfg.setdefault("type", "cax11")
        vm_cfg.setdefault(
            "availability_zone",
            vm_cfg.pop("region", None) or "hel1",
        )
        vm_cfg.setdefault("image", "ubuntu-24.04")

        env = RemoteDevEnvironment(
            name=args.name,
            config={"vm": vm_cfg},
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

        log.info("Environment created")
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


def _attach_name_and_remainder(args: argparse.Namespace):
    """
    Split env name from agent argv.

    Argparse can put flag-like tokens into the optional ``name`` positional
    when users write ``rig attach -- --continue``; move those into remainder.
    """
    name = getattr(args, "name", None)
    remainder = list(getattr(args, "agent_args", None) or [])
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

        env.ensure_firewall_access(project_dir=os.getcwd())
        remember_last_env(env.name)
        log.info("Attaching to '%s' (%s)", env.name, env.vm_ip)
        if agent_args:
            log.debug("Agent args: %s", " ".join(agent_args))
        return env.attach_to_agent(project_dir=os.getcwd(), agent_args=agent_args)
    except Exception as e:
        log.error("Error attaching to environment: %s", e)
        return 1


def _cmd_apply(args: argparse.Namespace) -> int:
    """Re-run Ansible playbooks on an existing environment (default: workspace.yml)."""
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
        log.info("Usage: rig apply [name] [--system]")
        return 1

    playbooks = ("system",) if getattr(args, "system", False) else ("workspace",)

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
            soft_fail_workspace=False,
            action="apply",
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
        return env.open_vscode(
            remote_path=getattr(args, "path", None) or REMOTE_WORKSPACE_ROOT,
            project_dir=os.getcwd(),
            browser=bool(getattr(args, "browser", False)),
            editor=getattr(args, "editor", None),
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


def _cmd_destroy(args: argparse.Namespace) -> int:
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
            log.info("Usage: rig destroy [name ...]   or   rig destroy --name <name>")
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
            env.cleanup(project_dir=os.getcwd())
            clear_last_env(name)
            log.info("Environment '%s' destroyed", name)
        except Exception as e:
            log.error("Error destroying environment '%s': %s", name, e)
            failures.append(name)

    if failures:
        log.error("Failed to destroy: %s", ", ".join(failures))
        return 1
    return 0


def _cmd_cache(args: argparse.Namespace) -> int:
    """Clear Hetzner snapshots for the current workspace or system playbook."""
    action = getattr(args, "action", None) or "clear"
    if action != "clear":
        log.error("Unknown cache action %r (supported: clear)", action)
        return 1

    _load_credentials()
    hetzner_token = os.getenv("HETZNER_CLOUD_API_TOKEN")
    if not hetzner_token:
        log.error(
            "Hetzner credentials missing. Run 'rig init' or set HETZNER_CLOUD_API_TOKEN"
        )
        return 1

    from cli.snapshots import filter_snapshots

    clear_system = bool(getattr(args, "system", False))
    workspace_name = None
    if not clear_system:
        project = ProjectConfig(os.getcwd())
        if not project.load() or not project.config:
            log.error("No rig.yml in %s; cannot determine workspace snapshots", os.getcwd())
            return 1
        workspace_name = project.config.get("name") or os.path.basename(
            os.getcwd().rstrip(os.sep)
        )

    try:
        env = RemoteDevEnvironment(storage_manager=None)
        images = env.manager.list_snapshots()
    except Exception as exc:
        log.error("Failed to list snapshots: %s", exc)
        return 1

    targets = filter_snapshots(
        images,
        system=clear_system,
        workspace_name=None if clear_system else str(workspace_name),
    )
    if not targets:
        scope = "system" if clear_system else f"workspace '{workspace_name}'"
        log.info("No %s snapshots to clear", scope)
        return 0

    failures = []
    for image in targets:
        image_id = image.get("id")
        description = image.get("description") or image_id
        if image_id is None:
            continue
        log.info("Deleting snapshot %s (id=%s)", description, image_id)
        if not env.manager.delete_image(str(image_id)):
            failures.append(str(description))

    if failures:
        log.error("Failed to delete: %s", ", ".join(failures))
        return 1
    log.info("Cleared %s snapshot(s)", len(targets))
    return 0


def _cmd_encrypt(args: argparse.Namespace) -> int:
    try:
        ciphertext = interactive_encrypt(stdin_name=args.stdin_name)
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


def main(argv=None):
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="RIG - Remote Development Environment CLI"
    )
    parser.add_argument("--version", action="version", version=f"rig {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show debug logs (IDs, playbook paths, SSH details)",
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
        "--type",
        default=None,
        help="VM type (default: from rig.yml vm.type, else cax11)",
    )
    create_parser.add_argument(
        "--availability-zone",
        "--region",
        dest="availability_zone",
        default=None,
        help="Hetzner location / AZ (default: from rig.yml vm.availability_zone, else hel1)",
    )
    create_parser.add_argument(
        "--image",
        default=None,
        help="VM image (default: from rig.yml vm.image, else ubuntu-24.04)",
    )
    create_parser.add_argument(
        "--no-attach",
        action="store_true",
        help="Skip attaching to the coding agent TTY after provisioning",
    )
    create_parser.add_argument(
        "--force-playbooks",
        action="store_true",
        help="Run system.yml and workspace.yml even when booting from a matching snapshot",
    )
    create_parser.add_argument(
        "--force-system",
        action="store_true",
        help="Run system.yml even when booting from a system/workspace snapshot",
    )
    create_parser.add_argument(
        "--force-workspace",
        action="store_true",
        help="Run workspace.yml even when booting from a matching workspace snapshot",
    )

    apply_parser = subparsers.add_parser(
        "apply",
        help=(
            "Re-run Ansible playbooks on an existing env "
            "(default: workspace.yml; use --system for system.yml)"
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
        "--system",
        action="store_true",
        help="Run .rig/system.yml instead of workspace.yml",
    )

    attach_parser = subparsers.add_parser(
        "attach",
        help="Attach to a remote coding-agent TTY (uses last env if name omitted)",
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
        help="Resume a Claude Code session (forwards --resume [ID])",
    )
    attach_parser.add_argument(
        "agent_args",
        nargs=argparse.REMAINDER,
        help="Extra args forwarded to opencode/claude (put -- before flags)",
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
        choices=["code", "code-insiders", "cursor", "codium"],
        help="Prefer a specific local editor CLI",
    )

    subparsers.add_parser("list", help="List remote environments")

    cache_parser = subparsers.add_parser(
        "cache",
        help="Manage Hetzner snapshots created after system/workspace playbooks",
        description=(
            "Clear VM snapshots. "
            "'rig cache clear' removes workspace snapshots for the current project; "
            "'rig cache --system clear' removes system.yml snapshots (rig-{version})."
        ),
    )
    cache_parser.add_argument(
        "--system",
        action="store_true",
        help="Target system.yml snapshots (rig-{version}) instead of workspace snapshots",
    )
    cache_parser.add_argument(
        "action",
        nargs="?",
        default="clear",
        choices=["clear"],
        help="Cache action (default: clear)",
    )

    destroy_parser = subparsers.add_parser(
        "destroy",
        help=(
            "Destroy remote environments (runs playbooks.finalizers first; "
            "uses last env if name omitted)"
        ),
    )
    destroy_parser.add_argument(
        "names",
        nargs="*",
        metavar="name",
        help="Environment name(s) to destroy (defaults to last handled env in /tmp/.rig/last-env)",
    )
    destroy_parser.add_argument(
        "--name",
        action="append",
        dest="name_flags",
        help="Environment name (repeatable; kept for compatibility)",
    )

    vars_parser = subparsers.add_parser(
        "vars",
        help="Manage vaulted values in .rig/vars.yml",
    )
    vars_subparsers = vars_parser.add_subparsers(
        dest="vars_command",
        help="Vars actions",
    )

    encrypt_parser = vars_subparsers.add_parser(
        "encrypt",
        help=(
            "Encrypt a value with a temporary data key and add it to .rig/vars.yml"
        ),
    )
    encrypt_parser.add_argument(
        "--stdin-name",
        help="Variable name; read multiline value from stdin until Ctrl+D",
    )

    decrypt_parser = vars_subparsers.add_parser(
        "decrypt",
        help="Decrypt a variable from .rig/vars.yml using the temporary data key",
    )
    decrypt_parser.add_argument(
        "name",
        nargs="?",
        help="Variable name to decrypt (prompted if omitted)",
    )

    args = parser.parse_args(argv)
    log.configure_logging(
        verbose=bool(getattr(args, "verbose", False)),
        quiet=bool(getattr(args, "quiet", False)),
    )

    if args.command == "init":
        return 0 if interactive_env_init() else 1
    if args.command == "create":
        return _cmd_create(args)
    if args.command == "apply":
        return _cmd_apply(args)
    if args.command == "attach":
        return _cmd_attach(args)
    if args.command == "dev":
        return _cmd_dev(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "cache":
        return _cmd_cache(args)
    if args.command == "destroy":
        return _cmd_destroy(args)
    if args.command == "vars":
        if args.vars_command == "encrypt":
            return _cmd_encrypt(args)
        if args.vars_command == "decrypt":
            return _cmd_decrypt(args)
        vars_parser.print_help()
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
