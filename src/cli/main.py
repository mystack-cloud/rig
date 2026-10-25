#!/usr/bin/env python3
"""
Main entry point for the remote development environment CLI (storage-focused)
"""

import argparse
import sys
import os

# Add the src directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from cli.storage import StorageManager
from cli.config import Config

def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(description="Remote Development Environment CLI (Storage-focused)")
    parser.add_argument("--config", help="Configuration file path", default="config.yaml")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Storage commands
    storage_parser = subparsers.add_parser("storage", help="Storage operations")
    storage_subparsers = storage_parser.add_subparsers(dest="storage_command", help="Storage operations")

    # Create bucket command
    create_bucket_parser = storage_subparsers.add_parser("create-bucket", help="Create a new bucket")
    create_bucket_parser.add_argument("--name", required=True, help="Bucket name")

    # List buckets command
    list_buckets_parser = storage_subparsers.add_parser("list-buckets", help="List all buckets")

    # Upload file command
    upload_parser = storage_subparsers.add_parser("upload", help="Upload a file to storage")
    upload_parser.add_argument("--bucket", required=True, help="Bucket name")
    upload_parser.add_argument("--file", required=True, help="Local file path")
    upload_parser.add_argument("--object-name", help="Object name in storage (optional)")

    # Download file command
    download_parser = storage_subparsers.add_parser("download", help="Download a file from storage")
    download_parser.add_argument("--bucket", required=True, help="Bucket name")
    download_parser.add_argument("--object", required=True, help="Object name in storage")
    download_parser.add_argument("--file", required=True, help="Local file path")

    # List objects command
    list_objects_parser = storage_subparsers.add_parser("list-objects", help="List objects in a bucket")
    list_objects_parser.add_argument("--bucket", required=True, help="Bucket name")
    list_objects_parser.add_argument("--prefix", help="Object prefix filter")

    # Delete object command
    delete_object_parser = storage_subparsers.add_parser("delete-object", help="Delete an object from storage")
    delete_object_parser.add_argument("--bucket", required=True, help="Bucket name")
    delete_object_parser.add_argument("--object", required=True, help="Object name in storage")

    # Delete bucket command
    delete_bucket_parser = storage_subparsers.add_parser("delete-bucket", help="Delete a bucket")
    delete_bucket_parser.add_argument("--name", required=True, help="Bucket name")

    args = parser.parse_args()

    # Handle commands
    if args.command == "storage":
        try:
            # Initialize storage manager with Hetzner token
            storage = StorageManager()
        except ValueError as e:
            print(f"Configuration error: {e}")
            return

        # Handle storage commands
        if args.storage_command == "create-bucket":
            storage.create_bucket(args.name)

        elif args.storage_command == "list-buckets":
            storage.list_buckets()

        elif args.storage_command == "upload":
            storage.upload_file(args.bucket, args.file, args.object_name)

        elif args.storage_command == "download":
            storage.download_file(args.bucket, args.object, args.file)

        elif args.storage_command == "list-objects":
            storage.list_objects(args.bucket, args.prefix or "")

        elif args.storage_command == "delete-object":
            storage.delete_object(args.bucket, args.object)

        elif args.storage_command == "delete-bucket":
            storage.delete_bucket(args.name)

        else:
            print("Unknown storage command. Use --help to see available commands.")
    else:
        print("Unknown command. Use --help to see available commands.")

if __name__ == "__main__":
    main()
