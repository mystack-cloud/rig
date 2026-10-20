# Storage-Only CLI Guide for Hetzner S3-Compatible Storage

This document explains how to use the CLI-only solution for managing Hetzner S3-compatible object storage.

## Overview

This CLI tool is specifically designed to manage storage operations with Hetzner's S3-compatible object storage. It provides a simple interface for common storage operations without requiring a full control plane.

## Installation

### Prerequisites
- Python 3.7 or higher
- pip package manager

### Setup Steps

1. **Install the package**:
   ```bash
   pip install .
   ```

2. **Configure environment variables** (recommended):
   ```bash
   export S3_ENDPOINT_URL="https://fsn1.yourdomain.com"
   export S3_ACCESS_KEY="your-access-key"
   export S3_SECRET_KEY="your-secret-key"
   ```

3. **Or create a config.yaml file**:
   ```yaml
   s3_endpoint_url: "https://fsn1.yourdomain.com"
   s3_access_key: "your-access-key"
   s3_secret_key: "your-secret-key"
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
2. **Storage Manager** (`src/cli/storage.py`): Manages S3-compatible storage operations
3. **Configuration Handler** (`src/cli/config.py`): Manages configuration loading and saving

## Configuration Options

### Environment Variables
- `S3_ENDPOINT_URL`: The S3 endpoint URL (e.g., `https://fsn1.yourdomain.com`)
- `S3_ACCESS_KEY`: Access key for authentication
- `S3_SECRET_KEY`: Secret key for authentication

### Configuration File
The CLI reads from `config.yaml` if it exists. The file should contain:
```yaml
s3_endpoint_url: "https://fsn1.yourdomain.com"
s3_access_key: "your-access-key"
s3_secret_key: "your-secret-key"
```

## Error Handling

The CLI includes robust error handling:
- Invalid configuration settings
- Network connectivity issues
- File operation errors
- Storage permission problems

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

## Security Considerations

1. **Environment Variables**: Sensitive credentials should be stored in environment variables rather than configuration files
2. **HTTPS**: Always use HTTPS endpoints for secure connections
3. **Access Controls**: Ensure proper IAM policies are configured for your S3 storage

## Troubleshooting

### Common Issues

1. **Configuration Missing**: Ensure S3 configuration is set via environment variables or config file
2. **Permission Denied**: Verify that your access keys have proper permissions
3. **Network Issues**: Check connectivity to the S3 endpoint

### Debugging

Add `--debug` flag for verbose output (if implemented in future versions):

```bash
python3 src/cli/main.py storage list-buckets --debug
```

## License

MIT