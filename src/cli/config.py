"""
Configuration management for the CLI.

Credentials live in ~/.rig/config using AWS-style profiles:

    [default]
    hetzner_cloud_api_token = ...
    hetzner_s3_access_key = ...
    hetzner_s3_secret_key = ...
    s3_endpoint_url = https://fsn1.your-objectstorage.com

    [profile staging]
    hetzner_cloud_api_token = ...
"""

from __future__ import annotations

import configparser
import getpass
import os
from collections.abc import Callable
from typing import Optional

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.rig/config")
DEFAULT_PROFILE = "default"
DEFAULT_S3_ENDPOINT = "https://fsn1.your-objectstorage.com"

PROFILE_KEYS = (
    "hetzner_cloud_api_token",
    "hetzner_s3_access_key",
    "hetzner_s3_secret_key",
    "s3_endpoint_url",
    "storage_provider",
    "r2_account_id",
    "r2_access_key",
    "r2_secret_key",
)

STORAGE_PROVIDERS = ("hetzner", "r2")
DEFAULT_STORAGE_PROVIDER = "hetzner"

# Developer identity for ``.rig/vars.{identity}.yml`` (not a cloud profile).
IDENTITY_KEY = "identity"


def _section_name(profile: str) -> str:
    if profile == DEFAULT_PROFILE:
        return DEFAULT_PROFILE
    if profile.startswith("profile "):
        return profile
    return f"profile {profile}"


def _profile_from_section(section: str) -> str:
    if section == DEFAULT_PROFILE:
        return DEFAULT_PROFILE
    if section.startswith("profile "):
        return section[len("profile ") :]
    return section


