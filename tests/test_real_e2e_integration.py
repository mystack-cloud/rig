"""
Real E2E integration tests
Converted from real_e2e_test.sh
"""

import pytest
import os
import sys
import json
import uuid
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_requires_credentials():
    """Check if credentials are available for E2E tests"""
    assert os.getenv('HETZNER_CLOUD_API_TOKEN') is not None, "Requires HETZNER_CLOUD_API_TOKEN environment variable"


@pytest.fixture
def unique_bucket_name(hetzner_token):
    """Generate unique bucket name"""
    timestamp = int(uuid.uuid4().time_low)
    return f"e2e-integration-{hetzner_token[:8].lower()}-{timestamp}"


@pytest.fixture
def shared_bucket_name(hetzner_token):
    """Generate unique shared bucket name"""
    timestamp = int(uuid.uuid4().time_low)
    return f"e2e-shared-{hetzner_token[:8].lower()}-{timestamp}"


@pytest.mark.requires_credentials
@pytest.mark.integration
@pytest.mark.e2e
class TestRealE2EIntegrationComplete:
    """Complete real E2E integration tests from real_e2e_test.sh"""

    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")

    def test_e2e_test_1_basic_storage_operations(self, hetzner_token, unique_bucket_name, tmp_path):
        """Test E2E TEST 1: Basic Storage Operations from real_e2e_test.sh"""
        from cli.storage import StorageManager

        storage = StorageManager()
        object_name = 'development_env/state.json'
        test_file_content = "This is test file content for environment state."

        buckets = storage.list_buckets()
        assert isinstance(buckets, list)
        assert storage.create_bucket(unique_bucket_name) is True

        try:
            test_file = tmp_path / "test_state_file.txt"
            test_file.write_text(test_file_content)
            assert storage.upload_file(unique_bucket_name, str(test_file), object_name) is True

            objects = storage.list_objects(unique_bucket_name, '')
            assert object_name in objects

            download_path = tmp_path / "downloaded_state.json"
            assert storage.download_file(unique_bucket_name, object_name, str(download_path)) is True
            assert download_path.read_text() == test_file_content

            assert storage.delete_object(unique_bucket_name, object_name) is True
        finally:
            storage.delete_object(unique_bucket_name, object_name)
            assert storage.delete_bucket(unique_bucket_name) is True

    def test_e2e_test_2_shared_state_management(
        self, hetzner_token, shared_bucket_name, tmp_path
    ):
        """Test E2E TEST 2: Shared State Management from real_e2e_test.sh"""
        from cli.storage import StorageManager

        storage = StorageManager()
        object_name = 'shared_state.json'
        shared_state_data = "Shared state data for all environments"

        assert storage.create_bucket(shared_bucket_name) is True
        try:
            shared_file = tmp_path / "shared_state.txt"
            shared_file.write_text(shared_state_data)
            assert storage.upload_file(shared_bucket_name, str(shared_file), object_name) is True

            objects = storage.list_objects(shared_bucket_name, '')
            assert object_name in objects

            download_path = tmp_path / "downloaded_shared_state.json"
            assert storage.download_file(shared_bucket_name, object_name, str(download_path)) is True
            assert download_path.read_text() == shared_state_data

            assert storage.delete_object(shared_bucket_name, object_name) is True
        finally:
            storage.delete_object(shared_bucket_name, object_name)
            assert storage.delete_bucket(shared_bucket_name) is True

    def test_e2e_complete_workflow(self, hetzner_token, unique_bucket_name, shared_bucket_name, tmp_path):
        """Test complete E2E workflow combining all operations"""
        from cli.storage import StorageManager

        storage = StorageManager()
        env_object = 'development_env/state.json'
        shared_object = 'shared_state.json'
        test_state = {
            'environment': 'production',
            'status': 'active',
            'timestamp': str(uuid.uuid4())
        }
        shared_state = {
            'shared': True,
            'version': '1.0'
        }

        assert storage.create_bucket(unique_bucket_name) is True
        assert storage.create_bucket(shared_bucket_name) is True
        try:
            env_file = tmp_path / "test_state_file.txt"
            env_file.write_text(json.dumps(test_state))
            assert storage.upload_file(unique_bucket_name, str(env_file), env_object) is True

            shared_file = tmp_path / "shared_state.txt"
            shared_file.write_text(json.dumps(shared_state))
            assert storage.upload_file(shared_bucket_name, str(shared_file), shared_object) is True

            env_download = tmp_path / "downloaded_state.json"
            shared_download = tmp_path / "downloaded_shared_state.json"
            assert storage.download_file(unique_bucket_name, env_object, str(env_download)) is True
            assert storage.download_file(shared_bucket_name, shared_object, str(shared_download)) is True
            assert json.loads(env_download.read_text())['environment'] == 'production'
            assert json.loads(shared_download.read_text())['shared'] is True
        finally:
            storage.delete_object(unique_bucket_name, env_object)
            storage.delete_object(shared_bucket_name, shared_object)
            storage.delete_bucket(unique_bucket_name)
            storage.delete_bucket(shared_bucket_name)

    def test_e2e_with_mocked_s3_client(self, hetzner_token, unique_bucket_name, tmp_path):
        """Test E2E workflow with mocked S3 client"""
        from cli.storage import StorageManager

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        download_path = tmp_path / "download.txt"

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            mock_s3.create_bucket.return_value = True
            mock_s3.list_buckets.return_value = {
                'Buckets': [{'Name': unique_bucket_name}]
            }
            mock_s3.upload_file.return_value = True
            mock_s3.list_objects_v2.return_value = {
                'Contents': [{'Key': 'test.txt', 'Size': 10}]
            }
            mock_s3.download_file = lambda Bucket, Key, Filename: open(Filename, 'w').write('content')
            mock_s3.delete_object.return_value = True
            mock_s3.delete_bucket.return_value = True

            assert storage.create_bucket(unique_bucket_name) is True

            buckets = storage.list_buckets()
            assert unique_bucket_name in buckets

            assert storage.upload_file(unique_bucket_name, str(test_file), 'test.txt') is True

            objects = storage.list_objects(unique_bucket_name, '')
            assert objects == ['test.txt']

            assert storage.download_file(unique_bucket_name, 'test.txt', str(download_path)) is True
            assert download_path.read_text() == 'content'

            assert storage.delete_object(unique_bucket_name, 'test.txt') is True
            assert storage.delete_bucket(unique_bucket_name) is True

    def test_e2e_token_security(self, hetzner_token):
        """Test that token security features work in E2E context"""
        from cli.storage import StorageManager

        storage = StorageManager()

        # Token should be loaded
        assert storage.hetzner_token == hetzner_token

        # Credentials info should mask token
        creds_info = storage.get_credentials_info()
        assert 'token_used' in creds_info

        # Token should not be exposed in string representation
        assert hetzner_token not in str(storage)


@pytest.mark.requires_credentials
@pytest.mark.integration
@pytest.mark.e2e
class TestRealE2EErrorHandling:
    """Test E2E error handling scenarios"""

    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")

    def test_e2e_with_missing_bucket(self, hetzner_token):
        """Test E2E operations with non-existent bucket"""
        from cli.storage import StorageManager

        storage = StorageManager()

        non_existent_bucket = f"non-existent-{hetzner_token[:8].lower()}-{uuid.uuid4().hex[:8]}"

        # List objects from non-existent bucket should handle gracefully
        objects = storage.list_objects(non_existent_bucket, '')
        # Should return empty list or handle error
        assert isinstance(objects, list)

    def test_e2e_with_invalid_file(self, hetzner_token, tmp_path, unique_bucket_name):
        """Test E2E operations with invalid file paths"""
        from cli.storage import StorageManager

        storage = StorageManager()

        invalid_file = str(tmp_path / "nonexistent.txt")
        result = storage.upload_file(unique_bucket_name, invalid_file, 'test.txt')
        assert result is False

        download_result = storage.download_file(
            unique_bucket_name,
            'test.txt',
            str(tmp_path / "missing-dir" / "file.txt")
        )
        assert download_result is False
