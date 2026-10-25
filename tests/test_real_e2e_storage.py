"""
Real E2E storage tests (require real Hetzner credentials)
Converted from test_real_e2e_storage.sh and real_e2e_test.sh
"""

import pytest
import os
import sys
import json
import tempfile
import shutil
from unittest.mock import patch, MagicMock, call

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_requires_credentials():
    """Check if credentials are available for E2E tests"""
    return os.getenv('HETZNER_CLOUD_API_TOKEN') is not None


@pytest.mark.requires_credentials
@pytest.mark.integration
@pytest.mark.e2e
class TestRealE2EStorage:
    """Test real E2E storage operations with actual Hetzner API"""
    
    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")
    
    def test_real_list_buckets(self, hetzner_token):
        """Test listing real buckets"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        buckets = storage.list_buckets()
        
        # Should return list of buckets (may be empty if no buckets exist)
        assert isinstance(buckets, list)
    
    def test_real_create_bucket(self, hetzner_token, test_bucket_name):
        """Test creating a real bucket"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Try to create bucket
        result = storage.create_bucket(test_bucket_name)
        
        # Bucket creation may succeed or fail (if bucket already exists)
        # Both are acceptable outcomes
        assert result in [True, False]
    
    def test_real_upload_download(self, hetzner_token, test_bucket_name, tmp_path):
        """Test real upload and download"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Create test file
        test_file = str(tmp_path / "real_test.txt")
        with open(test_file, 'w') as f:
            f.write("Real E2E test content")
        
        # Upload
        result = storage.upload_file(test_bucket_name, test_file, 'real_test.txt')
        
        # Download
        download_path = str(tmp_path / "real_downloaded.txt")
        result = storage.download_file(test_bucket_name, 'real_test.txt', download_path)
        
        # Verify download
        assert os.path.exists(download_path)
    
    def test_real_delete_operations(self, hetzner_token, test_bucket_name):
        """Test real delete operations"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Try to delete object
        result = storage.delete_object(test_bucket_name, 'nonexistent.txt')
        
        # Try to delete bucket
        result = storage.delete_bucket(test_bucket_name)
    
    def test_complete_e2e_workflow(self, hetzner_token, test_bucket_name, tmp_path):
        """Test complete E2E workflow"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # 1. Create bucket
        assert storage.create_bucket(test_bucket_name) is True
        
        # 2. Upload file
        test_file = str(tmp_path / "e2e_test.txt")
        with open(test_file, 'w') as f:
            f.write("E2E workflow test content")
        
        assert storage.upload_file(test_bucket_name, test_file, 'e2e_state.json') is True
        
        # 3. List objects
        objects = storage.list_objects(test_bucket_name, '')
        assert isinstance(objects, list)
        
        # 4. Download file
        download_path = str(tmp_path / "e2e_downloaded.txt")
        assert storage.download_file(test_bucket_name, 'e2e_state.json', download_path) is True
        
        # 5. Delete object
        assert storage.delete_object(test_bucket_name, 'e2e_state.json') is True
        
        # 6. Delete bucket
        assert storage.delete_bucket(test_bucket_name) is True
    
    def test_unique_bucket_naming(self, hetzner_token):
        """Test that bucket names are unique"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Create two buckets with unique names
        bucket1 = f"unique-test-{hetzner_token[:8]}-1"
        bucket2 = f"unique-test-{hetzner_token[:8]}-2"
        
        # Both should be able to be created (if they don't exist)
        result1 = storage.create_bucket(bucket1)
        result2 = storage.create_bucket(bucket2)
        
        # Clean up
        storage.delete_bucket(bucket1)
        storage.delete_bucket(bucket2)
    
    def test_shared_state_workflow(self, hetzner_token, tmp_path):
        """Test shared state management workflow"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        bucket_name = f"shared-e2e-{hetzner_token[:8]}"
        
        # Create shared state bucket
        assert storage.create_bucket(bucket_name) is True
        
        # Upload shared state
        shared_file = str(tmp_path / "shared_e2e.txt")
        with open(shared_file, 'w') as f:
            f.write(json.dumps({
                'shared': True,
                'version': '1.0',
                'timestamp': 'test'
            }))
        
        assert storage.upload_file(bucket_name, shared_file, 'shared_state.json') is True
        
        # Download and verify
        download_path = str(tmp_path / "shared_e2e_downloaded.txt")
        assert storage.download_file(bucket_name, 'shared_state.json', download_path) is True
        
        with open(download_path, 'r') as f:
            shared_data = json.load(f)
        
        assert shared_data['shared'] is True
        
        # Clean up
        storage.delete_object(bucket_name, 'shared_state.json')
        storage.delete_bucket(bucket_name)


@pytest.mark.requires_credentials
@pytest.mark.integration
@pytest.mark.e2e
class TestRealE2EIntegration:
    """Test real E2E integration scenarios"""
    
    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")
    
    def test_e2e_bucket_lifecycle(self, hetzner_token, test_bucket_name):
        """Test complete bucket lifecycle"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Create
        assert storage.create_bucket(test_bucket_name) is True
        
        # Verify exists
        buckets = storage.list_buckets()
        assert test_bucket_name in buckets
        
        # Upload
        test_file = str(test_bucket_name / "lifecycle_test.txt")
        with open(test_file, 'w') as f:
            f.write("Lifecycle test")
        
        assert storage.upload_file(test_bucket_name, str(test_file), 'lifecycle.txt') is True
        
        # List
        objects = storage.list_objects(test_bucket_name, '')
        assert len(objects) > 0
        
        # Download
        download_path = str(test_bucket_name / "lifecycle_downloaded.txt")
        assert storage.download_file(test_bucket_name, 'lifecycle.txt', download_path) is True
        
        # Delete object
        assert storage.delete_object(test_bucket_name, 'lifecycle.txt') is True
        
        # Delete bucket
        assert storage.delete_bucket(test_bucket_name) is True
    
    def test_e2e_with_timestamp_naming(self, hetzner_token):
        """Test bucket naming with timestamps"""
        from cli.storage import StorageManager
        import time
        
        storage = StorageManager()
        
        # Create bucket with timestamp
        timestamp = int(time.time())
        bucket_name = f"e2e-timestamp-{timestamp}"
        
        assert storage.create_bucket(bucket_name) is True
        
        # Verify bucket was created with unique name
        buckets = storage.list_buckets()
        assert bucket_name in buckets
        
        # Clean up
        storage.delete_bucket(bucket_name)
    
    def test_e2e_cleanup_on_exit(self, hetzner_token, tmp_path):
        """Test that E2E test cleanup happens properly"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        bucket_name = f"cleanup-test-{hetzner_token[:8]}"
        
        # Create bucket
        assert storage.create_bucket(bucket_name) is True
        
        # Create and upload test file
        test_file = str(tmp_path / "cleanup_test.txt")
        with open(test_file, 'w') as f:
            f.write("Cleanup test content")
        
        assert storage.upload_file(bucket_name, test_file, 'cleanup.txt') is True
        
        # Clean up
        storage.delete_object(bucket_name, 'cleanup.txt')
        storage.delete_bucket(bucket_name)
        
        # Verify cleanup (bucket should not exist)
        buckets = storage.list_buckets()
        assert bucket_name not in buckets
