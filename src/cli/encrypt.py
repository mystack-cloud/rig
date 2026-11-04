"""
Ansible-compatible encryption for `rig vars encrypt`.

The data key is a temporary Ansible vault password at /tmp/.rig/.data-key
(cleared on reboot). Encryption uses `ansible-vault encrypt_string` and
writes the result into the project's `.rig/vars.yml`.
"""

from __future__ import annotations

import getpass
import os
import re
import secrets
import subprocess
import sys
from typing import Callable, Optional, TextIO

# Intentionally under /tmp so the key does not persist across reboots.
DEFAULT_DATA_KEY_PATH = "/tmp/.rig/.data-key"
_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Vaulted password for code-server browser login (``rig dev --browser``).
VSCODE_BROWSER_PASSWORD_VAR = "vscode_browser_password"


def read_data_key(path: str = DEFAULT_DATA_KEY_PATH) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        key = handle.read()
    if key.endswith("\n"):
        key = key[:-1]
    return key if key else None


def write_data_key(data_key: str, path: str = DEFAULT_DATA_KEY_PATH) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(data_key)
        handle.write("\n")
    try:
        os.chmod(path, 0o600)
        if directory:
            os.chmod(directory, 0o700)
    except OSError:
        pass


def ensure_data_key(
    path: str = DEFAULT_DATA_KEY_PATH,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
) -> str:
    """Return the data key, creating the temporary vault password file when missing."""
    existing = read_data_key(path)
    if existing is not None:
        return existing

    ask_secret = secret_fn or getpass.getpass
    ask = input_fn or input
    print(f"Temporary data key not found at {path}")
    print("This path is under /tmp and will not survive a reboot.")
    try:
        data_key = ask_secret("Enter data key to create: ").strip()
    except Exception:
        data_key = ask("Enter data key to create: ").strip()
    if not data_key:
        raise ValueError("Data key is required")

    try:
        confirm = ask_secret("Confirm data key: ").strip()
    except Exception:
        confirm = ask("Confirm data key: ").strip()
    if confirm != data_key:
        raise ValueError("Data keys do not match")

    write_data_key(data_key, path)
    print(f"Saved temporary data key to {path}")
    return data_key


def validate_var_name(name: str) -> str:
    name = name.strip()
    if not name or not _VAR_NAME_RE.match(name):
        raise ValueError(
            "Variable name must match [A-Za-z_][A-Za-z0-9_]* "
            f"(got {name!r})"
        )
    return name


def resolve_project_dir(start: Optional[str] = None) -> str:
    """Prefer the git root (where rig init writes .rig/), else cwd."""
    start = os.path.abspath(start or os.getcwd())
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return start
    if result.returncode == 0 and result.stdout.strip():
        return os.path.abspath(result.stdout.strip())
    return start


def vars_path_for_project(project_dir: Optional[str] = None) -> str:
    root = resolve_project_dir(project_dir)
    return os.path.join(root, ".rig", "vars.yml")


