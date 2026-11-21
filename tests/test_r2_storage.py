"""Cloudflare R2 as the state backend (RIG_STORAGE_PROVIDER=r2)."""

import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.config import RigConfig, ensure_credentials
from cli.storage import StorageManager

R2_ENV = (
    "RIG_STORAGE_PROVIDER",
    "CF_R2_ACCOUNT_ID",
    "CF_R2_ACCESS_KEY",
    "CF_R2_SECRET_KEY",
    "CF_R2_ENDPOINT_URL",
    "HETZNER_S3_ACCESS_KEY",
    "HETZNER_S3_SECRET_KEY",
    "S3_ENDPOINT_URL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "hz-token")
    return monkeypatch


@pytest.fixture
def r2_env(clean_env):
    clean_env.setenv("RIG_STORAGE_PROVIDER", "r2")
    clean_env.setenv("CF_R2_ACCOUNT_ID", "acct123")
    clean_env.setenv("CF_R2_ACCESS_KEY", "r2-ak")
    clean_env.setenv("CF_R2_SECRET_KEY", "r2-sk")
    return clean_env


def test_r2_endpoint_region_and_credentials(r2_env):
    sm = StorageManager()
    assert sm.provider == "r2"
    assert sm.endpoint_url == "https://acct123.r2.cloudflarestorage.com"
    assert sm._endpoint_region() == "auto"
    assert (sm.s3_access_key, sm.s3_secret_key) == ("r2-ak", "r2-sk")


def test_r2_client_uses_r2_endpoint_and_auto_region(r2_env):
    with patch("boto3.client") as boto:
        StorageManager()._get_s3_client()
    kwargs = boto.call_args.kwargs
    assert kwargs["endpoint_url"] == "https://acct123.r2.cloudflarestorage.com"
    assert kwargs["region_name"] == "auto"
    assert kwargs["aws_access_key_id"] == "r2-ak"


def test_r2_explicit_endpoint_wins(r2_env):
    r2_env.setenv("CF_R2_ENDPOINT_URL", "https://acct123.eu.r2.cloudflarestorage.com")
    assert StorageManager().endpoint_url == "https://acct123.eu.r2.cloudflarestorage.com"


def test_r2_ignores_hetzner_s3_keys(r2_env):
    r2_env.setenv("HETZNER_S3_ACCESS_KEY", "hz-ak")
    r2_env.setenv("HETZNER_S3_SECRET_KEY", "hz-sk")
    assert StorageManager().s3_access_key == "r2-ak"


@pytest.mark.parametrize("missing", ["CF_R2_ACCOUNT_ID", "CF_R2_ACCESS_KEY", "CF_R2_SECRET_KEY"])
def test_r2_requires_endpoint_and_credentials(r2_env, missing):
    r2_env.delenv(missing)
    with pytest.raises(ValueError, match="R2"):
        StorageManager()


def test_unknown_provider_rejected(clean_env):
    clean_env.setenv("RIG_STORAGE_PROVIDER", "gcs")
    with pytest.raises(ValueError, match="Unknown storage provider"):
        StorageManager()


def test_default_provider_is_hetzner(clean_env):
    clean_env.setenv("HETZNER_S3_ACCESS_KEY", "ak")
    clean_env.setenv("HETZNER_S3_SECRET_KEY", "sk")
    sm = StorageManager()
    assert sm.provider == "hetzner"
    assert sm._endpoint_region() == "fsn1"


def test_credentials_info_reports_provider(r2_env):
    info = StorageManager().get_credentials_info()
    assert info["provider"] == "r2"
    assert "r2-sk" not in str(info)


def test_config_resolves_and_exports_r2(tmp_path, clean_env):
    cfg = RigConfig(path=str(tmp_path / "config"))
    cfg.set_profile(
        {
            "storage_provider": "r2",
            "r2_account_id": "acct123",
            "r2_access_key": "ak",
            "r2_secret_key": "sk",
        }
    )
    resolved = RigConfig(path=str(tmp_path / "config")).resolve()
    assert resolved["storage_provider"] == "r2"
    assert resolved["r2_account_id"] == "acct123"
    RigConfig(path=str(tmp_path / "config")).apply_to_environ()
    assert os.environ["CF_R2_ACCESS_KEY"] == "ak"
    assert os.environ["RIG_STORAGE_PROVIDER"] == "r2"
    for name in ("RIG_STORAGE_PROVIDER", "CF_R2_ACCOUNT_ID", "CF_R2_ACCESS_KEY", "CF_R2_SECRET_KEY"):
        os.environ.pop(name, None)


def test_ensure_credentials_r2_skips_hetzner_s3_prompt(tmp_path, r2_env):
    r2_env.delenv("CF_R2_ACCOUNT_ID")
    cfg = RigConfig(path=str(tmp_path / "config"))
    answers = iter(["acct999"])
    values = ensure_credentials(cfg, input_fn=lambda *_: next(answers), secret_fn=lambda *_: "")
    assert values["r2_account_id"] == "acct999"
    assert values["storage_provider"] == "r2"
    assert not values.get("hetzner_s3_access_key")


def test_ensure_credentials_r2_complete_does_not_prompt(tmp_path, r2_env):
    def boom(*_):
        raise AssertionError("prompted")

    values = ensure_credentials(RigConfig(path=str(tmp_path / "config")), input_fn=boom, secret_fn=boom)
    assert values["r2_access_key"] == "r2-ak"


def test_ensure_credentials_unknown_provider(tmp_path, clean_env):
    clean_env.setenv("RIG_STORAGE_PROVIDER", "nope")
    with pytest.raises(ValueError, match="Unknown storage provider"):
        ensure_credentials(RigConfig(path=str(tmp_path / "config")))


def test_r2_missing_object_is_not_found_without_hetzner_assumptions(r2_env):
    """download_file on a missing key returns False (fast path when retries are off)."""
    from botocore.exceptions import ClientError

    sm = StorageManager()
    client = MagicMock()
    client.download_file.side_effect = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
    with patch.object(sm, "_get_s3_client", return_value=client):
        assert sm.download_file("b", "k", "/tmp/x", retry_not_found=False) is False
    assert client.download_file.call_count == 1


@pytest.mark.e2e
@pytest.mark.requires_credentials
def test_live_r2_roundtrip(tmp_path):
    """Create bucket, put/list/get/delete against real R2. Needs CF_R2_* incl. CF_R2_ACCOUNT_ID."""
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        for line in open(env_file):
            key, sep, value = line.strip().partition("=")
            if sep and key.startswith("CF_R2_"):
                os.environ.setdefault(key, value.strip().strip("'\""))
    needed = ("CF_R2_ACCOUNT_ID", "CF_R2_ACCESS_KEY", "CF_R2_SECRET_KEY")
    if not all(os.getenv(n) for n in needed):
        pytest.skip("CF_R2_ACCOUNT_ID / CF_R2_ACCESS_KEY / CF_R2_SECRET_KEY not set")
    os.environ["RIG_STORAGE_PROVIDER"] = "r2"
    os.environ.setdefault("HETZNER_CLOUD_API_TOKEN", "unused-for-storage")
    try:
        sm = StorageManager()
        bucket = f"rig-test-{uuid.uuid4().hex[:8]}"
        assert sm.create_bucket(bucket)
        try:
            src = tmp_path / "a.yml"
            src.write_text("name: x\n")
            assert sm.upload_file(bucket, str(src), "p/a.yml")
            assert sm.list_objects(bucket, "p/") == ["p/a.yml"]
            dst = tmp_path / "b.yml"
            assert sm.download_file(bucket, "p/a.yml", str(dst))
            assert dst.read_text() == "name: x\n"
            assert sm.download_file(bucket, "p/missing.yml", str(tmp_path / "c"), retry_not_found=False) is False
        finally:
            sm.delete_object(bucket, "p/a.yml")
            sm.delete_bucket(bucket)
    finally:
        os.environ.pop("RIG_STORAGE_PROVIDER", None)
