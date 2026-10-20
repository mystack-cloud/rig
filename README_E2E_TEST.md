# End-to-End Test for Hetzner Cloud Object Storage

## Overview

This document describes the end-to-end test for Hetzner Cloud Object Storage credential management. 

## Test Structure

The test suite consists of three main components:

1. **test_simulation_storage.sh** - Simulates the complete workflow with Hetzner storage
2. **test_real_hetzner_storage.py** - Validates Hetzner Cloud credential management
3. **test_credential_security.sh** - Verifies security features

## Credential Management Process

### 1. Authentication with Hetzner Cloud
- Uses Hetzner Cloud token for authentication
- Validates token format and permissions

### 2. Credential Generation
- Generates temporary S3-compatible credentials via Hetzner API
- Shows credentials only once during generation
- Handles credential storage securely

### 3. Storage Operations
- Create buckets with unique names to avoid conflicts
- Upload/download files to/from storage
- List and delete objects
- Clean up resources automatically

## Security Features

### Single Credential Display
- S3 credentials are shown only once during generation
- Credentials are handled by Hetzner's secure infrastructure
- Temporary credentials are destroyed after use

### Token Management
- Uses Hetzner Cloud token for authentication
- Tokens are stored in environment variables
- No hardcoded credentials in source code

## Test Execution

### Prerequisites
1. Set Hetzner Cloud token:
   ```bash
   export HZ_TOKEN="your_hetzner_cloud_token_here"
   ```

### Run the Tests
```bash
# Run the simulation test
./test_simulation_storage.sh

# Run the credential validation
python3 test_real_hetzner_storage.py

# Run security tests  
./test_credential_security.sh
```

## Real Implementation Notes

The actual Hetzner integration would:
1. Use the Hetzner Cloud token to authenticate
2. Call Hetzner's Object Storage API to generate S3 credentials
3. Return credentials in a secure manner
4. Store credentials securely for reuse
5. Handle cleanup of temporary resources

## Resources

- **Documentation**: HETZNER_STORAGE_GUIDE.md
- **Security**: HETZNER_STORAGE_SECURITY_GUIDE.md
- **CLI Commands**: CLI_STORAGE_SUMMARY.md