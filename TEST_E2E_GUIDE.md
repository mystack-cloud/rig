# End-to-End Testing Guide for Hetzner S3-Compatible Storage

This guide explains how to run end-to-end tests for the Hetzner S3-compatible storage solution.

## Prerequisites

1. **Python 3.7+** installed
2. **pip** package manager
3. **Hetzner Cloud API Token** with appropriate permissions
4. **.env** file with the token (see sample.env)

## Setup Instructions

### 1. Create the .env file

Rename `sample.env` to `.env` and populate it with your Hetzner Cloud API token:

```bash
# Copy the sample file
cp sample.env .env

# Edit .env and replace with your actual token
# vi .env
```

### 2. Install Dependencies

```bash
pip install .
```

### 3. Run End-to-End Tests

```bash
./test_e2e_storage.sh
```

## Test Structure

The end-to-end test covers:

1. **Basic Storage Operations**
   - List buckets
   - Create bucket
   - Upload file (environment state)
   - List objects
   - Download file
   - Delete object
   - Delete bucket

2. **Shared State Management**
   - Create shared state bucket
   - Upload shared state
   - List shared objects
   - Download shared state
   - Delete shared object
   - Delete shared state bucket

## Expected Behavior

### Credential Handling
- The Hetzner Cloud token authenticates with Hetzner Cloud API
- Temporary S3 credentials are generated on demand
- S3 credentials are shown only once during generation
- Generated credentials are handled by Hetzner's secure infrastructure

### Storage Operations
- Buckets are created with unique names
- Files are uploaded with proper paths
- Objects are listed and retrieved correctly
- State files are stored flat in one file per development environment
- Shared state files are stored in a shared bucket

## Security Notes

### Credential Exposure
- The Hetzner Cloud token is masked in logs for security
- S3 credentials are shown only once during generation
- No permanent credential storage in the CLI

### Temporary Credentials
- S3 credentials are generated temporarily by Hetzner
- Credentials expire automatically after a period
- New credentials are generated when needed

## Test Output

The test will show:
1. Authentication with Hetzner Cloud
2. Bucket creation and deletion
3. File upload and download operations
4. Object listing and retrieval
5. Security features in action

## Troubleshooting

### Common Issues

1. **Token Not Found**
   ```bash
   Error: HETZNER_CLOUD_API_TOKEN not found in .env file
   ```
   - Ensure `.env` file exists and contains the token
   - Verify the token is not commented out

2. **Permission Denied**
   ```bash
   Permission denied for storage operations
   ```
   - Check that the Hetzner token has appropriate permissions
   - Verify the endpoint URL is correct

3. **Network Issues**
   ```bash
   Connection failed to Hetzner storage
   ```
   - Check internet connectivity
   - Verify the S3 endpoint is accessible

## License

MIT