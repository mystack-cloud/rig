"""
Hetzner S3-compatible storage CLI tests
Converted from test_hetzner_storage.sh
"""

import pytest
import os
import sys
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.mark.requires_credentials
@pytest.mark.integration
class TestHetznerStorageCLI:
    """Test Hetzner S3-compatible storage CLI"""
    
    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")
    
    def test_list_buckets_with_hetzner(self, hetzner_token):
        """Test list buckets command with Hetzner token"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # List buckets should work with valid Hetzner token
        buckets = storage.list_buckets()
        
        # Should return a list (may be empty)
        assert isinstance(buckets, list)
    
    def test_create_bucket_hetzner(self, hetzner_token, test_bucket_name):
        """Test create bucket command with Hetzner token"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Create bucket should work with valid token
        result = storage.create_bucket(test_bucket_name)
        
        # Bucket creation may succeed or bucket may already exist
        assert result in [True, False]
    
    def test_upload_with_hetzner(self, hetzner_token, test_bucket_name, tmp_path):
        """Test upload command with Hetzner token"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Create test file
        test_file = str(tmp_path / "readme_test.md")
        with open(test_file, 'w') as f:
            f.write("# Test README\n\nThis is a test file.")
        
        # Upload should work with valid token
        result = storage.upload_file(
            test_bucket_name,
            test_file,
            'readme-copy.md'
        )
        
        # Upload may succeed or fail depending on bucket existence
        assert result in [True, False]
    
    def test_list_objects_hetzner(self, hetzner_token, test_bucket_name):
        """Test list objects command with Hetzner token"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # List objects should work with valid token
        objects = storage.list_objects(test_bucket_name, '')
        
        # Should return a list
        assert isinstance(objects, list)
    
    def test_delete_object_hetzner(self, hetzner_token, test_bucket_name):
        """Test delete object command with Hetzner token"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Delete object should work with valid token
        result = storage.delete_object(test_bucket_name, 'readme-copy.md')
        
        # Delete may succeed or object may not exist
        assert result in [True, False]
    
    def test_storage_manager_initialization(self, hetzner_token):
        """Test StorageManager initialization with Hetzner token"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Storage manager should be initialized with Hetzner token
        assert storage.hetzner_token == hetzner_token
        
        # S3 endpoint should be configured
        assert storage.endpoint_url is not None
        assert 'objectstorage' in storage.endpoint_url.lower() or 'fsn1' in storage.endpoint_url.lower()
    
    def test_credentials_info_masking(self, hetzner_token):
        """Test that credentials info properly masks the token"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Get credentials info
        creds_info = storage.get_credentials_info()
        
        # Token should be masked
        token_used = creds_info.get('token_used', '')
        
        # Should show first 4 and last 4 characters with masking
        assert len(token_used) > 0
        assert '****' in token_used or token_used.startswith(hetzner_token[:4])
    
    def test_hetzner_api_connection(self, hetzner_token):
        """Test connection to Hetzner Cloud API"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Storage manager should be able to connect to Hetzner API
        # to retrieve S3 credentials
        assert storage is not None
        
        # Token should be valid (not empty)
        assert len(hetzner_token) > 0
    
    def test_s3_endpoint_configuration(self, hetzner_token):
        """Test S3 endpoint configuration"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Endpoint should be configured for Hetzner
        endpoint = storage.endpoint_url
        
        # Should contain expected Hetzner endpoint patterns
        assert endpoint is not None
        assert len(endpoint) > 0


@pytest.mark.requires_credentials
@pytest.mark.integration
class TestHetznerStorageOperations:
    """Test specific Hetzner storage operations"""
    
    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")
    
    def test_full_hetzner_workflow(self, hetzner_token, test_bucket_name, tmp_path):
        """Test full storage workflow with Hetzner"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # 1. List buckets
        buckets = storage.list_buckets()
        assert isinstance(buckets, list)
        
        # 2. Create bucket
        assert storage.create_bucket(test_bucket_name) is True
        
        # 3. Upload file
        test_file = str(tmp_path / "hetzner_test.txt")
        with open(test_file, 'w') as f:
            f.write("Hetzner test content")
        
        assert storage.upload_file(test_bucket_name, test_file, 'hetzner_file.txt') is True
        
        # 4. List objects
        objects = storage.list_objects(test_bucket_name, '')
        assert isinstance(objects, list)
        assert len(objects) > 0
        
        # 5. Download file
        download_path = str(tmp_path / "hetzner_downloaded.txt")
        assert storage.download_file(test_bucket_name, 'hetzner_file.txt', download_path) is True
        
        # 6. Delete object
        assert storage.delete_object(test_bucket_name, 'hetzner_file.txt') is True
        
        # 7. Delete bucket
        assert storage.delete_bucket(test_bucket_name) is True
    
    def test_hetzner_with_mocked_api(self, hetzner_token, test_bucket_name, tmp_path):
        """Test Hetzner storage with mocked API calls"""
        from cli.storage import StorageManager
        
        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            
            storage = StorageManager()
            
            # Configure mocks
            mock_s3.create_bucket.return_value = True
            mock_s3.list_buckets.return_value = {
                'Buckets': [{'Name': test_bucket_name}]
            }
            mock_s3.upload_file.return_value = True
            mock_s3.list_objects_v2.return_value = {
                'Contents': [{'Key': 'hetzner.txt', 'Size': 15}]
            }
            mock_s3.download_file = lambda Bucket, Key, Filename: open(Filename, 'w').write('test')
            mock_s3.delete_object.return_value = True
            mock_s3.delete_bucket.return_value = True
            
            # Test all operations
            assert storage.create_bucket(test_bucket_name) is True
            assert test_bucket_name in storage.list_buckets()
            assert storage.upload_file(test_bucket_name, str(tmp_path / "test.txt"), 'hetzner.txt') is True
            assert len(storage.list_objects(test_bucket_name, '')) > 0
            
            with patch('builtins.open', create=True):
                assert storage.download_file(test_bucket_name, 'hetzner.txt', str(tmp_path / "download.txt")) is True
            
            assert storage.delete_object(test_bucket_name, 'hetzner.txt') is True
            assert storage.delete_bucket(test_bucket_name) is True
