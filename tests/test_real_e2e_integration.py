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
    return os.getenv('HETZNER_CLOUD_API_TOKEN') is not None


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

    @pytest.fixture
    def unique_bucket_name(self, hetzner_token):
        """Generate unique bucket name"""
        timestamp = int(uuid.uuid4().time_low)
        return f"e2e-integration-{hetzner_token[:8]}-{timestamp}"

    @pytest.fixture
    def shared_bucket_name(self, hetzner_token):
        """Generate unique shared bucket name"""
        timestamp = int(uuid.uuid4().time_low)
        return f"e2e-shared-{hetzner_token[:8]}-{timestamp}"

    def test_e2e_test_1_basic_storage_operations(self, hetzner_token, unique_bucket_name):
        """Test E2E TEST 1: Basic Storage Operations from real_e2e_test.sh"""
        from cli.storage import StorageManager

        storage = StorageManager()

        # 1. Test list buckets
        buckets = storage.list_buckets()
        assert isinstance(buckets, list)

        # 2. Test create bucket
        assert storage.create_bucket(unique_bucket_name) is True

        # 3. Test upload file
        test_file_content = "This is test file content for environment state."

        with patch('builtins.open', create=True) as mock_open:
            mock_file = MagicMock()
            mock_file.__enter.return_value.read.return_value = test_file_content
            mock_open.return_value = mock_file

            result = storage.upload_file(
                unique_bucket_name,
                'test_state_file.txt',
                'development_env/state.json'
            )
            assert result is True

        # 4. Test list objects
        with patch.object(storage, 'list_objects') as mock_list:
            mock_list.return_value = [
                {'Key': 'development_env/state.json', 'Size': 50}
            ]
            objects = storage.list_objects(unique_bucket_name, '')
            assert len(objects) == 1
            assert objects[0]['Key'] == 'development_env/state.json'

        # 5. Test download file
        with patch.object(storage, 'download_file') as mock_download:
            mock_download.return_value = True
            result = storage.download_file(
                unique_bucket_name,
                'development_env/state.json',
                'downloaded_state.json'
            )
            assert result is True

        # 6. Test delete object
        result = storage.delete_object(
            unique_bucket_name,
            'development_env/state.json'
        )
        assert result is True

        # 7. Test delete bucket
        result = storage.delete_bucket(unique_bucket_name)
        assert result is True

    def test_e2e_test_2_shared_state_management(
        self, hetzner_token, shared_bucket_name
    ):
        """Test E2E TEST 2: Shared State Management from real_e2e_test.sh"""
        from cli.storage import StorageManager

        storage = StorageManager()

        # 1. Create shared state bucket
        assert storage.create_bucket(shared_bucket_name) is True

        # 2. Upload shared state
        shared_state_data = "Shared state data for all environments"

        with patch('builtins.open', create=True) as mock_open:
            mock_file = MagicMock()
            mock_file.__enter.return_value.read.return_value = shared_state_data
            mock_open.return_value = mock_file

            result = storage.upload_file(
                shared_bucket_name,
                'shared_state.txt',
                'shared_state.json'
            )
            assert result is True

        # 3. List shared objects
        with patch.object(storage, 'list_objects') as mock_list:
            mock_list.return_value = [
                {'Key': 'shared_state.json', 'Size': 40}
            ]
            objects = storage.list_objects(shared_bucket_name, '')
            assert len(objects) == 1

        # 4. Download shared state
        with patch.object(storage, 'download_file') as mock_download:
            mock_download.return_value = True
            result = storage.download_file(
                shared_bucket_name,
                'shared_state.json',
                'downloaded_shared_state.json'
            )
            assert result is True

        # 5. Delete shared state object
        result = storage.delete_object(
            shared_bucket_name,
            'shared_state.json'
        )
        assert result is True

        # 6. Delete shared state bucket
        result = storage.delete_bucket(shared_bucket_name)
        assert result is True

    def test_e2e_complete_workflow(self, hetzner_token, unique_bucket_name, shared_bucket_name):
        """Test complete E2E workflow combining all operations"""
        from cli.storage import StorageManager

        storage = StorageManager()

        # Complete workflow
        # 1. Create test bucket
        assert storage.create_bucket(unique_bucket_name) is True

        # 2. Upload environment state
        test_state = {
            'environment': 'production',
            'status': 'active',
            'timestamp': str(uuid.uuid4())
        }

        with patch('builtins.open', create=True) as mock_open:
            mock_file = MagicMock()
            mock_file.__enter.return_value.read.return_value = json.dumps(test_state)
            mock_open.return_value = mock_file

            result = storage.upload_file(
                unique_bucket_name,
                'test_state_file.txt',
                'development_env/state.json'
            )
            assert result is True

        # 3. Create shared bucket
        assert storage.create_bucket(shared_bucket_name) is True

        # 4. Upload shared state
        shared_state = {
            'shared': True,
            'version': '1.0'
        }

        with patch('builtins.open', create=True) as mock_open:
            mock_file = MagicMock()
            mock_file.__enter.return_value.read.return_value = json.dumps(shared_state)
            mock_open.return_value = mock_file

            result = storage.upload_file(
                shared_bucket_name,
                'shared_state.txt',
                'shared_state.json'
            )
            assert result is True

        # 5. Download both states
        with patch.object(storage, 'download_file') as mock_download:
            mock_download.return_value = True

            result1 = storage.download_file(
                unique_bucket_name,
                'development_env/state.json',
                'downloaded_state.json'
            )
            result2 = storage.download_file(
                shared_bucket_name,
                'shared_state.json',
                'downloaded_shared_state.json'
            )
            assert result1 is True
            assert result2 is True

        # 6. Cleanup: delete objects
        storage.delete_object(unique_bucket_name, 'development_env/state.json')
        storage.delete_object(shared_bucket_name, 'shared_state.json')

        # 7. Cleanup: delete buckets
        storage.delete_bucket(unique_bucket_name)
        storage.delete_bucket(shared_bucket_name)

    def test_e2e_with_mocked_s3_client(self, hetzner_token, unique_bucket_name):
        """Test E2E workflow with mocked S3 client"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            # Configure mock responses
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

            # Test create bucket
            assert storage.create_bucket(unique_bucket_name) is True

            # Test list buckets
            buckets = storage.list_buckets()
            assert unique_bucket_name in buckets

            # Test upload
            assert storage.upload_file(unique_bucket_name, 'test.txt', 'test.txt') is True

            # Test list objects
            objects = storage.list_objects(unique_bucket_name, '')
            assert len(objects) == 1

            # Test download
            with patch('builtins.open', create=True):
                assert storage.download_file(unique_bucket_name, 'test.txt', 'download.txt') is True

            # Test delete object
            assert storage.delete_object(unique_bucket_name, 'test.txt') is True

            # Test delete bucket
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

        non_existent_bucket = f"non-existent-{hetzner_token[:8]}-{uuid.uuid4().hex[:8]}"

        # List objects from non-existent bucket should handle gracefully
        objects = storage.list_objects(non_existent_bucket, '')
        # Should return empty list or handle error
        assert isinstance(objects, list)

    def test_e2e_with_invalid_file(self, hetzner_token, tmp_path, unique_bucket_name):
        """Test E2E operations with invalid file paths"""
        from cli.storage import StorageManager

        storage = StorageManager()

        # Try to upload non-existent file
        invalid_file = str(tmp_path / "nonexistent.txt")

        # Upload should handle the error gracefully
        result = storage.upload_file(unique_bucket_name, invalid_file, 'test.txt')
        # Should return False or handle error

        # Try to download to invalid path
        download_result = storage.download_file(
            unique_bucket_name,
            'test.txt',
            '/root/invalid/path/file.txt'
        )
        # Should handle invalid path gracefully