class RigConfig:
    """AWS-style profile config stored at ~/.rig/config."""

    def __init__(self, path: str | None = None, profile: str | None = None):
        self.path = os.path.expanduser(path or os.getenv("RIG_CONFIG") or DEFAULT_CONFIG_PATH)
        self.profile = profile or os.getenv("RIG_PROFILE") or DEFAULT_PROFILE
        self._parser = configparser.ConfigParser()
        self.load()

    def load(self) -> None:
        self._parser = configparser.ConfigParser()
        if os.path.exists(self.path):
            self._parser.read(self.path)

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        with open(self.path, "w") as handle:
            self._parser.write(handle)
        try:
            os.chmod(self.path, 0o600)
            if directory:
                os.chmod(directory, 0o700)
        except OSError:
            pass

    def list_profiles(self) -> list[str]:
        profiles = [_profile_from_section(section) for section in self._parser.sections()]
        if DEFAULT_PROFILE not in profiles and self._parser.has_section(DEFAULT_PROFILE):
            profiles.insert(0, DEFAULT_PROFILE)
        return profiles

    def get_profile(self, profile: str | None = None) -> dict[str, str]:
        name = profile or self.profile or DEFAULT_PROFILE
        section = _section_name(name)
        if not self._parser.has_section(section):
            # Accept bare section names too, matching ~/.aws/credentials style.
            if name != DEFAULT_PROFILE and self._parser.has_section(name):
                section = name
            else:
                return {}
        return {key: value for key, value in self._parser.items(section) if key in PROFILE_KEYS and value}

    def set_profile(self, values: dict[str, str], profile: str | None = None) -> None:
        name = profile or self.profile or DEFAULT_PROFILE
        section = _section_name(name)
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        for key, value in values.items():
            if key not in PROFILE_KEYS:
                continue
            if value:
                self._parser.set(section, key, value)
            elif self._parser.has_option(section, key):
                self._parser.remove_option(section, key)
        self.save()

    def resolve(self, profile: str | None = None) -> dict[str, str]:
        """Merge environment variables over the selected profile."""
        stored = self.get_profile(profile)
        return {
            "hetzner_cloud_api_token": (
                os.getenv("HETZNER_CLOUD_API_TOKEN")
                or os.getenv("HZ_TOKEN")
                or stored.get("hetzner_cloud_api_token", "")
            ),
            "hetzner_s3_access_key": (
                os.getenv("HETZNER_S3_ACCESS_KEY")
                or os.getenv("HETZNER_CLOUD_OBJECT_STORAGE_ACCESS_KEY")
                or stored.get("hetzner_s3_access_key", "")
            ),
            "hetzner_s3_secret_key": (
                os.getenv("HETZNER_S3_SECRET_KEY")
                or os.getenv("HETZNER_CLOUD_OBJECT_STORAGE_SECRET_KEY")
                or stored.get("hetzner_s3_secret_key", "")
            ),
            "s3_endpoint_url": (os.getenv("S3_ENDPOINT_URL") or stored.get("s3_endpoint_url") or DEFAULT_S3_ENDPOINT),
            "storage_provider": (
                (os.getenv("RIG_STORAGE_PROVIDER") or stored.get("storage_provider") or DEFAULT_STORAGE_PROVIDER)
                .strip()
                .lower()
            ),
            "r2_account_id": os.getenv("CF_R2_ACCOUNT_ID") or stored.get("r2_account_id", ""),
            "r2_access_key": os.getenv("CF_R2_ACCESS_KEY") or stored.get("r2_access_key", ""),
            "r2_secret_key": os.getenv("CF_R2_SECRET_KEY") or stored.get("r2_secret_key", ""),
        }

    def apply_to_environ(self, profile: str | None = None) -> dict[str, str]:
        """Export resolved credentials into the process environment."""
        resolved = self.resolve(profile)
        mapping = {
            "hetzner_cloud_api_token": "HETZNER_CLOUD_API_TOKEN",
            "hetzner_s3_access_key": "HETZNER_S3_ACCESS_KEY",
            "hetzner_s3_secret_key": "HETZNER_S3_SECRET_KEY",
            "s3_endpoint_url": "S3_ENDPOINT_URL",
            "storage_provider": "RIG_STORAGE_PROVIDER",
            "r2_account_id": "CF_R2_ACCOUNT_ID",
            "r2_access_key": "CF_R2_ACCESS_KEY",
            "r2_secret_key": "CF_R2_SECRET_KEY",
        }
        for key, env_name in mapping.items():
            value = resolved.get(key) or ""
            if value and not os.getenv(env_name):
                os.environ[env_name] = value
        return resolved

    def get_identity(self) -> str | None:
        """Return persisted developer identity from the active profile section."""
        section = _section_name(self.profile or DEFAULT_PROFILE)
        if not self._parser.has_section(section):
            if self.profile != DEFAULT_PROFILE and self._parser.has_section(self.profile):
                section = self.profile
            elif self._parser.has_section(DEFAULT_PROFILE):
                section = DEFAULT_PROFILE
            else:
                return None
        value = self._parser.get(section, IDENTITY_KEY, fallback="").strip()
        return value or None

    def set_identity(self, identity: str) -> None:
        """Persist developer identity on the active profile section."""
        from cli.vault_keys import sanitize_identity

        value = sanitize_identity(identity)
        section = _section_name(self.profile or DEFAULT_PROFILE)
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        self._parser.set(section, IDENTITY_KEY, value)
        self.save()


def _prompt_secret(message: str, default: str = "", input_fn=None, secret_fn=None) -> str:
    ask = secret_fn or getpass.getpass
    plain = input_fn or input
    if default:
        suffix = f" [{default[:4]}…]" if len(default) > 4 else f" [{default}]"
        # Keep existing values visible only as a short prefix; Enter keeps them.
        try:
            answer = ask(f"{message}{suffix}: ")
        except Exception:
            answer = plain(f"{message}{suffix}: ")
        return answer.strip() or default
    try:
        answer = ask(f"{message}: ")
    except Exception:
        answer = plain(f"{message}: ")
    return answer.strip()


