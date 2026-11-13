"""
Basic storage operations tests
Converted from test_storage.sh
"""

import pytest
import os
import sys
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def s3_credentials():
    """Setup S3 credentials for StorageManager"""
    os.environ['HETZNER_S3_ACCESS_KEY'] = 'test-access-key'
    os.environ['HETZNER_S3_SECRET_KEY'] = 'test-secret-key'
    yield
    os.environ.pop('HETZNER_S3_ACCESS_KEY', None)
    os.environ.pop('HETZNER_S3_SECRET_KEY', None)


class TestBasicStorageOperations:
    """Test basic storage operations"""

    def test_list_buckets_command(self, hetzner_token, s3_credentials):
        """Test list buckets command"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.list_buckets.return_value = {
                'Buckets': [{'Name': 'test-bucket-1'}, {'Name': 'test-bucket-2'}]
            }

            storage = StorageManager()
            buckets = storage.list_buckets()

            assert isinstance(buckets, list)
            assert len(buckets) == 2
            assert 'test-bucket-1' in buckets
            assert 'test-bucket-2' in buckets

    def test_create_bucket_command(self, hetzner_token, test_bucket_name):
        """Test create bucket command"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.create_bucket.return_value = True
            mock_s3.head_bucket.return_value = {}

            storage = StorageManager()
            result = storage.create_bucket(test_bucket_name)

            assert result is True
            mock_s3.create_bucket.assert_called_once_with(Bucket=test_bucket_name)
            mock_s3.head_bucket.assert_called_with(Bucket=test_bucket_name)
            # Hetzner needs path-style + SigV4 on every client (create and upload).
            kwargs = mock_boto.call_args.kwargs
            assert kwargs.get("region_name") == "fsn1"
            assert kwargs["config"].s3.get("addressing_style") == "path"
            assert kwargs["config"].signature_version == "s3v4"

    def test_upload_file_command(self, hetzner_token, s3_credentials, test_bucket_name, test_file):
        """Test upload file command"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.upload_file.return_value = True
            mock_s3.head_object.return_value = {}

            storage = StorageManager()
            result = storage.upload_file(test_bucket_name, test_file, 'test_object.txt')

            assert result is True
            mock_s3.upload_file.assert_called_once()
            mock_s3.head_object.assert_called_with(
                Bucket=test_bucket_name, Key='test_object.txt'
            )

    def test_list_objects_command(self, hetzner_token, s3_credentials, test_bucket_name):
        """Test list objects command"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.list_objects_v2.return_value = {
                'Contents': [
                    {'Key': 'file1.txt', 'Size': 100},
                    {'Key': 'file2.txt', 'Size': 200}
                ]
            }

            storage = StorageManager()
            objects = storage.list_objects(test_bucket_name, '')

            assert isinstance(objects, list)
            assert len(objects) == 2
            assert objects[0] == 'file1.txt'

    def test_download_file_command(self, hetzner_token, s3_credentials, test_bucket_name, tmp_path):
        """Test download file command"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3

            # Create a temporary file to simulate download
            download_path = str(tmp_path / "downloaded.txt")

            # Mock the download_file method
            def mock_download_file(Bucket, Key, Filename):
                with open(Filename, 'w') as f:
                    f.write("Downloaded content")

            mock_s3.download_file = mock_download_file

            storage = StorageManager()
            result = storage.download_file(test_bucket_name, 'test_object.txt', download_path)

            assert os.path.exists(download_path)
            with open(download_path, 'r') as f:
                content = f.read()
            assert content == "Downloaded content"

    def test_delete_object_command(self, hetzner_token, s3_credentials, test_bucket_name):
        """Test delete object command"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.delete_object.return_value = True

            storage = StorageManager()
            result = storage.delete_object(test_bucket_name, 'test_object.txt')

            assert result is True
            mock_s3.delete_object.assert_called_once_with(
                Bucket=test_bucket_name, Key='test_object.txt'
            )

    def test_delete_bucket_command(self, hetzner_token, s3_credentials, test_bucket_name):
        """Test delete bucket command"""
        from cli.storage import StorageManager

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.delete_bucket.return_value = True

            storage = StorageManager()
            result = storage.delete_bucket(test_bucket_name)

            assert result is True
            mock_s3.delete_bucket.assert_called_once_with(Bucket=test_bucket_name)


