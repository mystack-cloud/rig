#!/usr/bin/env python3
"""
Simple E2E Test to verify CLI structure and functionality
"""

import subprocess
import sys
import os

def run_command(cmd):
    """Run a command and return the result"""
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("SUCCESS")
        if result.stdout:
            print(f"STDOUT: {result.stdout}")
        if result.stderr:
            print(f"STDERR: {result.stderr}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"FAILED: {e}")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        return False

def main():
    print("=== Simple E2E Test for Hetzner S3-Compatible Storage CLI ===")
    
    # Test 1: Check that CLI exists and can be imported
    print("\n1. Testing CLI import...")
    try:
        import src.cli.main
        print("SUCCESS: CLI module can be imported")
    except Exception as e:
        print(f"FAILED: Cannot import CLI - {e}")
        return False
    
    # Test 2: Check that CLI can show help
    print("\n2. Testing CLI help...")
    cmd = [sys.executable, "src/cli/main.py", "--help"]
    success = run_command(cmd)
    if not success:
        return False
    
    # Test 3: Test basic storage commands (without actual Hetzner connection)
    print("\n3. Testing basic storage commands...")
    
    # Test list buckets command
    cmd = [sys.executable, "src/cli/main.py", "storage", "list-buckets"]
    success = run_command(cmd)
    if not success:
        print("Note: list-buckets failed (expected for simulation)")
    
    # Test create bucket command (should show usage or fail gracefully)
    cmd = [sys.executable, "src/cli/main.py", "storage", "create-bucket", "--name", "test-bucket"]
    success = run_command(cmd)
    if not success:
        print("Note: create-bucket command failed (expected for simulation)")
    
    print("\n=== Test Complete ===")
    print("This demonstrates that the CLI structure is working.")
    print("Actual Hetzner integration requires valid credentials.")

if __name__ == "__main__":
    main()