def ensure_credentials(
    config: RigConfig | None = None,
    input_fn: Callable[..., str] | None = None,
    secret_fn: Callable[..., str] | None = None,
    force_prompt: bool = False,
) -> dict[str, str]:
    """Return credentials, prompting and saving to ~/.rig/config when needed."""
    config = config or RigConfig()
    resolved = config.resolve()
    missing_token = not resolved.get("hetzner_cloud_api_token")
    provider = resolved.get("storage_provider") or DEFAULT_STORAGE_PROVIDER
    if provider not in STORAGE_PROVIDERS:
        raise ValueError(f"Unknown storage provider {provider!r} (expected one of: {', '.join(STORAGE_PROVIDERS)})")
    use_r2 = provider == "r2"
    if use_r2:
        # Hetzner S3 keys are not used; state lives in Cloudflare R2.
        missing_s3 = not (
            resolved.get("r2_account_id") and resolved.get("r2_access_key") and resolved.get("r2_secret_key")
        )
    else:
        missing_s3 = not (resolved.get("hetzner_s3_access_key") and resolved.get("hetzner_s3_secret_key"))

    if not force_prompt and not missing_token and not missing_s3:
        config.apply_to_environ()
        return resolved

    if use_r2:
        return _ensure_r2_credentials(
            config, resolved, missing_token, force_prompt, input_fn=input_fn, secret_fn=secret_fn
        )

    print(f"Hetzner credentials ({config.profile} profile in {config.path})")
    token = resolved.get("hetzner_cloud_api_token", "")
    access_key = resolved.get("hetzner_s3_access_key", "")
    secret_key = resolved.get("hetzner_s3_secret_key", "")
    endpoint = resolved.get("s3_endpoint_url", DEFAULT_S3_ENDPOINT)

    if force_prompt or missing_token:
        token = _prompt_secret(
            "Hetzner Cloud API token",
            default=token,
            input_fn=input_fn,
            secret_fn=secret_fn,
        )
    if force_prompt or missing_s3:
        access_key = _prompt_secret(
            "Hetzner S3 access key",
            default=access_key,
            input_fn=input_fn,
            secret_fn=secret_fn,
        )
        secret_key = _prompt_secret(
            "Hetzner S3 secret key",
            default=secret_key,
            input_fn=input_fn,
            secret_fn=secret_fn,
        )
        ask = input_fn or input
        endpoint_answer = ask(f"S3 endpoint URL [{endpoint}]: ").strip()
        if endpoint_answer:
            endpoint = endpoint_answer

    if not token:
        raise ValueError("Hetzner Cloud API token is required")
    if not access_key or not secret_key:
        raise ValueError("Hetzner S3 access and secret keys are required")

    values = {
        "hetzner_cloud_api_token": token,
        "hetzner_s3_access_key": access_key,
        "hetzner_s3_secret_key": secret_key,
        "s3_endpoint_url": endpoint or DEFAULT_S3_ENDPOINT,
    }
    config.set_profile(values)
    config.apply_to_environ()
    print(f"Saved credentials to {config.path} [{config.profile}]")
    return values


def _ensure_r2_credentials(
    config: RigConfig,
    resolved: dict[str, str],
    missing_token: bool,
    force_prompt: bool,
    input_fn: Callable[..., str] | None = None,
    secret_fn: Callable[..., str] | None = None,
) -> dict[str, str]:
    """Prompt for the Hetzner token plus Cloudflare R2 credentials and save them."""
    print(f"Hetzner token and Cloudflare R2 credentials ({config.profile} profile in {config.path})")
    token = resolved.get("hetzner_cloud_api_token", "")
    account_id = resolved.get("r2_account_id", "")
    access_key = resolved.get("r2_access_key", "")
    secret_key = resolved.get("r2_secret_key", "")

    if force_prompt or missing_token:
        token = _prompt_secret("Hetzner Cloud API token", default=token, input_fn=input_fn, secret_fn=secret_fn)
    if force_prompt or not (account_id and access_key and secret_key):
        ask = input_fn or input
        suffix = f" [{account_id}]" if account_id else ""
        account_id = ask(f"Cloudflare account ID{suffix}: ").strip() or account_id
        access_key = _prompt_secret("R2 access key ID", default=access_key, input_fn=input_fn, secret_fn=secret_fn)
        secret_key = _prompt_secret("R2 secret access key", default=secret_key, input_fn=input_fn, secret_fn=secret_fn)

    if not token:
        raise ValueError("Hetzner Cloud API token is required")
    if not (account_id and access_key and secret_key):
        raise ValueError("Cloudflare account ID and R2 access and secret keys are required")

    values = {
        "hetzner_cloud_api_token": token,
        "storage_provider": "r2",
        "r2_account_id": account_id,
        "r2_access_key": access_key,
        "r2_secret_key": secret_key,
    }
    config.set_profile(values)
    config.apply_to_environ()
    print(f"Saved credentials to {config.path} [{config.profile}]")
    return {**resolved, **values}
