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
from typing import Any, Callable, Dict, Optional


DEFAULT_CONFIG_PATH = os.path.expanduser("~/.rig/config")
DEFAULT_PROFILE = "default"
DEFAULT_S3_ENDPOINT = "https://fsn1.your-objectstorage.com"

PROFILE_KEYS = (
    "hetzner_cloud_api_token",
    "hetzner_s3_access_key",
    "hetzner_s3_secret_key",
    "s3_endpoint_url",
)


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
        return section[len("profile "):]
    return section


class RigConfig:
    """AWS-style profile config stored at ~/.rig/config."""

    def __init__(self, path: Optional[str] = None, profile: Optional[str] = None):
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

    def get_profile(self, profile: Optional[str] = None) -> Dict[str, str]:
        name = profile or self.profile or DEFAULT_PROFILE
        section = _section_name(name)
        if not self._parser.has_section(section):
            # Accept bare section names too, matching ~/.aws/credentials style.
            if name != DEFAULT_PROFILE and self._parser.has_section(name):
                section = name
            else:
                return {}
        return {
            key: value
            for key, value in self._parser.items(section)
            if key in PROFILE_KEYS and value
        }

    def set_profile(self, values: Dict[str, str], profile: Optional[str] = None) -> None:
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

    def resolve(self, profile: Optional[str] = None) -> Dict[str, str]:
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
            "s3_endpoint_url": (
                os.getenv("S3_ENDPOINT_URL")
                or stored.get("s3_endpoint_url")
                or DEFAULT_S3_ENDPOINT
            ),
        }

    def apply_to_environ(self, profile: Optional[str] = None) -> Dict[str, str]:
        """Export resolved credentials into the process environment."""
        resolved = self.resolve(profile)
        mapping = {
            "hetzner_cloud_api_token": "HETZNER_CLOUD_API_TOKEN",
            "hetzner_s3_access_key": "HETZNER_S3_ACCESS_KEY",
            "hetzner_s3_secret_key": "HETZNER_S3_SECRET_KEY",
            "s3_endpoint_url": "S3_ENDPOINT_URL",
        }
        for key, env_name in mapping.items():
            value = resolved.get(key) or ""
            if value and not os.getenv(env_name):
                os.environ[env_name] = value
        return resolved


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
    config: Optional[RigConfig] = None,
    input_fn: Optional[Callable[..., str]] = None,
    secret_fn: Optional[Callable[..., str]] = None,
    force_prompt: bool = False,
) -> Dict[str, str]:
    """Return credentials, prompting and saving to ~/.rig/config when needed."""
    config = config or RigConfig()
    resolved = config.resolve()
    missing_token = not resolved.get("hetzner_cloud_api_token")
    missing_s3 = not (
        resolved.get("hetzner_s3_access_key") and resolved.get("hetzner_s3_secret_key")
    )

    if not force_prompt and not missing_token and not missing_s3:
        config.apply_to_environ()
        return resolved

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


# Backwards-compatible wrapper used by older imports.
class Config:
    """Legacy project-local YAML config wrapper."""

    def __init__(self, config_file: str = "config.yaml"):
        self.config_file = config_file
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        default_config = {
            "control_plane_url": "http://localhost:5000",
            "api_token": os.getenv("CONTROL_PLANE_TOKEN", ""),
            "default_provider": "ansible",
        }
        if os.path.exists(self.config_file):
            import yaml
            with open(self.config_file, "r") as handle:
                file_config = yaml.safe_load(handle) or {}
                merged = default_config.copy()
                merged.update(file_config)
                return merged
        return default_config

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value
        self._save_config()

    def _save_config(self) -> None:
        import yaml
        with open(self.config_file, "w") as handle:
            yaml.dump(self.config, handle)