def _remove_top_level_key(content: str, name: str) -> str:
    """Remove a top-level YAML key and its indented block from vars.yml text."""
    if not content:
        return ""
    pattern = re.compile(
        rf"^{re.escape(name)}:.*?(?=^\S|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    cleaned = pattern.sub("", content)
    # Collapse excessive blank lines left behind.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.lstrip("\n")


def upsert_vault_var(vars_path: str, name: str, vault_block: str) -> None:
    """Insert or replace an ansible-vault variable block in vars.yml."""
    name = validate_var_name(name)
    directory = os.path.dirname(vars_path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)

    existing = ""
    if os.path.exists(vars_path):
        with open(vars_path, "r", encoding="utf-8") as handle:
            existing = handle.read()

    cleaned = _remove_top_level_key(existing, name).rstrip()
    block = vault_block.strip() + "\n"
    if cleaned:
        new_content = cleaned + "\n" + block
    else:
        new_content = block

    with open(vars_path, "w", encoding="utf-8") as handle:
        handle.write(new_content)
    try:
        os.chmod(vars_path, 0o600)
    except OSError:
        pass


def encrypt_named_value(
    plaintext: str,
    name: str,
    vault_password_file: str = DEFAULT_DATA_KEY_PATH,
) -> str:
    """Encrypt plaintext as an Ansible inventory/vars entry named `name`."""
    name = validate_var_name(name)
    if plaintext == "":
        raise ValueError("Value to encrypt is required")

    cmd = [
        "ansible-vault",
        "encrypt_string",
        "--vault-password-file",
        vault_password_file,
        "--stdin-name",
        name,
    ]
    try:
        result = subprocess.run(
            cmd,
            input=plaintext,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ansible-vault not found. Install Ansible to use rig vars encrypt."
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"ansible-vault encrypt_string failed: {detail}")
    return result.stdout.rstrip("\n")


def encrypt_stdin(
    vault_password_file: str = DEFAULT_DATA_KEY_PATH,
    stdin_name: str = "",
    stdin: Optional[TextIO] = None,
) -> str:
    """Encrypt stdin until EOF (Ctrl+D) using ansible-vault --stdin-name."""
    name = validate_var_name(stdin_name)
    source = stdin if stdin is not None else sys.stdin
    if source.isatty():
        print(
            f"Reading multiline value for '{name}'. Finish with Ctrl+D (EOF).",
            file=sys.stderr,
        )
    plaintext = source.read()
    if plaintext == "":
        raise ValueError("Value to encrypt is required")
    return encrypt_named_value(plaintext, name, vault_password_file)


def read_variable_name(
    default: Optional[str] = None,
    input_fn: Optional[Callable[..., str]] = None,
) -> str:
    ask = input_fn or input
    suffix = f" [{default}]" if default else ""
    answer = ask(f"Variable name{suffix}: ").strip()
    if not answer:
        if default:
            return validate_var_name(default)
        raise ValueError("Variable name is required")
    return validate_var_name(answer)


def read_single_line_value(input_fn: Optional[Callable[..., str]] = None) -> str:
    """Read one line to encrypt (default encrypt mode)."""
    ask = input_fn or input
    value = ask("Enter value to encrypt: ")
    if value is None or value == "":
        raise ValueError("Value to encrypt is required")
    return value


def store_encrypted_var(
    name: str,
    plaintext: str,
    project_dir: Optional[str] = None,
    data_key_path: str = DEFAULT_DATA_KEY_PATH,
) -> str:
    """Encrypt plaintext as `name` and upsert it into the project's vars.yml."""
    vault_block = encrypt_named_value(
        plaintext,
        name=name,
        vault_password_file=data_key_path,
    )
    vars_path = vars_path_for_project(project_dir)
    upsert_vault_var(vars_path, name, vault_block)
    print(f"Added '{name}' to {vars_path}")
    return vault_block


def _prompt_secret_with_env_fallback(
    label: str,
    env_name: str,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
) -> str:
    ask_secret = secret_fn or getpass.getpass
    ask = input_fn or input
    env_value = os.getenv(env_name, "").strip()
    hint = f" [Enter keeps {env_name} from env]" if env_value else " [Enter skips]"
    try:
        value = ask_secret(f"{label}{hint}: ").strip()
    except Exception:
        value = ask(f"{label}{hint}: ").strip()
    if value:
        return value
    if env_value:
        print(f"Using {env_name} from the environment")
        return env_value
    return ""


PROVIDER_ENV_VARS = {
    "github": "RIG_GITHUB_PAT",
    "gitlab": "RIG_GITLAB_PAT",
}


def detect_git_provider(url: str) -> Optional[str]:
    """Return 'github' or 'gitlab' when the remote URL is a known host."""
    if not url:
        return None
    lower = url.strip().lower()
    if "github.com" in lower or lower.startswith("git@github.com:"):
        return "github"
    if "gitlab.com" in lower or lower.startswith("git@gitlab.com:"):
        return "gitlab"
    return None


def git_providers_from_repos(repos: Optional[list]) -> list:
    """Unique git providers (github/gitlab) referenced by workspace repos."""
    found: list = []
    for repo in repos or []:
        if not isinstance(repo, dict):
            continue
        url = (repo.get("url") or repo.get("repo") or "").strip()
        provider = detect_git_provider(url)
        if provider and provider not in found:
            found.append(provider)
    return found


def project_pat_var_name(project_name: str) -> str:
    """Repo-specific PAT variable: ``{project_name}_git_pat`` (YAML-safe)."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", (project_name or "").strip())
    if not safe:
        safe = "project"
    if safe[0].isdigit():
        safe = f"p_{safe}"
    return validate_var_name(f"{safe}_git_pat")


def provider_pat_var_name(provider: str) -> str:
    return validate_var_name(f"{provider}_pat")


def _is_ssh_git_url(url: str) -> bool:
    value = (url or "").strip()
    return value.startswith("git@") or value.startswith("ssh://")


def to_https_git_url(url: str) -> str:
    """
    Convert SSH git remotes to HTTPS.

    Examples:
      git@github.com:org/repo.git → https://github.com/org/repo.git
      ssh://git@gitlab.com/org/repo.git → https://gitlab.com/org/repo.git
    """
    value = (url or "").strip()
    if not value:
        return value
    if value.startswith("https://") or value.startswith("http://"):
        return value

    scp = re.match(r"^git@([^:]+):(.+)$", value)
    if scp:
        host, path = scp.group(1), scp.group(2).lstrip("/")
        if not path.endswith(".git"):
            path = f"{path}.git"
        return f"https://{host}/{path}"

    ssh = re.match(r"^ssh://(?:git@)?([^/]+)/(.+)$", value)
    if ssh:
        host, path = ssh.group(1), ssh.group(2).lstrip("/")
        if not path.endswith(".git"):
            path = f"{path}.git"
        return f"https://{host}/{path}"

    return value


def _prompt_yes_no(
    message: str,
    default: bool = True,
    input_fn: Optional[Callable[..., str]] = None,
) -> bool:
    ask = input_fn or input
    suffix = " [Y/n]" if default else " [y/N]"
    answer = ask(f"{message}{suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def collect_repo_pats(
    repos: list,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
) -> tuple:
    """
    Ask for a PAT for each GitHub/GitLab repo (HTTPS or SSH).

    SSH remotes still need a PAT for CLI auth (MR review, etc.); clone URLs
    are converted to HTTPS separately. After the first PAT for a provider,
    asks whether to store it as the default ``github_pat`` / ``gitlab_pat``.
    Later repos on that provider can Enter to reuse the shared value.

    Returns ``(assignments, default_pats)`` where assignments is
    ``{repo_name: plaintext}`` and default_pats is
    ``{provider: plaintext}`` for PATs the user marked as default.
    """
    ask_secret = secret_fn or getpass.getpass
    ask = input_fn or input
    provider_shared: dict = {}
    default_pats: dict = {}
    assignments: dict = {}

    for repo in repos:
        if not isinstance(repo, dict):
            continue
        name = (repo.get("name") or "").strip()
        url = (repo.get("url") or "").strip()
        if not name or not url:
            continue
        provider = detect_git_provider(url)
        if not provider:
            print(f"Skipping PAT for '{name}' (unsupported git host; GitHub/GitLab only)")
            continue
        if _is_ssh_git_url(url):
            print(
                f"Note: '{name}' uses SSH remote; "
                f"will clone via {to_https_git_url(url)} and use PAT for {provider} CLI"
            )

        if provider in provider_shared:
            shared_label = (
                f"default {provider}_pat"
                if provider in default_pats
                else f"shared {provider} PAT"
            )
            label = f"PAT for '{name}' ({provider}) [Enter reuses {shared_label}]"
            try:
                value = ask_secret(f"{label}: ").strip()
            except Exception:
                value = ask(f"{label}: ").strip()
            if not value:
                assignments[name] = provider_shared[provider]
                print(f"Reusing {shared_label} for '{name}'")
            else:
                assignments[name] = value
            continue

        env_name = PROVIDER_ENV_VARS[provider]
        env_value = os.getenv(env_name, "").strip()
        hint = f" [Enter keeps {env_name} from env]" if env_value else " [Enter skips]"
        label = f"PAT for '{name}' ({provider}){hint}"
        try:
            value = ask_secret(f"{label}: ").strip()
        except Exception:
            value = ask(f"{label}: ").strip()
        if not value and env_value:
            print(f"Using {env_name} from the environment")
            value = env_value
        if not value:
            print(f"Skipping PAT for '{name}'")
            continue
        assignments[name] = value
        provider_shared[provider] = value

        var_default = provider_pat_var_name(provider)
        if _prompt_yes_no(
            f"Use this as the default {provider} PAT ({var_default})?",
            default=True,
            input_fn=ask,
        ):
            default_pats[provider] = value
            print(f"Will store as {var_default}")
        else:
            print(f"Will store as {project_pat_var_name(name)} (repo-specific)")

    return assignments, default_pats


def resolve_repo_pat_var_names(
    assignments: dict,
    repos: list,
    default_pats: Optional[dict] = None,
) -> tuple:
    """
    Map repo PATs to variable names.

    - Provider default (user opted in) → ``github_pat`` / ``gitlab_pat``
    - Same PAT shared by multiple repos → provider var
    - Otherwise → ``{project_name}_git_pat``

    Returns ``(password_vars, to_store)`` where password_vars is
    ``{repo_name: var_name}`` and to_store is ``{var_name: plaintext}``.
    """
    default_pats = dict(default_pats or {})
    providers = {}
    for repo in repos:
        if not isinstance(repo, dict):
            continue
        name = (repo.get("name") or "").strip()
        url = (repo.get("url") or "").strip()
        if name and url:
            providers[name] = detect_git_provider(url)

    groups: dict = {}
    for name, plaintext in assignments.items():
        provider = providers.get(name)
        if not provider or not plaintext:
            continue
        groups.setdefault((provider, plaintext), []).append(name)

    password_vars: dict = {}
    to_store: dict = {}
    for (provider, plaintext), names in groups.items():
        is_default = default_pats.get(provider) == plaintext
        if is_default or len(names) > 1:
            var_name = provider_pat_var_name(provider)
        else:
            var_name = project_pat_var_name(names[0])
        to_store[var_name] = plaintext
        for name in names:
            password_vars[name] = var_name
    return password_vars, to_store


def store_repo_pats(
    to_store: dict,
    project_dir: Optional[str] = None,
    data_key_path: str = DEFAULT_DATA_KEY_PATH,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
) -> bool:
    """Encrypt and upsert each PAT variable into ``.rig/vars.yml``."""
    if not to_store:
        return False
    ensure_data_key(path=data_key_path, input_fn=input_fn, secret_fn=secret_fn)
    for var_name, plaintext in to_store.items():
        store_encrypted_var(
            var_name,
            plaintext,
            project_dir=project_dir,
            data_key_path=data_key_path,
        )
    return True


def prompt_and_store_git_pat(
    project_dir: Optional[str] = None,
    data_key_path: str = DEFAULT_DATA_KEY_PATH,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
    repos: Optional[list] = None,
) -> dict:
    """
    Prompt for GitHub/GitLab PATs for the given repos and store them encrypted.

    When ``repos`` is omitted, falls back to a single shared ``github_pat``
    prompt (legacy). Returns ``{repo_name: var_name}`` for assigned PATs.
    """
    if not repos:
        ensure_data_key(path=data_key_path, input_fn=input_fn, secret_fn=secret_fn)
        pat = _prompt_secret_with_env_fallback(
            "GitHub PAT for private repo clones",
            "RIG_GITHUB_PAT",
            input_fn=input_fn,
            secret_fn=secret_fn,
        )
        if not pat:
            print("Skipping github_pat (no PAT provided)")
            return {}
        store_encrypted_var(
            "github_pat",
            pat,
            project_dir=project_dir,
            data_key_path=data_key_path,
        )
        return {}

    assignments, default_pats = collect_repo_pats(
        repos, input_fn=input_fn, secret_fn=secret_fn
    )
    password_vars, to_store = resolve_repo_pat_var_names(
        assignments, repos, default_pats=default_pats
    )
    store_repo_pats(
        to_store,
        project_dir=project_dir,
        data_key_path=data_key_path,
        input_fn=input_fn,
        secret_fn=secret_fn,
    )
    return password_vars


def prompt_and_store_coding_agent_credentials(
    project_dir: Optional[str] = None,
    data_key_path: str = DEFAULT_DATA_KEY_PATH,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
    agent_type: Optional[str] = None,
) -> bool:
    """
    Ask for the remote coding agent API key, encrypt it, and store it as
    coding_agent_api_key in .rig/vars.yml for system.yml to consume.

    Config files under ``.rig/.opencode`` / ``.rig/.claude`` reference the key
    via the ``CODING_AGENT_API_KEY`` environment variable set on the VM.
    """
    label = "Coding agent API key"
    if agent_type:
        try:
            from cli.agents import AGENT_LABELS, normalize_agent_type
            agent = normalize_agent_type(agent_type)
            label = f"{AGENT_LABELS.get(agent, agent)} API key"
        except ValueError:
            pass
    ensure_data_key(path=data_key_path, input_fn=input_fn, secret_fn=secret_fn)
    agent_normalized = None
    if agent_type:
        try:
            from cli.agents import normalize_agent_type
            agent_normalized = normalize_agent_type(agent_type)
        except ValueError:
            agent_normalized = None
    skip_hint = " [Enter = OpenCode free models]" if agent_normalized == "opencode" else ""
    api_key = _prompt_secret_with_env_fallback(
        f"{label}{skip_hint}",
        "RIG_CODING_AGENT_API_KEY",
        input_fn=input_fn,
        secret_fn=secret_fn,
    )
    if not api_key:
        if agent_normalized == "opencode":
            print(
                "Skipping coding_agent_api_key (no key provided); "
                "OpenCode will use free models"
            )
        else:
            print("Skipping coding_agent_api_key (no key provided)")
        return False

    store_encrypted_var(
        "coding_agent_api_key",
        api_key,
        project_dir=project_dir,
        data_key_path=data_key_path,
    )
    return True


def read_vscode_browser_password(
    project_dir: Optional[str] = None,
    data_key_path: str = DEFAULT_DATA_KEY_PATH,
) -> Optional[str]:
    """Decrypt ``vscode_browser_password`` when present and decryptable."""
    vars_path = vars_path_for_project(project_dir)
    if VSCODE_BROWSER_PASSWORD_VAR not in list_vault_var_names(vars_path=vars_path):
        return None
    try:
        return decrypt_var(
            VSCODE_BROWSER_PASSWORD_VAR,
            vars_path=vars_path,
            vault_password_file=data_key_path,
        )
    except (ValueError, RuntimeError):
        return None


def ensure_vscode_browser_password(
    project_dir: Optional[str] = None,
    data_key_path: str = DEFAULT_DATA_KEY_PATH,
    *,
    force: bool = False,
) -> bool:
    """
    Ensure ``vscode_browser_password`` exists in ``.rig/vars.yml`` (vaulted).

    Autogenerates a high-entropy password on first use. Returns ``True`` when a
    new value was written.
    """
    ensure_data_key(path=data_key_path)
    vars_path = vars_path_for_project(project_dir)
    existing = list_vault_var_names(vars_path=vars_path)
    if VSCODE_BROWSER_PASSWORD_VAR in existing and not force:
        return False

    plaintext = secrets.token_urlsafe(32)
    store_encrypted_var(
        VSCODE_BROWSER_PASSWORD_VAR,
        plaintext,
        project_dir=project_dir,
        data_key_path=data_key_path,
    )
    return True


def interactive_encrypt(
    path: str = DEFAULT_DATA_KEY_PATH,
    stdin_name: Optional[str] = None,
    project_dir: Optional[str] = None,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
    stdin: Optional[TextIO] = None,
) -> str:
    """
    Ensure a temporary data key exists, encrypt a value, and store it in vars.yml.

    Default: ask for variable name, then a single-line value.
    With stdin_name: use that variable name and read stdin until EOF.
    """
    ensure_data_key(path=path, input_fn=input_fn, secret_fn=secret_fn)

    if stdin_name:
        name = validate_var_name(stdin_name)
        vault_block = encrypt_stdin(
            vault_password_file=path,
            stdin_name=name,
            stdin=stdin,
        )
    else:
        name = read_variable_name(input_fn=input_fn)
        plaintext = read_single_line_value(input_fn=input_fn)
        vault_block = encrypt_named_value(
            plaintext,
            name=name,
            vault_password_file=path,
        )

    vars_path = vars_path_for_project(project_dir)
    upsert_vault_var(vars_path, name, vault_block)
    print(f"Added '{name}' to {vars_path}")
    return vault_block


def require_data_key_file(path: str = DEFAULT_DATA_KEY_PATH) -> str:
    """Return the data key path, or raise if the temporary key is missing."""
    if not os.path.exists(path):
        raise ValueError(
            f"Temporary data key not found at {path}. "
            "Run 'rig vars encrypt' first to create it."
        )
    return path


def extract_vault_ciphertext(block: str) -> str:
    """Normalize an ansible-vault blob from a !vault YAML block."""
    match = re.search(r"(\$ANSIBLE_VAULT;.*)", block, re.DOTALL)
    if not match:
        raise ValueError("No ansible-vault ciphertext found")
    lines = [line.strip() for line in match.group(1).splitlines() if line.strip()]
    return "\n".join(lines) + "\n"


def extract_var_block(vars_content: str, name: str) -> str:
    """Return the top-level YAML block for a variable name."""
    name = validate_var_name(name)
    pattern = re.compile(
        rf"^{re.escape(name)}:.*?(?=^\S|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(vars_content)
    if not match:
        raise ValueError(f"Variable '{name}' not found in vars.yml")
    return match.group(0).rstrip()


def decrypt_vault_blob(
    ciphertext: str,
    vault_password_file: str = DEFAULT_DATA_KEY_PATH,
) -> str:
    """Decrypt ansible-vault ciphertext using the temporary data key by default."""
    password_file = require_data_key_file(vault_password_file)
    body = (
        extract_vault_ciphertext(ciphertext)
        if "$ANSIBLE_VAULT" in ciphertext
        else ciphertext
    )

    import tempfile

    fd, temp_path = tempfile.mkstemp(prefix="rig-vault-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        try:
            result = subprocess.run(
                [
                    "ansible-vault",
                    "decrypt",
                    temp_path,
                    "--vault-password-file",
                    password_file,
                    "--output",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ansible-vault not found. Install Ansible to use rig vars decrypt."
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise RuntimeError(f"ansible-vault decrypt failed: {detail}")
        return result.stdout
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def decrypt_var(
    name: str,
    vars_path: Optional[str] = None,
    vault_password_file: str = DEFAULT_DATA_KEY_PATH,
    project_dir: Optional[str] = None,
) -> str:
    """Decrypt a named variable from .rig/vars.yml using the temporary data key."""
    name = validate_var_name(name)
    path = vars_path or vars_path_for_project(project_dir)
    if not os.path.exists(path):
        raise ValueError(f"vars.yml not found at {path}")
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    block = extract_var_block(content, name)
    return decrypt_vault_blob(block, vault_password_file=vault_password_file)


def list_vault_var_names(vars_path: Optional[str] = None, project_dir: Optional[str] = None) -> list[str]:
    path = vars_path or vars_path_for_project(project_dir)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    names = []
    for match in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*):\s*!vault\b", content, re.MULTILINE):
        names.append(match.group(1))
    return names


def interactive_decrypt(
    path: str = DEFAULT_DATA_KEY_PATH,
    name: Optional[str] = None,
    project_dir: Optional[str] = None,
    input_fn: Optional[Callable[..., str]] = None,
) -> str:
    """Decrypt a vars.yml variable using the temporary data key (no file flag needed)."""
    require_data_key_file(path)
    vars_path = vars_path_for_project(project_dir)
    if name:
        var_name = validate_var_name(name)
    else:
        available = list_vault_var_names(vars_path=vars_path)
        default = available[0] if len(available) == 1 else None
        if available:
            print("Encrypted variables: " + ", ".join(available))
        var_name = read_variable_name(default=default, input_fn=input_fn)
    return decrypt_var(
        var_name,
        vars_path=vars_path,
        vault_password_file=path,
    )
