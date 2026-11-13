"""
Storage management for the CLI using Hetzner S3-compatible object storage
"""

import os
import time
from typing import Optional, Dict
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from cli import log


class StorageManager:
    """Manages storage operations with Hetzner S3-compatible storage"""

    def __init__(self, hetzner_token: Optional[str] = None, endpoint_url: Optional[str] = None):
        self.hetzner_token = hetzner_token or os.getenv('HZ_TOKEN') or os.getenv('HETZNER_CLOUD_API_TOKEN')
        self.endpoint_url = endpoint_url or os.getenv('S3_ENDPOINT_URL') or "https://fsn1.your-objectstorage.com"

        # Read S3 credentials from environment variables
        # Hetzner uses HETZNER_S3_ACCESS_KEY and HETZNER_S3_SECRET_KEY
        # Also supports HETZNER_CLOUD_OBJECT_STORAGE_ACCESS_KEY and HETZNER_CLOUD_OBJECT_STORAGE_SECRET_KEY
        self.s3_access_key = os.getenv('HETZNER_S3_ACCESS_KEY') or os.getenv('HETZNER_CLOUD_OBJECT_STORAGE_ACCESS_KEY')
        self.s3_secret_key = os.getenv('HETZNER_S3_SECRET_KEY') or os.getenv('HETZNER_CLOUD_OBJECT_STORAGE_SECRET_KEY')

        if not self.hetzner_token:
            raise ValueError(
                "Hetzner Cloud token missing. Please set HZ_TOKEN or HETZNER_CLOUD_API_TOKEN environment variable")

        if not self.s3_access_key or not self.s3_secret_key:
            raise ValueError(
                "S3 credentials missing. Please set HETZNER_S3_ACCESS_KEY and HETZNER_S3_SECRET_KEY environment variables")

    def _endpoint_region(self) -> str:
        """Location id embedded in the Hetzner Object Storage endpoint hostname."""
        url = (self.endpoint_url or "").lower()
        for region in ("hel1", "fsn1", "nbg1"):
            if region in url:
                return region
        return "fsn1"

    def _s3_config(self) -> Config:
        """
        Hetzner Object Storage requires path-style URLs and SigV4.

        Virtual-hosted style (``bucket.endpoint``) yields BadRequest / NoSuchBucket
        on PutObject even right after a successful CreateBucket.
        """
        return Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            # Object storage can be slow under load; avoid flaky short timeouts.
            connect_timeout=10,
            read_timeout=120,
            retries={"max_attempts": 5, "mode": "standard"},
        )

    def _get_s3_client(self):
        """Get S3 client configured for Hetzner Object Storage."""
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self._endpoint_region(),
            aws_access_key_id=self.s3_access_key,
            aws_secret_access_key=self.s3_secret_key,
            config=self._s3_config(),
        )

    @staticmethod
    def _is_not_found(exc: BaseException) -> bool:
        """True for RGW/S3 not-found responses (eventual consistency)."""
        if not isinstance(exc, ClientError):
            # boto3 transfer manager wraps some 404s.
            msg = str(exc)
            return "404" in msg or "NoSuchKey" in msg or "Not Found" in msg
        code = str(exc.response.get("Error", {}).get("Code", "") or "")
        http = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
        return code in ("404", "NoSuchBucket", "NoSuchKey", "NotFound") or http == 404

    def _wait_for_bucket(self, s3_client, bucket_name: str, timeout_s: float = 30.0) -> bool:
        """Poll until the bucket is visible to HeadBucket (eventual consistency)."""
        deadline = time.time() + timeout_s
        delay = 0.5
        while time.time() < deadline:
            try:
                s3_client.head_bucket(Bucket=bucket_name)
                return True
            except ClientError as exc:
                # 403 can mean the bucket exists but is not yet readable on this gateway.
                http = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
                if self._is_not_found(exc) or http == 403:
                    time.sleep(delay)
                    delay = min(delay * 1.5, 3.0)
                    continue
                log.warning("HeadBucket for %r failed: %s", bucket_name, exc)
                return False
            except Exception as exc:
                log.warning("HeadBucket for %r failed: %s", bucket_name, exc)
                return False
        log.warning("Timed out waiting for bucket %r to be available", bucket_name)
        return False

    def _wait_for_object(
        self, s3_client, bucket_name: str, object_name: str, timeout_s: float = 30.0
    ) -> bool:
        """Poll until the object is visible to HeadObject (Hetzner RGW lag)."""
        deadline = time.time() + timeout_s
        delay = 0.2
        while time.time() < deadline:
            try:
                s3_client.head_object(Bucket=bucket_name, Key=object_name)
                return True
            except Exception as exc:
                if self._is_not_found(exc):
                    time.sleep(delay)
                    delay = min(delay * 1.5, 2.0)
                    continue
                log.warning(
                    "HeadObject for %r/%r failed: %s", bucket_name, object_name, exc
                )
                return False
        log.warning(
            "Timed out waiting for object %r/%r to be available",
            bucket_name,
            object_name,
        )
        return False

    def bucket_exists(self, bucket_name: str) -> bool:
        """Return True if HeadBucket succeeds (missing bucket is False)."""
        s3_client = self._get_s3_client()
        try:
            s3_client.head_bucket(Bucket=bucket_name)
            return True
        except ClientError as exc:
            http = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
            if self._is_not_found(exc):
                return False
            # Gateway may return 403 while the bucket is still propagating.
            if http == 403:
                return True
            log.debug("HeadBucket for %r failed: %s", bucket_name, exc)
            return False
        except Exception as exc:
            log.debug("HeadBucket for %r failed: %s", bucket_name, exc)
            return False

    def create_bucket(self, bucket_name: str, location: Optional[str] = None) -> bool:
        """Create a new bucket using Hetzner Object Storage"""
        try:
            s3_client = self._get_s3_client()
            try:
                s3_client.create_bucket(Bucket=bucket_name)
                log.debug("Created storage bucket %r", bucket_name)
            except ClientError as create_error:
                error_code = create_error.response.get("Error", {}).get("Code", "")
                # If bucket already exists, that's fine
                if error_code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    log.debug("Storage bucket %r already exists", bucket_name)
                elif "LocationConstraintConflict" in str(create_error):
                    # Retry once with the same client (path-style + region already set).
                    s3_client.create_bucket(Bucket=bucket_name)
                    log.debug(
                        "Created storage bucket %r (region %s)",
                        bucket_name,
                        self._endpoint_region(),
                    )
                else:
                    raise

            if not self._wait_for_bucket(s3_client, bucket_name):
                return False
            return True

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')

            # If bucket already exists, that's fine
            if error_code == 'BucketAlreadyOwnedByYou' or error_code == 'BucketAlreadyExists':
                log.debug("Storage bucket %r already exists", bucket_name)
                s3_client = self._get_s3_client()
                return self._wait_for_bucket(s3_client, bucket_name)

            log.warning("Could not create storage bucket: %s", e)
            return False

    def list_buckets(self) -> list:
        """List all buckets"""
        try:
            s3_client = self._get_s3_client()
            response = s3_client.list_buckets()
            buckets = [bucket['Name'] for bucket in response['Buckets']]
            log.debug("Storage buckets: %s", buckets)
            return buckets
        except ClientError as e:
            log.warning("Could not list storage buckets: %s", e)
            return []

    def upload_file(self, bucket_name: str, file_path: str, object_name: Optional[str] = None) -> bool:
        """Upload a file to storage"""
        if not os.path.exists(file_path):
            log.warning("Local file not found for upload: %s", file_path)
            return False

        if object_name is None:
            object_name = os.path.basename(file_path)

        s3_client = self._get_s3_client()
        deadline = time.time() + 30.0
        delay = 0.2
        last_error: Optional[BaseException] = None

        while time.time() < deadline:
            try:
                s3_client.upload_file(file_path, bucket_name, object_name)
                # Hetzner Object Storage can 404 Head/Get for a short window after Put.
                if not self._wait_for_object(s3_client, bucket_name, object_name):
                    last_error = RuntimeError("object not visible after upload")
                    time.sleep(delay)
                    delay = min(delay * 1.5, 2.0)
                    continue
                log.debug("Uploaded %s -> %s/%s", file_path, bucket_name, object_name)
                return True
            except Exception as e:
                last_error = e
                # RGW gateways briefly return NoSuchBucket even for buckets we just used.
                if self._is_not_found(e) or "NoSuchBucket" in str(e):
                    time.sleep(delay)
                    delay = min(delay * 1.5, 2.0)
                    continue
                log.warning("Could not upload file: %s", e)
                return False

        log.warning("Could not upload file after retries: %s", last_error)
        return False

    def download_file(self, bucket_name: str, object_name: str, file_path: str) -> bool:
        """Download a file from storage"""
        s3_client = self._get_s3_client()
        deadline = time.time() + 30.0
        delay = 0.2
        last_error: Optional[BaseException] = None
        while time.time() < deadline:
            try:
                s3_client.download_file(bucket_name, object_name, file_path)
                log.debug("Downloaded %s/%s -> %s", bucket_name, object_name, file_path)
                return True
            except OSError as e:
                log.debug("Could not download file: %s", e)
                return False
            except Exception as e:
                last_error = e
                if self._is_not_found(e):
                    time.sleep(delay)
                    delay = min(delay * 1.5, 2.0)
                    continue
                log.debug("Could not download file: %s", e)
                return False
        log.debug("Could not download file after retries: %s", last_error)
        return False

    def list_objects(self, bucket_name: str, prefix: str = "") -> list:
        """List objects in a bucket.

        Hetzner Object Storage can return NoSuchBucket for a short window
        after a successful upload, so that response is retried.
        """
        s3_client = self._get_s3_client()
        deadline = time.time() + 30.0
        delay = 0.2
        last_error: Optional[BaseException] = None
        while time.time() < deadline:
            try:
                response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
                objects = []
                if 'Contents' in response:
                    objects = [obj['Key'] for obj in response['Contents']]
                log.debug(
                    "Storage objects in %r (prefix %r): %s",
                    bucket_name,
                    prefix,
                    objects,
                )
                return objects
            except ClientError as e:
                last_error = e
                if self._is_not_found(e):
                    time.sleep(delay)
                    delay = min(delay * 1.5, 2.0)
                    continue
                log.warning("Could not list storage objects: %s", e)
                return []
        log.warning("Could not list storage objects: %s", last_error)
        return []

    def delete_object(self, bucket_name: str, object_name: str) -> bool:
        """Delete an object from storage.

        A missing key is success. NoSuchBucket is retried because the gateway
        can report it while the bucket is still there.
        """
        s3_client = self._get_s3_client()
        deadline = time.time() + 30.0
        delay = 0.2
        last_error: Optional[BaseException] = None
        while time.time() < deadline:
            try:
                s3_client.delete_object(Bucket=bucket_name, Key=object_name)
                log.debug("Deleted %s/%s", bucket_name, object_name)
                return True
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code in ("NoSuchKey", "404", "NotFound"):
                    return True
                last_error = e
                if error_code == "NoSuchBucket" or self._is_not_found(e):
                    time.sleep(delay)
                    delay = min(delay * 1.5, 2.0)
                    continue
                log.warning("Could not delete storage object: %s", e)
                return False
        log.warning("Could not delete storage object: %s", last_error)
        return False

    def delete_bucket(self, bucket_name: str) -> bool:
        """Delete a bucket. A bucket that is already gone counts as deleted."""
        s3_client = self._get_s3_client()
        deadline = time.time() + 30.0
        delay = 0.2
        last_error: Optional[BaseException] = None
        while time.time() < deadline:
            try:
                s3_client.delete_bucket(Bucket=bucket_name)
                log.debug("Deleted storage bucket %r", bucket_name)
                return True
            except ClientError as e:
                last_error = e
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code in ("BucketNotEmpty", "NoSuchBucket") or self._is_not_found(e):
                    try:
                        s3_client.head_bucket(Bucket=bucket_name)
                    except ClientError as head_exc:
                        if self._is_not_found(head_exc):
                            log.debug("Storage bucket %r is already gone", bucket_name)
                            return True
                    time.sleep(delay)
                    delay = min(delay * 1.5, 2.0)
                    continue
                log.warning("Could not delete storage bucket: %s", e)
                return False
        log.warning("Could not delete storage bucket: %s", last_error)
        return False

    def get_credentials_info(self) -> Dict[str, str]:
        """Return information about the configured credentials (token masked)."""
        token_part = self.hetzner_token or ""
        if len(token_part) > 4:
            masked = f"{'*' * (len(token_part) - 4)}{token_part[-4:]}"
        else:
            masked = token_part
        return {
            "endpoint": self.endpoint_url or "",
            "token_used": masked,
            "credentials_expiration": "Temporary credentials generated by Hetzner",
        }
