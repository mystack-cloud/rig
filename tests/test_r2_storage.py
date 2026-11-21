"""Cloudflare R2 as the state backend, selected by the S3 endpoint."""

import os
import sys
import uuid
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.storage import StorageManager

R2_ENDPOINT = "https://acct123.r2.cloudflarestorage.com"


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "hz-token")
    monkeypatch.setenv("HETZNER_S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("HETZNER_S3_SECRET_KEY", "sk")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    "endpoint",
    [
        R2_ENDPOINT,
        "https://acct123.eu.r2.cloudflarestorage.com",
        "https://acct123.fedramp.r2.cloudflarestorage.com",
    ],
)
def test_r2_endpoint_is_detected_and_signs_with_auto(s3_env, endpoint):
    s3_env.setenv("S3_ENDPOINT_URL", endpoint)
    sm = StorageManager()
    assert sm.is_r2
    assert sm._endpoint_region() == "auto"


@pytest.mark.parametrize(
    "endpoint,region",
    [
        ("https://fsn1.your-objectstorage.com", "fsn1"),
        ("https://hel1.your-objectstorage.com", "hel1"),
        ("https://r2.cloudflarestorage.com.evil.example", "fsn1"),
        ("https://notr2.cloudflarestorage.com", "fsn1"),
    ],
)
def test_other_endpoints_keep_hetzner_behaviour(s3_env, endpoint, region):
    s3_env.setenv("S3_ENDPOINT_URL", endpoint)
    sm = StorageManager()
    assert not sm.is_r2
    assert sm._endpoint_region() == region


def test_r2_client_gets_endpoint_and_auto_region(s3_env):
    s3_env.setenv("S3_ENDPOINT_URL", R2_ENDPOINT)
    with patch("boto3.client") as boto:
        StorageManager()._get_s3_client()
    kwargs = boto.call_args.kwargs
    assert kwargs["endpoint_url"] == R2_ENDPOINT
    assert kwargs["region_name"] == "auto"


def test_credentials_info_names_r2_token(s3_env):
    s3_env.setenv("S3_ENDPOINT_URL", R2_ENDPOINT)
    info = StorageManager().get_credentials_info()
    assert info["endpoint"] == R2_ENDPOINT
    assert "Hetzner" not in info["credentials_expiration"]


@pytest.mark.e2e
@pytest.mark.requires_credentials
def test_live_r2_roundtrip(tmp_path, monkeypatch):
    """Real R2 round trip. Needs CF_R2_ACCOUNT_ID, CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY (e.g. in .env)."""
    account, ak, sk = (os.getenv(n) for n in ("CF_R2_ACCOUNT_ID", "CF_R2_ACCESS_KEY", "CF_R2_SECRET_KEY"))
    if not (account and ak and sk):
        pytest.skip("CF_R2_ACCOUNT_ID / CF_R2_ACCESS_KEY / CF_R2_SECRET_KEY not set")
    monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "unused-for-storage")
    monkeypatch.setenv("S3_ENDPOINT_URL", f"https://{account}.r2.cloudflarestorage.com")
    monkeypatch.setenv("HETZNER_S3_ACCESS_KEY", ak)
    monkeypatch.setenv("HETZNER_S3_SECRET_KEY", sk)
    sm = StorageManager()
    assert sm.is_r2
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
