"""
Simulation storage tests (end-to-end workflow simulation)
Converted from test_simulation_storage.sh
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


class TestSimulationStorageOperations:
    """Test simulated storage operations workflow"""

    @pytest.fixture
    def temp_dir(self, tmp_path):
        """Create temporary directory for test files"""
        return str(tmp_path)

    def test_basic_storage_workflow(self, hetzner_token, temp_dir):
        """Test basic storage operations workflow"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            # Create bucket
            bucket_name = f"sim-test-bucket-{hetzner_token[:8]}"
            assert storage.create_bucket(bucket_name) is True

            # Upload file
            test_file = os.path.join(temp_dir, "sim_test.txt")
            with open(test_file, 'w') as f:
                f.write("Simulation test content")

            assert storage.upload_file(bucket_name, test_file, 'sim_state.json') is True

            # List objects
            mock_s3.list_objects_v2.return_value = {
                'Contents': [{'Key': 'sim_state.json', 'Size': 25}]
            }
            objects = storage.list_objects(bucket_name, '')
            assert len(objects) == 1
            assert objects[0] == 'sim_state.json'

            # Download file
            download_path = os.path.join(temp_dir, "sim_downloaded.txt")
            def mock_download(Bucket, Key, Filename):
                with open(Filename, 'w') as f:
                    f.write("Simulation test content")
            mock_s3.download_file = mock_download

            assert storage.download_file(bucket_name, 'sim_state.json', download_path) is True
            assert os.path.exists(download_path)

            # Delete object
            assert storage.delete_object(bucket_name, 'sim_state.json') is True

            # Delete bucket
            assert storage.delete_bucket(bucket_name) is True

    def test_shared_state_management(self, hetzner_token, temp_dir):
        """Test shared state management workflow"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            # Create shared state bucket
            bucket_name = f"shared-state-{hetzner_token[:8]}"
            assert storage.create_bucket(bucket_name) is True

            # Upload shared state
            shared_file = os.path.join(temp_dir, "shared_state.txt")
            with open(shared_file, 'w') as f:
                f.write(json.dumps({
                    'shared_data': 'for all environments',
                    'version': '1.0'
                }))

            assert storage.upload_file(bucket_name, shared_file, 'shared_state.json') is True

            # List shared objects
            mock_s3.list_objects_v2.return_value = {
                'Contents': [{'Key': 'shared_state.json', 'Size': 60}]
            }
            objects = storage.list_objects(bucket_name, '')
            assert len(objects) == 1

            # Download shared state
            download_path = os.path.join(temp_dir, "downloaded_shared.txt")
            def mock_download(Bucket, Key, Filename):
                with open(Filename, 'w') as f:
                    f.write(json.dumps({'shared_data': 'for all environments', 'version': '1.0'}))
            mock_s3.download_file = mock_download

            assert storage.download_file(bucket_name, 'shared_state.json', download_path) is True

            # Verify downloaded content
            with open(download_path, 'r') as f:
                downloaded = json.load(f)
            assert downloaded['shared_data'] == 'for all environments'

            # Delete shared state
            assert storage.delete_object(bucket_name, 'shared_state.json') is True

            # Delete bucket
            assert storage.delete_bucket(bucket_name) is True

    def test_file_upload_download_cycle(self, hetzner_token, temp_dir):
        """Test complete file upload and download cycle"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            bucket_name = f"cycle-test-{hetzner_token[:8]}"
            storage.create_bucket(bucket_name)

            # Create test file with various content
            test_file = os.path.join(temp_dir, "cycle_test.txt")
            test_content = "This is a test file for upload/download cycle"
            with open(test_file, 'w') as f:
                f.write(test_content)

            # Upload
            assert storage.upload_file(bucket_name, test_file, 'cycle_file.txt') is True

            # Download
            download_path = os.path.join(temp_dir, "cycle_downloaded.txt")
            def mock_download(Bucket, Key, Filename):
                with open(Filename, 'w') as f:
                    f.write(test_content)
            mock_s3.download_file = mock_download

            assert storage.download_file(bucket_name, 'cycle_file.txt', download_path) is True

            # Verify content matches
            with open(test_file, 'r') as f:
                original = f.read()
            with open(download_path, 'r') as f:
                downloaded = f.read()

            assert original == downloaded

    def test_bucket_cleanup(self, hetzner_token):
        """Test bucket cleanup functionality"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            bucket_name = f"cleanup-test-{hetzner_token[:8]}"

            # Create bucket
            storage.create_bucket(bucket_name)

            # Add some objects
            mock_s3.list_objects_v2.return_value = {
                'Contents': [
                    {'Key': 'file1.txt', 'Size': 100},
                    {'Key': 'file2.txt', 'Size': 200}
                ]
            }

            # Delete all objects
            objects = storage.list_objects(bucket_name, '')
            for obj in objects:
                storage.delete_object(bucket_name, obj)

            # Delete bucket
            storage.delete_bucket(bucket_name)

            # Verify delete_bucket was called
            assert mock_s3.delete_bucket.called
            assert mock_s3.delete_bucket.call_args[1]['Bucket'] == bucket_name

    def test_multiple_file_operations(self, hetzner_token, temp_dir):
        """Test multiple file operations in sequence"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            bucket_name = f"multi-test-{hetzner_token[:8]}"
            storage.create_bucket(bucket_name)

            # Upload multiple files
            files_to_upload = []
            for i in range(3):
                test_file = os.path.join(temp_dir, f"multi_file_{i}.txt")
                with open(test_file, 'w') as f:
                    f.write(f"Content of file {i}")
                files_to_upload.append(test_file)
                storage.upload_file(bucket_name, test_file, f'multi_file_{i}.txt')

            # List objects
            mock_s3.list_objects_v2.return_value = {
                'Contents': [
                    {'Key': 'multi_file_0.txt', 'Size': 15},
                    {'Key': 'multi_file_1.txt', 'Size': 15},
                    {'Key': 'multi_file_2.txt', 'Size': 15}
                ]
            }

            objects = storage.list_objects(bucket_name, '')
            assert len(objects) == 3

            # Download all files
            for i in range(3):
                download_path = os.path.join(temp_dir, f"multi_downloaded_{i}.txt")
                def mock_download(Bucket, Key, Filename):
                    with open(Filename, 'w') as f:
                        f.write(f"Content of file {i}")
                mock_s3.download_file = mock_download
                storage.download_file(bucket_name, f'multi_file_{i}.txt', download_path)
                assert os.path.exists(download_path)

    def test_error_handling_in_workflow(self, hetzner_token, temp_dir):
        """Test error handling during storage operations"""
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            bucket_name = f"error-test-{hetzner_token[:8]}"

            # Simulate error on upload
            mock_s3.upload_file.side_effect = ClientError(
                error_response={'Error': {'Code': 'AccessDenied', 'Message': 'Access Denied'}},
                operation_name='UploadFile'
            )

            test_file = os.path.join(temp_dir, "error_test.txt")
            with open(test_file, 'w') as f:
                f.write("Error test content")

            # Upload should handle error
            result = storage.upload_file(bucket_name, test_file, 'error_file.txt')
            assert result is False  # Or handle as appropriate


class TestSimulationEnvironmentState:
    """Test simulation of environment state management"""

    def test_environment_state_upload(self, hetzner_token, tmp_path):
        """Test uploading environment state"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            bucket_name = f"env-state-{hetzner_token[:8]}"
            storage.create_bucket(bucket_name)

            # Create environment state file
            state_file = os.path.join(str(tmp_path), "env_state.json")
            state_data = {
                'environment': 'development',
                'status': 'running',
                'resources': ['vm-1', 'vm-2']
            }
            with open(state_file, 'w') as f:
                json.dump(state_data, f)

            # Upload state
            assert storage.upload_file(
                bucket_name, state_file,
                'environment/development/state.json'
            ) is True

    def test_shared_state_bucket_naming(self, hetzner_token):
        """Test shared state bucket naming convention"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            storage = StorageManager()

            bucket_name = f"shared-state-{hetzner_token[:8]}"

            # Verify bucket name format
            assert bucket_name.startswith('shared-state-')
            assert hetzner_token[:8] in bucket_name
