# Hetzner S3-Compatible Storage CLI Guide

This document explains how to use the CLI-only solution for managing Hetzner S3-compatible object storage with token-based authentication.

## Overview

This CLI tool is specifically designed to manage storage operations with Hetzner's S3-compatible object storage using Hetzner Cloud Tokens for authentication. It provides a simple interface for common storage operations without requiring explicit S3 credentials.

## Installation

### Prerequisites
- Python 3.7 or higher
- pip package manager

### Setup Steps

1. **Install the package**:
   ```bash
   pip install .
   ```

2. **Configure the Hetzner Cloud token**:
   ```bash
   export HZ_TOKEN="your-hetzner-cloud-token"
   ```

## Usage Examples

### 1. List Buckets
```bash
python3 src/cli/main.py storage list-buckets
```

### 2. Create a Bucket
```bash
python3 src/cli/main.py storage create-bucket --name my-new-bucket
```

### 3. Upload a File
```bash
python3 src/cli/main.py storage upload \
  --bucket my-new-bucket \
  --file ./local-file.txt \
  --object-name remote-file.txt
```

### 4. Download a File
```bash
python3 src/cli/main.py storage download \
  --bucket my-new-bucket \
  --object remote-file.txt \
  --file ./downloaded-file.txt
```

### 5. List Objects in a Bucket
```bash
python3 src/cli/main.py storage list-objects \
  --bucket my-new-bucket \
  --prefix "subdirectory/"
```

### 6. Delete an Object
```bash
python3 src/cli/main.py storage delete-object \
  --bucket my-new-bucket \
  --object remote-file.txt
```

### 7. Delete a Bucket
```bash
python3 src/cli/main.py storage delete-bucket --name my-new-bucket
```

## Architecture

The CLI follows a modular architecture:

1. **Main Entry Point** (`src/cli/main.py`): Parses command-line arguments and routes to appropriate handlers
2. **Storage Manager** (`src/cli/storage.py`): Manages Hetzner S3-compatible storage operations
3. **Configuration Handler** (`src/cli/config.py`): Manages configuration loading and saving

## Configuration

### Environment Variables
- `HZ_TOKEN`: Hetzner Cloud API token (required)
- `S3_ENDPOINT_URL`: S3 endpoint URL (defaults to `https://fsn1.yourdomain.com`)

### Configuration File (`config.yaml`)
```yaml
hz_token: "your-hetzner-cloud-token"
s3_endpoint_url: "https://fsn1.yourdomain.com"
```

## How It Works

### Token-Based Authentication
The solution uses Hetzner Cloud Token for authentication:
1. The token authenticates with Hetzner Cloud API
2. Hetzner generates temporary S3-compatible credentials
3. These credentials are used for storage operations

### Credential Handling Process
1. **Authentication**: CLI authenticates with Hetzner Cloud using the token
2. **Credential Generation**: Hetzner generates temporary S3 credentials
3. **Credential Display**: S3 credentials shown only once during generation
4. **Secure Handling**: Generated credentials are managed by Hetzner's secure infrastructure

### Endpoint Configuration
- Default endpoint: `https://fsn1.yourdomain.com`
- Can be overridden via environment variable or config file

## Security Considerations

1. **Token Management**: 
   - Store Hetzner Cloud tokens securely
   - Use environment variables rather than hardcoded values
   - Rotate tokens regularly

2. **Temporary Credentials**:
   - S3 credentials are generated temporarily
   - No permanent credential storage in the CLI
   - Credentials are shown only once during generation

3. **Network Security**:
   - All operations use HTTPS
   - Endpoints are validated for security

## Credential Replacement Process

### When Credentials Need Replacement
1. **Expiration**: Temporary S3 credentials expire automatically
2. **Manual Refresh**: User requests new credentials via CLI
3. **Security Breach**: If compromise is suspected

### How Replacement Works
1. **Re-authentication**: CLI re-authenticates with Hetzner Cloud using token
2. **New Credential Generation**: Hetzner generates new temporary S3 credentials
3. **Display**: New credentials are shown to user (only once)
4. **Secure Storage**: New credentials are handled by Hetzner's secure system

## Error Handling

The CLI includes robust error handling:
- Invalid or missing Hetzner token
- Network connectivity issues
- Storage permission problems
- File operation errors

## Development

### Testing
```bash
python -m pytest tests/
```

### Linting
```bash
pylint src/
```

### Building Distribution
```bash
python setup.py sdist bdist_wheel
```

## License

MIT