class TestStorageErrorHandling:
    """Test storage error handling"""

    def test_list_buckets_with_error(self, hetzner_token, s3_credentials):
        """Test list buckets with S3 error"""
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.list_buckets.side_effect = ClientError(
                error_response={'Error': {'Code': 'AccessDenied', 'Message': 'Access Denied'}},
                operation_name='ListBuckets'
            )

            storage = StorageManager()
            buckets = storage.list_buckets()

            # Should return empty list on error
            assert isinstance(buckets, list)

    def test_create_bucket_with_existing(self, hetzner_token, s3_credentials, test_bucket_name):
        """Test create bucket when bucket already exists"""
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        with patch('boto3.client') as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.create_bucket.side_effect = ClientError(
                error_response={'Error': {'Code': 'BucketAlreadyOwnedByYou', 'Message': 'Bucket already exists'}},
                operation_name='CreateBucket'
            )

            storage = StorageManager()
            result = storage.create_bucket(test_bucket_name)

            # Should handle existing bucket gracefully
            assert result is True  # Or handle as appropriate
            mock_s3.head_bucket.assert_called_with(Bucket=test_bucket_name)

    def test_create_bucket_waits_through_eventual_consistency(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        """HeadBucket 404s briefly after CreateBucket before the bucket is ready."""
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        not_ready = ClientError(
            error_response={
                "Error": {"Code": "404", "Message": "Not Found"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="HeadBucket",
        )

        with patch("boto3.client") as mock_boto, patch("cli.storage.time.sleep") as mock_sleep:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.create_bucket.return_value = True
            mock_s3.head_bucket.side_effect = [not_ready, not_ready, {}]

            storage = StorageManager()
            assert storage.create_bucket(test_bucket_name) is True
            assert mock_s3.head_bucket.call_count == 3
            assert mock_sleep.call_count == 2

    def test_create_bucket_retries_location_constraint(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        """Retry CreateBucket once when Hetzner reports LocationConstraintConflict."""
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        conflict = ClientError(
            error_response={
                "Error": {
                    "Code": "InvalidLocationConstraint",
                    "Message": "LocationConstraintConflict for bucket",
                }
            },
            operation_name="CreateBucket",
        )

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.create_bucket.side_effect = [conflict, True]
            mock_s3.head_bucket.return_value = {}

            storage = StorageManager()
            assert storage.create_bucket(test_bucket_name) is True
            assert mock_s3.create_bucket.call_count == 2

    def test_create_bucket_returns_false_when_wait_times_out(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        """create_bucket fails closed if the bucket never becomes visible."""
        from cli.storage import StorageManager

        with patch("boto3.client") as mock_boto, patch(
            "cli.storage.StorageManager._wait_for_bucket", return_value=False
        ):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.create_bucket.return_value = True

            storage = StorageManager()
            assert storage.create_bucket(test_bucket_name) is False

    def test_delete_object_missing_key_is_ok(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.delete_object.side_effect = ClientError(
                error_response={"Error": {"Code": "NoSuchKey", "Message": "gone"}},
                operation_name="DeleteObject",
            )

            storage = StorageManager()
            assert storage.delete_object(test_bucket_name, "missing.txt") is True

    def test_endpoint_region_from_url(self, hetzner_token, s3_credentials):
        from cli.storage import StorageManager

        storage = StorageManager(endpoint_url="https://hel1.your-objectstorage.com")
        assert storage._endpoint_region() == "hel1"
        storage.endpoint_url = "https://nbg1.your-objectstorage.com"
        assert storage._endpoint_region() == "nbg1"
        storage.endpoint_url = "https://objects.example.com"
        assert storage._endpoint_region() == "fsn1"

    def test_init_requires_tokens(self, monkeypatch):
        from cli.storage import StorageManager

        monkeypatch.delenv("HZ_TOKEN", raising=False)
        monkeypatch.delenv("HETZNER_CLOUD_API_TOKEN", raising=False)
        monkeypatch.setenv("HETZNER_S3_ACCESS_KEY", "a")
        monkeypatch.setenv("HETZNER_S3_SECRET_KEY", "s")
        with pytest.raises(ValueError, match="Hetzner Cloud token"):
            StorageManager()

        monkeypatch.setenv("HETZNER_CLOUD_API_TOKEN", "tok")
        monkeypatch.delenv("HETZNER_S3_ACCESS_KEY", raising=False)
        monkeypatch.delenv("HETZNER_S3_SECRET_KEY", raising=False)
        with pytest.raises(ValueError, match="S3 credentials"):
            StorageManager()

    def test_is_not_found_helpers(self, hetzner_token, s3_credentials):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        assert StorageManager._is_not_found(RuntimeError("404 Not Found"))
        assert StorageManager._is_not_found(RuntimeError("NoSuchKey"))
        assert not StorageManager._is_not_found(RuntimeError("AccessDenied"))
        err = ClientError(
            error_response={
                "Error": {"Code": "NoSuchBucket"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="HeadBucket",
        )
        assert StorageManager._is_not_found(err)

    def test_bucket_exists(self, hetzner_token, s3_credentials, test_bucket_name):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        storage = StorageManager()
        client = MagicMock()
        client.head_bucket.return_value = {}

        missing = ClientError(
            error_response={
                "Error": {"Code": "NoSuchBucket"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="HeadBucket",
        )
        forbidden = ClientError(
            error_response={
                "Error": {"Code": "AccessDenied"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            operation_name="HeadBucket",
        )

        hard = ClientError(
            error_response={
                "Error": {"Code": "InternalError"},
                "ResponseMetadata": {"HTTPStatusCode": 500},
            },
            operation_name="HeadBucket",
        )

        with patch.object(storage, "_get_s3_client", return_value=client):
            assert storage.bucket_exists(test_bucket_name) is True
            client.head_bucket.side_effect = missing
            assert storage.bucket_exists(test_bucket_name) is False
            client.head_bucket.side_effect = forbidden
            assert storage.bucket_exists(test_bucket_name) is True
            client.head_bucket.side_effect = hard
            assert storage.bucket_exists(test_bucket_name) is False
            client.head_bucket.side_effect = RuntimeError("network down")
            assert storage.bucket_exists(test_bucket_name) is False

    def test_wait_for_bucket_hard_error_and_timeout(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        storage = StorageManager()
        denied = ClientError(
            error_response={
                "Error": {"Code": "AccessDenied", "Message": "no"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            operation_name="HeadBucket",
        )
        # 403 is treated as "not ready yet" — force timeout via clock.
        client = MagicMock()
        client.head_bucket.side_effect = denied
        ticks = iter([0.0, 0.1, 2.0])

        def fake_time():
            try:
                return next(ticks)
            except StopIteration:
                return 99.0

        with patch("cli.storage.time.sleep"), patch("cli.storage.time.time", side_effect=fake_time):
            assert storage._wait_for_bucket(client, test_bucket_name, timeout_s=1.0) is False

        hard = ClientError(
            error_response={
                "Error": {"Code": "InternalError", "Message": "boom"},
                "ResponseMetadata": {"HTTPStatusCode": 500},
            },
            operation_name="HeadBucket",
        )
        client.head_bucket.side_effect = hard
        assert storage._wait_for_bucket(client, test_bucket_name) is False

        client.head_bucket.side_effect = RuntimeError("network")
        assert storage._wait_for_bucket(client, test_bucket_name) is False

    def test_wait_for_object_paths(self, hetzner_token, s3_credentials, test_bucket_name):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        storage = StorageManager()
        client = MagicMock()
        not_ready = ClientError(
            error_response={
                "Error": {"Code": "404", "Message": "missing"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="HeadObject",
        )
        client.head_object.side_effect = [not_ready, {}]
        with patch("cli.storage.time.sleep"):
            assert storage._wait_for_object(client, test_bucket_name, "k") is True

        client.head_object.side_effect = RuntimeError("AccessDenied")
        assert storage._wait_for_object(client, test_bucket_name, "k") is False

        client.head_object.side_effect = not_ready
        ticks = iter([0.0, 0.1, 2.0])

        def fake_time():
            try:
                return next(ticks)
            except StopIteration:
                return 99.0

        with patch("cli.storage.time.sleep"), patch("cli.storage.time.time", side_effect=fake_time):
            assert storage._wait_for_object(client, test_bucket_name, "k", timeout_s=1) is False

    def test_create_bucket_hard_failure(self, hetzner_token, s3_credentials, test_bucket_name):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.create_bucket.side_effect = ClientError(
                error_response={"Error": {"Code": "AccessDenied", "Message": "no"}},
                operation_name="CreateBucket",
            )
            assert StorageManager().create_bucket(test_bucket_name) is False

    def test_upload_missing_file_and_retries(
        self, hetzner_token, s3_credentials, test_bucket_name, tmp_path
    ):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        storage = StorageManager()
        assert storage.upload_file(test_bucket_name, str(tmp_path / "nope.txt")) is False

        path = tmp_path / "ok.txt"
        path.write_text("hi")
        not_found = ClientError(
            error_response={"Error": {"Code": "NoSuchBucket", "Message": "x"}},
            operation_name="PutObject",
        )
        with patch("boto3.client") as mock_boto, patch("cli.storage.time.sleep"):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.upload_file.side_effect = [not_found, None]
            mock_s3.head_object.return_value = {}
            assert storage.upload_file(test_bucket_name, str(path), "ok.txt") is True

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.upload_file.side_effect = ClientError(
                error_response={"Error": {"Code": "AccessDenied", "Message": "no"}},
                operation_name="PutObject",
            )
            assert storage.upload_file(test_bucket_name, str(path), "ok.txt") is False

        ticks = iter([0.0, 0.1, 100.0])

        def fake_time():
            try:
                return next(ticks)
            except StopIteration:
                return 999.0

        with patch("boto3.client") as mock_boto, patch(
            "cli.storage.StorageManager._wait_for_object", return_value=False
        ), patch("cli.storage.time.sleep"), patch("cli.storage.time.time", side_effect=fake_time):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.upload_file.return_value = None
            assert storage.upload_file(test_bucket_name, str(path), "ok.txt") is False

    def test_download_retries_and_errors(
        self, hetzner_token, s3_credentials, test_bucket_name, tmp_path
    ):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        out = str(tmp_path / "out.txt")
        not_found = ClientError(
            error_response={
                "Error": {"Code": "NoSuchKey", "Message": "gone"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="GetObject",
        )
        with patch("boto3.client") as mock_boto, patch("cli.storage.time.sleep"):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.download_file.side_effect = [not_found, None]
            assert StorageManager().download_file(test_bucket_name, "k", out) is True

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.download_file.side_effect = OSError("disk")
            assert StorageManager().download_file(test_bucket_name, "k", out) is False

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.download_file.side_effect = ClientError(
                error_response={"Error": {"Code": "AccessDenied", "Message": "no"}},
                operation_name="GetObject",
            )
            assert StorageManager().download_file(test_bucket_name, "k", out) is False

        ticks = iter([0.0, 0.1, 100.0])

        def fake_time():
            try:
                return next(ticks)
            except StopIteration:
                return 999.0

        with patch("boto3.client") as mock_boto, patch("cli.storage.time.sleep"), patch(
            "cli.storage.time.time", side_effect=fake_time
        ):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.download_file.side_effect = not_found
            assert StorageManager().download_file(test_bucket_name, "k", out) is False

    def test_list_objects_retries_missing_bucket(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        missing = ClientError(
            error_response={
                "Error": {"Code": "NoSuchBucket", "Message": "gone"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="ListObjectsV2",
        )
        with patch("boto3.client") as mock_boto, patch("cli.storage.time.sleep"):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.list_objects_v2.side_effect = [
                missing,
                {"Contents": [{"Key": "lifecycle.txt"}]},
            ]
            assert StorageManager().list_objects(test_bucket_name) == ["lifecycle.txt"]
            assert mock_s3.list_objects_v2.call_count == 2

    def test_delete_retries_missing_bucket(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        missing = ClientError(
            error_response={
                "Error": {"Code": "NoSuchBucket", "Message": "gone"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="DeleteObject",
        )
        with patch("boto3.client") as mock_boto, patch("cli.storage.time.sleep"):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.delete_object.side_effect = [missing, None]
            assert StorageManager().delete_object(test_bucket_name, "lifecycle.txt") is True
            assert mock_s3.delete_object.call_count == 2

        gone = ClientError(
            error_response={
                "Error": {"Code": "NoSuchBucket", "Message": "gone"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="DeleteBucket",
        )
        with patch("boto3.client") as mock_boto, patch("cli.storage.time.sleep"):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.delete_bucket.side_effect = gone
            mock_s3.head_bucket.side_effect = gone
            assert StorageManager().delete_bucket(test_bucket_name) is True

    def test_list_objects_and_delete_errors(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.list_objects_v2.side_effect = ClientError(
                error_response={"Error": {"Code": "AccessDenied", "Message": "no"}},
                operation_name="ListObjectsV2",
            )
            assert StorageManager().list_objects(test_bucket_name) == []

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.delete_object.side_effect = ClientError(
                error_response={"Error": {"Code": "AccessDenied", "Message": "no"}},
                operation_name="DeleteObject",
            )
            assert StorageManager().delete_object(test_bucket_name, "k") is False

        ticks = iter([0.0, 0.1, 100.0])

        def fake_time():
            try:
                return next(ticks)
            except StopIteration:
                return 999.0

        with patch("boto3.client") as mock_boto, patch("cli.storage.time.sleep"), patch(
            "cli.storage.time.time", side_effect=fake_time
        ):
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.delete_bucket.side_effect = ClientError(
                error_response={"Error": {"Code": "BucketNotEmpty", "Message": "no"}},
                operation_name="DeleteBucket",
            )
            mock_s3.head_bucket.return_value = {}
            assert StorageManager().delete_bucket(test_bucket_name) is False

    def test_credentials_info_short_token(self, hetzner_token, s3_credentials):
        from cli.storage import StorageManager

        storage = StorageManager()
        storage.hetzner_token = "ab"
        info = storage.get_credentials_info()
        assert info["token_used"] == "ab"

    def test_upload_defaults_object_name_to_basename(
        self, hetzner_token, s3_credentials, test_bucket_name, tmp_path
    ):
        from cli.storage import StorageManager

        path = tmp_path / "payload.bin"
        path.write_text("x")
        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.upload_file.return_value = None
            mock_s3.head_object.return_value = {}
            assert StorageManager().upload_file(test_bucket_name, str(path)) is True
            mock_s3.upload_file.assert_called_once_with(
                str(path), test_bucket_name, "payload.bin"
            )

    def test_create_bucket_outer_already_owned_after_raise(
        self, hetzner_token, s3_credentials, test_bucket_name
    ):
        """Outer handler covers already-owned when raised past the inner try."""
        from cli.storage import StorageManager
        from botocore.exceptions import ClientError

        owned = ClientError(
            error_response={
                "Error": {"Code": "BucketAlreadyExists", "Message": "exists"}
            },
            operation_name="CreateBucket",
        )
        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            # First create raises a non-owned code that re-raises; simulate by
            # making the inner path raise owned from LocationConstraint retry.
            mock_s3.create_bucket.side_effect = [
                ClientError(
                    error_response={
                        "Error": {
                            "Code": "InvalidLocationConstraint",
                            "Message": "LocationConstraintConflict",
                        }
                    },
                    operation_name="CreateBucket",
                ),
                owned,
            ]
            mock_s3.head_bucket.return_value = {}
            assert StorageManager().create_bucket(test_bucket_name) is True
