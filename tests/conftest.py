"""
Shared fixtures and configuration for pytest tests
"""

import pytest
import os
import sys
from unittest.mock import MagicMock, patch


# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line(
        "markers", "requires_credentials: test requires real Hetzner credentials"
    )
    config.addinivalue_line(
        "markers", "integration: integration test with real API"
    )
    config.addinivalue_line(
        "markers", "e2e: end-to-end test"
    )


@pytest.fixture
def mock_storage_manager(mocker=None):
    """Mock storage manager for tests"""
    if mocker:
        return mocker.MagicMock()
    return MagicMock()


@pytest.fixture
def hetzner_token():
    """Get Hetzner token from environment"""
    return os.getenv('HETZNER_CLOUD_API_TOKEN', 'test-hetzner-token-12345')


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
    return mocker.patch('requests.request')


@pytest.fixture
def mock_s3_client(mocker):
    """Mock S3 client for testing"""
    return mocker.patch('boto3.client')


@pytest.fixture(autouse=True)
def setup_test_env(request):
    """Setup test environment variables (only for non-E2E tests without real credentials)"""
    # Only set fake values for tests that aren't marked as e2e or requires_credentials
    # E2E tests and tests requiring credentials should use real endpoints with real credentials
    if request.node.get_closest_marker('e2e') or request.node.get_closest_marker('requires_credentials'):
        # Don't override environment variables for E2E tests or tests requiring credentials
        # Yield anyway to satisfy pytest fixture protocol
        yield
        return
    
    # Set test environment variables for regular unit tests
    os.environ['HETZNER_CLOUD_API_TOKEN'] = 'test-token-for-testing'
    os.environ['S3_ENDPOINT_URL'] = 'https://fsn1.test.example.com'
    os.environ['HETZNER_S3_ACCESS_KEY'] = 'test-access-key'
    os.environ['HETZNER_S3_SECRET_KEY'] = 'test-secret-key'
    os.environ.pop('STATE_BUCKET', None)  # Clear any existing state bucket
    os.environ.pop('TEST_BUCKET_PREFIX', None)  # Clear test prefix
    yield
    # Cleanup after test
    os.environ.pop('HETZNER_CLOUD_API_TOKEN', None)
    os.environ.pop('S3_ENDPOINT_URL', None)
    os.environ.pop('HETZNER_S3_ACCESS_KEY', None)
    os.environ.pop('HETZNER_S3_SECRET_KEY', None)
