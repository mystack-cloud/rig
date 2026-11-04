"""Tests for ~/.rig/config profile credentials."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.config import RigConfig, ensure_credentials


def test_set_and_resolve_default_profile(tmp_path, monkeypatch):
    path = tmp_path / "config"
    monkeypatch.delenv("HETZNER_CLOUD_API_TOKEN", raising=False)
    monkeypatch.delenv("HZ_TOKEN", raising=False)
    monkeypatch.delenv("HETZNER_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("HETZNER_S3_SECRET_KEY", raising=False)
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)

    config = RigConfig(path=str(path))
    config.set_profile({
        "hetzner_cloud_api_token": "token-default",
        "hetzner_s3_access_key": "ak",
        "hetzner_s3_secret_key": "sk",
        "s3_endpoint_url": "https://hel1.your-objectstorage.com",
    })

    text = path.read_text()
    assert "[default]" in text
    assert "hetzner_cloud_api_token = token-default" in text
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)

    resolved = RigConfig(path=str(path)).resolve()
    assert resolved["hetzner_cloud_api_token"] == "token-default"
    assert resolved["hetzner_s3_access_key"] == "ak"
    assert resolved["s3_endpoint_url"] == "https://hel1.your-objectstorage.com"


def test_named_profile_uses_aws_style_section(tmp_path, monkeypatch):
    path = tmp_path / "config"
    monkeypatch.delenv("HETZNER_CLOUD_API_TOKEN", raising=False)
    config = RigConfig(path=str(path), profile="staging")
    config.set_profile({
        "hetzner_cloud_api_token": "token-staging",
        "hetzner_s3_access_key": "ak",
        "hetzner_s3_secret_key": "sk",
    })
    text = path.read_text()
    assert "[profile staging]" in text
    assert RigConfig(path=str(path), profile="staging").resolve()["hetzner_cloud_api_token"] == "token-staging"


def test_env_overrides_profile(tmp_path, monkeypatch):
    path = tmp_path / "config"
    config = RigConfig(path=str(path))
    config.set_profile({
        "hetzner_cloud_api_token": "from-file",
        "hetzner_s3_access_key": "ak",
        "hetzner_s3_secret_key": "sk",
    })
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "from-env")
    assert RigConfig(path=str(path)).resolve()["hetzner_cloud_api_token"] == "from-env"


def test_ensure_credentials_prompts_and_saves(tmp_path, monkeypatch):
    path = tmp_path / "config"
    for key in (
        "HETZNER_CLOUD_API_TOKEN",
        "HZ_TOKEN",
        "HETZNER_S3_ACCESS_KEY",
        "HETZNER_S3_SECRET_KEY",
        "S3_ENDPOINT_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    answers = iter([
        "tok-123",
        "access-123",
        "secret-123",
        "https://fsn1.your-objectstorage.com",
    ])
    values = ensure_credentials(
        RigConfig(path=str(path)),
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: next(answers),
    )
    assert values["hetzner_cloud_api_token"] == "tok-123"
    assert os.getenv("HETZNER_CLOUD_API_TOKEN") == "tok-123"
    saved = RigConfig(path=str(path)).get_profile()
    assert saved["hetzner_cloud_api_token"] == "tok-123"
    assert saved["hetzner_s3_access_key"] == "access-123"


def test_ensure_credentials_skips_prompt_when_env_set(tmp_path, monkeypatch):
    path = tmp_path / "config"
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "env-token")
    monkeypatch.setenv("HETZNER_S3_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("HETZNER_S3_SECRET_KEY", "env-sk")

    def fail_prompt(_prompt):
        raise AssertionError("should not prompt")

    values = ensure_credentials(
        RigConfig(path=str(path)),
        input_fn=fail_prompt,
        secret_fn=fail_prompt,
    )
    assert values["hetzner_cloud_api_token"] == "env-token"
    assert not path.exists()
