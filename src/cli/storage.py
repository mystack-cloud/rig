"""
Storage management for the CLI using Hetzner S3-compatible object storage
"""

import os
import json
from typing import Optional, Dict, List
import boto3
from botocore.exceptions import ClientError


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

    def _get_s3_client(self):
        """Get S3 client configured for Hetzner Object Storage"""
        # Use the pre-generated S3 credentials from environment variables
        # Hetzner S3 uses HETZNER_S3_ACCESS_KEY and HETZNER_S3_SECRET_KEY

        # Hetzner Object Storage is region-agnostic for clients
        # but the endpoint URL determines the physical location
        s3_client = boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.s3_access_key,
            aws_secret_access_key=self.s3_secret_key
        )
        return s3_client

    def create_bucket(self, bucket_name: str, location: Optional[str] = None) -> bool:
        """Create a new bucket using Hetzner Object Storage"""
        try:
            import requests
            import time
            import hashlib
            import hmac

            # For Hetzner S3, we need to use a direct HTTP PUT request
            # to avoid boto3's automatic location constraint
            # The endpoint URL determines the region

            # Hetzner S3 requires the bucket to be created in the same region as the endpoint
            # Extract region from endpoint (fsn1, hel1, nbg1)
            endpoint_region = None
            if 'hel1' in self.endpoint_url:
                endpoint_region = 'hel1'
            elif 'fsn1' in self.endpoint_url:
                endpoint_region = 'fsn1'
            elif 'nbg1' in self.endpoint_url:
                endpoint_region = 'nbg1'

            # Use requests for direct control over the request
            url = f"{self.endpoint_url}/{bucket_name}"

            # Generate signature for AWS S3 v4
            # For bucket creation, we need to sign an empty PUT request
            request_timestamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
            canonical_uri = f"/{bucket_name}"
            canonical_headers = f"host:{self.endpoint_url.replace('https://', '')}\n"
            signed_headers = "host"
            payload_hash = hashlib.sha256(b'').hexdigest()

            canonical_request = f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

            # For Hetzner, we need to use the endpoint region for bucket creation
            # but the client doesn't need region for operations
            s3_client = boto3.client(
                's3',
                endpoint_url=self.endpoint_url,
                region_name=endpoint_region,
                aws_access_key_id=self.s3_access_key,
                aws_secret_access_key=self.s3_secret_key
            )

            # Try creating without explicit LocationConstraint first
            # If this fails with LocationConstraintConflict, the issue is that
            # boto3's session has a default region that doesn't match the endpoint
            try:
                s3_client.create_bucket(Bucket=bucket_name)
                print(f"Bucket '{bucket_name}' created successfully")
                # Wait a moment for bucket to be fully created (Hetzner S3 can have slight delay)
                time.sleep(1)
                return True
            except ClientError as create_error:
                # If the error is about location constraint, try again with explicit region
                error_msg = str(create_error)
                if 'LocationConstraintConflict' in error_msg:
                    # The endpoint region is correct, but boto3 is using a different region
                    # We need to explicitly specify the region in the request
                    s3_client = boto3.client(
                        's3',
                        endpoint_url=self.endpoint_url,
                        region_name=endpoint_region,
                        aws_access_key_id=self.s3_access_key,
                        aws_secret_access_key=self.s3_secret_key,
                        config=boto3.session.Config(signature_version='s3v4')
                    )
                    s3_client.create_bucket(Bucket=bucket_name)
                    print(f"Bucket '{bucket_name}' created successfully (with region {endpoint_region})")
                    # Wait a moment for bucket to be fully created
                    time.sleep(1)
                    return True
                raise

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')

            # If bucket already exists, that's fine
            if error_code == 'BucketAlreadyOwnedByYou' or error_code == 'BucketAlreadyExists':
                print(f"Bucket '{bucket_name}' already exists")
                return True

            print(f"Error creating bucket: {e}")
            return False

    def list_buckets(self) -> list:
        """List all buckets"""
        try:
            s3_client = self._get_s3_client()
            response = s3_client.list_buckets()
            buckets = [bucket['Name'] for bucket in response['Buckets']]
            print("Buckets:")
            for bucket in buckets:
                print(f"  - {bucket}")
            return buckets
        except ClientError as e:
            print(f"Error listing buckets: {e}")
            return []

    def upload_file(self, bucket_name: str, file_path: str, object_name: Optional[str] = None) -> bool:
        """Upload a file to storage"""
        try:
            if not os.path.exists(file_path):
                print(f"Error: File '{file_path}' not found")
                return False

            s3_client = self._get_s3_client()

            if object_name is None:
                object_name = os.path.basename(file_path)

            s3_client.upload_file(file_path, bucket_name, object_name)
            print(f"File '{file_path}' uploaded to bucket '{bucket_name}' as '{object_name}'")
            return True
        except ClientError as e:
            print(f"Error uploading file: {e}")
            return False

    def download_file(self, bucket_name: str, object_name: str, file_path: str) -> bool:
        """Download a file from storage"""
        try:
            s3_client = self._get_s3_client()
            s3_client.download_file(bucket_name, object_name, file_path)
            print(f"File '{object_name}' downloaded from bucket '{bucket_name}' to '{file_path}'")
            return True
        except (ClientError, OSError) as e:
            print(f"Error downloading file: {e}")
            return False

    def list_objects(self, bucket_name: str, prefix: str = "") -> list:
        """List objects in a bucket"""
        try:
            s3_client = self._get_s3_client()
            response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
            objects = []
            if 'Contents' in response:
                objects = [obj['Key'] for obj in response['Contents']]
            print("Objects:")
            for obj in objects:
                print(f"  - {obj}")
            return objects
        except ClientError as e:
            print(f"Error listing objects: {e}")
            return []

    def delete_object(self, bucket_name: str, object_name: str) -> bool:
        """Delete an object from storage"""
        try:
            s3_client = self._get_s3_client()
            s3_client.delete_object(Bucket=bucket_name, Key=object_name)
            print(f"Object '{object_name}' deleted from bucket '{bucket_name}'")
            return True
        except ClientError as e:
            print(f"Error deleting object: {e}")
            return False

    def delete_bucket(self, bucket_name: str) -> bool:
        """Delete a bucket"""
        try:
            s3_client = self._get_s3_client()
            s3_client.delete_bucket(Bucket=bucket_name)
            print(f"Bucket '{bucket_name}' deleted successfully")
            return True
        except ClientError as e:
            print(f"Error deleting bucket: {e}")
            return False

    def get_credentials_info(self) -> Dict[str, str]:
        """Return information about the configured credentials"""
        token_part = self.hetzner_token or ""
        return {
            "endpoint": self.endpoint_url,
            "token_used": f"{'*' * (len(token_part) - 4)}{token_part[-4:] if len(token_part) > 4 else token_part}",
            "credentials_expiration": "Temporary credentials generated by Hetzner"
        }
