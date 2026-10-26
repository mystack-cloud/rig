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
from cli.vpn import VPNManager, setup_remote_vpn_connection, get_vpn_status, teardown_vpn_connection

# Import package version
try:
    from importlib.metadata import version
    __version__ = version("remote-dev-control-plane")
except ImportError:
    try:
        from importlib_metadata import version
        __version__ = version("remote-dev-control-plane")
    except ImportError:
        __version__ = "0.1.0"

def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Remote Development Environment CLI (Storage-focused)"
    )
    parser.add_argument("--version", action="version", version=f"rdcp {__version__}")
    parser.add_argument("--config", help="Configuration file path", default="config.yaml")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # VPN commands
    vpn_parser = subparsers.add_parser("vpn", help="VPN tunnel operations (optional, for accessing upstream services)")
    vpn_subparsers = vpn_parser.add_subparsers(dest="vpn_command", help="VPN operations")

    # VPN generate-keys command
    vpn_generate_parser = vpn_subparsers.add_parser("generate-keys", help="Generate WireGuard key pair")

    # VPN setup command
    vpn_setup_parser = vpn_subparsers.add_parser("setup", help="Setup WireGuard VPN tunnel to remote gateway")
    vpn_setup_parser.add_argument("--endpoint", required=True, help="VPN gateway endpoint (host:port)")
    vpn_setup_parser.add_argument("--interface", default="wg0", help="WireGuard interface name")

    # VPN teardown command
    vpn_teardown_parser = vpn_subparsers.add_parser("teardown", help="Tear down WireGuard VPN tunnel")
    vpn_teardown_parser.add_argument("--interface", default="wg0", help="WireGuard interface name")

    # VPN status command
    vpn_status_parser = vpn_subparsers.add_parser("status", help="Check WireGuard VPN tunnel status")
    vpn_status_parser.add_argument("--interface", default="wg0", help="WireGuard interface name")

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

    # Handle VPN commands
    if args.command == "vpn":
        try:
            vpn_manager = VPNManager()
        except Exception as e:
            print(f"Configuration error: {e}")
            return

        if args.vpn_command == "generate-keys":
            keys = vpn_manager.generate_wireguard_keys()
            print("Generated WireGuard key pair:")
            print(f"Private key: {keys['private_key']}")
            print(f"Public key: {keys['public_key']}")

        elif args.vpn_command == "setup":
            # Get private key from environment or prompt
            private_key = os.getenv('WG_PRIVATE_KEY')
            peer_public_key = os.getenv('WG_PEER_PUBLIC_KEY')
            
            if not private_key or not peer_public_key:
                print("Error: WG_PRIVATE_KEY and WG_PEER_PUBLIC_KEY environment variables required")
                print("Example: WG_PRIVATE_KEY=... WG_PEER_PUBLIC_KEY=... rig vpn setup --endpoint ...")
                return
            
            success = vpn_manager.setup_vpn_with_credentials(
                name=args.interface,
                endpoint=args.endpoint,
                private_key=private_key,
                peer_public_key=peer_public_key
            )
            if not success:
                return 1

        elif args.vpn_command == "teardown":
            success = vpn_manager.teardown_vpn_tunnel(args.interface)
            if not success:
                return 1

        elif args.vpn_command == "status":
            status = vpn_manager.check_vpn_status(args.interface)
            print(f"VPN Tunnel Status: {args.interface}")
            print(f"  Active: {status['up']}")
            print(f"  Status: {status['status']}")
            if status['error']:
                print(f"  Error: {status['error']}")

        else:
            print("Unknown VPN command. Use --help to see available commands.")

    # Handle storage commands
    elif args.command == "storage":
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
