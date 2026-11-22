"""
Shared fixtures and configuration for pytest tests
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _load_dotenv():
    """Load repo .env without overriding variables already set in the environment."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path) as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)
            if key == "HETZNER_CLOUD_OBJECT_STORAGE_ACCESS_KEY":
                os.environ.setdefault("HETZNER_S3_ACCESS_KEY", value)
            elif key == "HETZNER_CLOUD_OBJECT_STORAGE_SECRET_KEY":
                os.environ.setdefault("HETZNER_S3_SECRET_KEY", value)


_load_dotenv()


def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line("markers", "requires_credentials: test requires real Hetzner credentials")
    config.addinivalue_line("markers", "integration: integration test with real API")
    config.addinivalue_line("markers", "e2e: end-to-end test")


@pytest.fixture
def mock_storage_manager(mocker=None):
    """Mock storage manager for tests"""
    if mocker:
        return mocker.MagicMock()
    return MagicMock()


@pytest.fixture
def hetzner_token():
    """Get Hetzner token from environment"""
    return os.getenv("HETZNER_CLOUD_API_TOKEN", "test-hetzner-token-12345")


@pytest.fixture
def test_bucket_name():
    """Generate a unique test bucket name"""
    import uuid

    return f"test-bucket-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def test_file(tmp_path):
    """Create a temporary test file"""
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("This is test content")
    return str(test_file)


@pytest.fixture
def mock_hetzner_api(mocker):
    """Mock Hetzner Cloud API calls"""
    return mocker.patch("requests.request")


@pytest.fixture
def mock_s3_client(mocker):
    """Mock S3 client for testing"""
    return mocker.patch("boto3.client")


@pytest.fixture(autouse=True)
def setup_test_env(request):
    """Setup test environment variables (only for non-E2E tests without real credentials)"""
    # E2E tests and tests requiring credentials use real endpoints and credentials.
    if request.node.get_closest_marker("e2e") or request.node.get_closest_marker("requires_credentials"):
        yield
        return

    overridden = (
        "HETZNER_CLOUD_API_TOKEN",
        "S3_ENDPOINT_URL",
        "HETZNER_S3_ACCESS_KEY",
        "HETZNER_S3_SECRET_KEY",
        "RIG_STATE_ENDPOINT_URL",
        "RIG_STATE_ACCESS_KEY",
        "RIG_STATE_SECRET_KEY",
        "STATE_BUCKET",
        "TEST_BUCKET_PREFIX",
    )
    saved = {key: os.environ.get(key) for key in overridden}

    os.environ["HETZNER_CLOUD_API_TOKEN"] = "test-token-for-testing"
    os.environ["S3_ENDPOINT_URL"] = "https://fsn1.test.example.com"
    os.environ["HETZNER_S3_ACCESS_KEY"] = "test-access-key"
    os.environ["HETZNER_S3_SECRET_KEY"] = "test-secret-key"
    # RigConfig.apply_to_environ exports RIG_STATE_*; keep it from leaking between tests.
    for name in ("RIG_STATE_ENDPOINT_URL", "RIG_STATE_ACCESS_KEY", "RIG_STATE_SECRET_KEY"):
        os.environ.pop(name, None)
    os.environ.pop("STATE_BUCKET", None)
    os.environ.pop("TEST_BUCKET_PREFIX", None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _isolate_rig_config(tmp_path, monkeypatch):
    """
    Point ~/.rig/config at a per-test file.

    rig init / RemoteDevManager persist ``state_bucket`` (and identity) into
    the user config; tests must never read or write the real one.
    """
    monkeypatch.setenv("RIG_CONFIG", str(tmp_path / "rig-config"))


@pytest.fixture(autouse=True)
def _isolate_rig_tmp_keys(tmp_path, monkeypatch):
    """
    Point ephemeral data-key paths at a per-test directory.

    Never unlink the developer's real ``/tmp/.rig/.{personal,shared,data}-key``
    files — those keep an unlocked session alive across reboot boundaries.
    """
    key_dir = tmp_path / "rig-tmp-keys"
    key_dir.mkdir(exist_ok=True)
    personal = str(key_dir / ".personal-data-key")
    shared = str(key_dir / ".shared-data-key")
    legacy = str(key_dir / ".data-key")

    monkeypatch.setattr("cli.vault_keys.TMP_RIG_DIR", str(key_dir))
    monkeypatch.setattr("cli.vault_keys.PERSONAL_DATA_KEY_PATH", personal)
    monkeypatch.setattr("cli.vault_keys.SHARED_DATA_KEY_PATH", shared)
    monkeypatch.setattr("cli.vault_keys.DEFAULT_DATA_KEY_PATH", legacy)

    try:
        import cli.encrypt as encrypt_mod

        monkeypatch.setattr(encrypt_mod, "PERSONAL_DATA_KEY_PATH", personal)
        monkeypatch.setattr(encrypt_mod, "SHARED_DATA_KEY_PATH", shared)
        monkeypatch.setattr(encrypt_mod, "DEFAULT_DATA_KEY_PATH", legacy)
    except ImportError:
        pass
