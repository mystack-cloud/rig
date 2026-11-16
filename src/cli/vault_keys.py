"""
Identity + master-key wrapping for rig data keys.

Personal master key is never stored. It unwraps:
  - rig_identity_data_key → /tmp/.rig/.personal-data-key
  - rig_shared_data_key   → /tmp/.rig/.shared-data-key

Both wraps live in ``.rig/vars.{identity}.yml``.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
import re
import secrets
from typing import Callable, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Ephemeral Ansible vault passwords (tmpfs; cleared on reboot).
TMP_RIG_DIR = "/tmp/.rig"
PERSONAL_DATA_KEY_PATH = f"{TMP_RIG_DIR}/.personal-data-key"
SHARED_DATA_KEY_PATH = f"{TMP_RIG_DIR}/.shared-data-key"
# Legacy alias used by older code paths.
DEFAULT_DATA_KEY_PATH = f"{TMP_RIG_DIR}/.data-key"

IDENTITY_DATA_KEY_VAR = "rig_identity_data_key"
SHARED_DATA_KEY_VAR = "rig_shared_data_key"
WRAP_FIELD_NAMES = frozenset({IDENTITY_DATA_KEY_VAR, SHARED_DATA_KEY_VAR})

_IDENTITY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_WRAP_PREFIX = "rig1"


def sanitize_identity(raw: str) -> str:
    """Normalize an identity to a safe filename fragment."""
    value = (raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = value.strip("-_")
    if not value:
        raise ValueError("Identity is empty after sanitization")
    if value[0].isdigit():
        value = f"u-{value}"
    if not _IDENTITY_RE.match(value):
        raise ValueError(f"Invalid identity {raw!r}")
    return value


def default_os_identity() -> str:
    try:
        return sanitize_identity(getpass.getuser())
    except Exception:
        return "user"


def resolve_identity(
    explicit: Optional[str] = None,
    config=None,
) -> str:
    """Resolve identity: explicit → RIG_IDENTITY → config → OS user."""
    if explicit:
        return sanitize_identity(explicit)
    env = os.getenv("RIG_IDENTITY", "").strip()
    if env:
        return sanitize_identity(env)
    if config is None:
        try:
            from cli.config import RigConfig
            config = RigConfig()
        except Exception:
            config = None
    if config is not None:
        stored = getattr(config, "get_identity", lambda: None)()
        if stored:
            return sanitize_identity(stored)
    return default_os_identity()


def personal_vars_filename(identity: str) -> str:
    return f"vars.{sanitize_identity(identity)}.yml"


def personal_vars_path(project_dir: str, identity: Optional[str] = None) -> str:
    ident = resolve_identity(identity)
    return os.path.join(os.path.abspath(project_dir), ".rig", personal_vars_filename(ident))


def shared_vars_path(project_dir: str) -> str:
    return os.path.join(os.path.abspath(project_dir), ".rig", "vars.yml")


def generate_data_key() -> str:
    return secrets.token_urlsafe(32)


def _derive_key(master: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    return kdf.derive(master.encode("utf-8"))


def wrap_with_master_key(plaintext: str, master_key: str) -> str:
    """Return an opaque ``rig1$…`` blob decryptable only with master_key."""
    if not plaintext:
        raise ValueError("Data key plaintext is required")
    if not master_key:
        raise ValueError("Master key is required")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(master_key, salt)
    aes = AESGCM(key)
    ciphertext = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    parts = [
        _WRAP_PREFIX,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(ciphertext).decode("ascii").rstrip("="),
    ]
    return "$".join(parts)


def unwrap_with_master_key(blob: str, master_key: str) -> str:
    if not blob or not master_key:
        raise ValueError("Wrap blob and master key are required")
    parts = blob.strip().split("$")
    if len(parts) != 4 or parts[0] != _WRAP_PREFIX:
        raise ValueError("Unrecognized key wrap format")

    def _pad(s: str) -> str:
        return s + "=" * (-len(s) % 4)

    salt = base64.urlsafe_b64decode(_pad(parts[1]))
    nonce = base64.urlsafe_b64decode(_pad(parts[2]))
    ciphertext = base64.urlsafe_b64decode(_pad(parts[3]))
    key = _derive_key(master_key, salt)
    aes = AESGCM(key)
    try:
        plaintext = aes.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError("Incorrect master key or corrupt wrap") from exc
    return plaintext.decode("utf-8")


def read_data_key_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        key = handle.read()
    if key.endswith("\n"):
        key = key[:-1]
    return key or None


def write_data_key_file(data_key: str, path: str) -> None:
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


def materialize_data_keys(identity_key: str, shared_key: str) -> Tuple[str, str]:
    """Write both data keys under /tmp/.rig and return their paths."""
    write_data_key_file(identity_key, PERSONAL_DATA_KEY_PATH)
    write_data_key_file(shared_key, SHARED_DATA_KEY_PATH)
    # Keep legacy path as a copy of the identity key for older call sites.
    write_data_key_file(identity_key, DEFAULT_DATA_KEY_PATH)
    return PERSONAL_DATA_KEY_PATH, SHARED_DATA_KEY_PATH


def tmp_data_keys_present() -> bool:
    return bool(
        read_data_key_file(PERSONAL_DATA_KEY_PATH)
        and read_data_key_file(SHARED_DATA_KEY_PATH)
    )


def read_wrap_fields(identity_vars_path: str) -> dict:
    """Parse wrap fields from an identity vars file without decrypting vaults."""
    if not os.path.exists(identity_vars_path):
        return {}
    with open(identity_vars_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    found = {}
    for name in WRAP_FIELD_NAMES:
        match = re.search(
            rf"^{re.escape(name)}:\s*[\"']?({re.escape(_WRAP_PREFIX)}\$[^\s\"']+)[\"']?\s*$",
            content,
            re.MULTILINE,
        )
        if match:
            found[name] = match.group(1)
    return found


def upsert_plain_yaml_string(path: str, name: str, value: str) -> None:
    """Insert or replace a top-level scalar string in a YAML vars file."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    pattern = re.compile(
        rf"^{re.escape(name)}:.*?(?=^\S|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    cleaned = pattern.sub("", existing)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()
    line = f'{name}: "{value}"\n'
    new_content = (cleaned + "\n" + line) if cleaned else line
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(new_content if new_content.endswith("\n") else new_content + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_key_wraps(
    identity_vars_path: str,
    *,
    identity_data_key: str,
    shared_data_key: str,
    master_key: str,
) -> None:
    upsert_plain_yaml_string(
        identity_vars_path,
        IDENTITY_DATA_KEY_VAR,
        wrap_with_master_key(identity_data_key, master_key),
    )
    upsert_plain_yaml_string(
        identity_vars_path,
        SHARED_DATA_KEY_VAR,
        wrap_with_master_key(shared_data_key, master_key),
    )


def prompt_master_key(
    *,
    confirm: bool = False,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
    label: str = "Personal master key",
) -> str:
    ask_secret = secret_fn or getpass.getpass
    ask = input_fn or input
    try:
        value = ask_secret(f"{label}: ").strip()
    except Exception:
        value = ask(f"{label}: ").strip()
    if not value:
        raise ValueError("Personal master key is required")
    if confirm:
        try:
            again = ask_secret(f"Confirm {label.lower()}: ").strip()
        except Exception:
            again = ask(f"Confirm {label.lower()}: ").strip()
        if again != value:
            raise ValueError("Master keys do not match")
    return value


def unlock_data_keys(
    project_dir: str,
    *,
    identity: Optional[str] = None,
    master_key: Optional[str] = None,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
    require_wraps: bool = True,
) -> Tuple[str, str]:
    """
    Ensure personal + shared data keys exist under /tmp/.rig.

    Returns ``(personal_key_path, shared_key_path)``.
    """
    if tmp_data_keys_present():
        return PERSONAL_DATA_KEY_PATH, SHARED_DATA_KEY_PATH

    ident = resolve_identity(identity)
    id_path = personal_vars_path(project_dir, ident)
    wraps = read_wrap_fields(id_path)
    if IDENTITY_DATA_KEY_VAR not in wraps or SHARED_DATA_KEY_VAR not in wraps:
        if require_wraps:
            raise ValueError(
                f"Missing key wraps in {id_path}. Run 'rig init' to create "
                f"{IDENTITY_DATA_KEY_VAR} / {SHARED_DATA_KEY_VAR}."
            )
        # Legacy / test fallback: one ephemeral key for both vaults.
        legacy = read_data_key_file(DEFAULT_DATA_KEY_PATH) or generate_data_key()
        return materialize_data_keys(legacy, legacy)

    key = master_key or prompt_master_key(
        confirm=False,
        input_fn=input_fn,
        secret_fn=secret_fn,
        label="Personal master key (unlock)",
    )
    identity_dk = unwrap_with_master_key(wraps[IDENTITY_DATA_KEY_VAR], key)
    shared_dk = unwrap_with_master_key(wraps[SHARED_DATA_KEY_VAR], key)
    return materialize_data_keys(identity_dk, shared_dk)


def shared_vault_exists(project_dir: str) -> bool:
    path = shared_vars_path(project_dir)
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    return "!vault" in content or "$ANSIBLE_VAULT" in content


def bootstrap_identity_keys(
    project_dir: str,
    *,
    identity: Optional[str] = None,
    master_key: Optional[str] = None,
    shared_data_key: Optional[str] = None,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
    config=None,
) -> Tuple[str, str, str]:
    """
    Create wraps for a new identity and materialize keys.

    Returns ``(identity, personal_key_path, shared_key_path)``.
    """
    from cli.config import RigConfig

    cfg = config or RigConfig()
    ask = input_fn or input
    default_id = resolve_identity(identity, config=cfg)
    answer = ask(f"Identity [{default_id}]: ").strip()
    ident = sanitize_identity(answer or default_id)
    cfg.set_identity(ident)

    key = master_key or prompt_master_key(
        confirm=True,
        input_fn=input_fn,
        secret_fn=secret_fn,
    )

    identity_dk = generate_data_key()
    if shared_data_key:
        shared_dk = shared_data_key
    elif tmp_data_keys_present():
        shared_dk = read_data_key_file(SHARED_DATA_KEY_PATH) or generate_data_key()
    elif shared_vault_exists(project_dir):
        ask_secret = secret_fn or getpass.getpass
        print(
            "Shared secrets already exist. Paste the shared data key "
            "(from a teammate: rig vars export-shared-key)."
        )
        try:
            pasted = ask_secret("Shared data key: ").strip()
        except Exception:
            pasted = ask("Shared data key: ").strip()
        if not pasted:
            raise ValueError("Shared data key is required to join this workspace")
        shared_dk = pasted
    else:
        shared_dk = generate_data_key()
        print("Generated new shared data key for this workspace")

    id_path = personal_vars_path(project_dir, ident)
    write_key_wraps(
        id_path,
        identity_data_key=identity_dk,
        shared_data_key=shared_dk,
        master_key=key,
    )
    personal_path, shared_path = materialize_data_keys(identity_dk, shared_dk)
    print(f"Wrote key wraps to {id_path}")
    print(f"Materialized data keys under {TMP_RIG_DIR}")
    return ident, personal_path, shared_path


def filter_wrap_fields_from_yaml_text(content: str) -> str:
    """Remove rig_*_data_key wrap lines/blocks from vars YAML text."""
    cleaned = content
    for name in WRAP_FIELD_NAMES:
        cleaned = re.sub(
            rf"^{re.escape(name)}:.*?(?=^\S|\Z)",
            "",
            cleaned,
            flags=re.MULTILINE | re.DOTALL,
        )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.lstrip("\n")


def write_filtered_vars_copy(src_path: str, dest_path: str) -> Optional[str]:
    """
    Copy vars file without wrap fields for ansible ``-e @file``.

    Returns ``None`` when the filtered file would be empty (wraps-only identity
    file). Ansible rejects an empty ``-e @file`` with "could not be made into
    a dictionary".
    """
    if not os.path.exists(src_path):
        return None
    with open(src_path, "r", encoding="utf-8") as handle:
        content = handle.read()
    filtered = filter_wrap_fields_from_yaml_text(content)
    # Host connection vars belong in the inventory. As extra vars (highest
    # precedence) they also hit ``delegate_to: localhost`` and force SSH there.
    filtered = re.sub(
        r"^ansible_(?:connection|user|python_interpreter):[^\n]*\n?",
        "",
        filtered,
        flags=re.MULTILINE,
    ).strip()
    if not filtered:
        return None
    if not filtered.endswith("\n"):
        filtered = filtered + "\n"
    directory = os.path.dirname(dest_path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as handle:
        handle.write(filtered)
    try:
        os.chmod(dest_path, 0o600)
    except OSError:
        pass
    return dest_path


def is_personal_secret_name(name: str) -> bool:
    """Heuristic: PATs and coding agent key are personal."""
    n = (name or "").strip()
    if n in {
        "coding_agent_api_key",
        "opencode_api_key",
        "claude_api_key",
        "cursor_api_key",
        "github_pat",
        "gitlab_pat",
    }:
        return True
    if n.endswith("_git_pat"):
        return True
    return False


def fingerprint_key(data_key: str) -> str:
    return hashlib.sha256(data_key.encode("utf-8")).hexdigest()[:12]
