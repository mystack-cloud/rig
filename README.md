# Remote Development Environment CLI (Storage-Focused)

A CLI tool for managing remote development environments focused on Hetzner S3-compatible object storage.

## Features

- Manage S3-compatible storage buckets and objects
- Upload/download files to/from storage
- List buckets and objects
- Delete buckets and objects
- Uses Hetzner Cloud Token for authentication
- Secure credential handling with temporary S3 credentials

## Installation

```bash
pip install .
```

## Usage

### Configure Hetzner Token

Set the Hetzner Cloud token as an environment variable:

```bash
export HZ_TOKEN="your-hetzner-cloud-token"
```

### Storage Operations

#### Create a bucket
```bash
python src/cli/main.py storage create-bucket --name my-bucket
```

#### List buckets
```bash
python src/cli/main.py storage list-buckets
```

#### Upload a file
```bash
python src/cli/main.py storage upload --bucket my-bucket --file ./local-file.txt
```

#### Download a file
```bash
python src/cli/main.py storage download --bucket my-bucket --object remote-file.txt --file ./local-file.txt
```

#### List objects in a bucket
```bash
python src/cli/main.py storage list-objects --bucket my-bucket
```

#### Delete an object
```bash
python src/cli/main.py storage delete-object --bucket my-bucket --object remote-file.txt
```

#### Delete a bucket
```bash
python src/cli/main.py storage delete-bucket --name my-bucket
```

## Security Model

### Hetzner Cloud Token Authentication
This solution uses Hetzner Cloud Token for authentication. The token is used to authenticate with Hetzner Cloud, which then generates temporary S3-compatible credentials for storage operations.

### Credential Handling
- **Authentication**: Hetzner Cloud token is used to authenticate with Hetzner Cloud API
- **Credential Generation**: Hetzner generates temporary S3-compatible credentials
- **Exposure**: S3 credentials are displayed only once during generation
- **Security**: Generated credentials are handled by Hetzner's secure infrastructure

### Why This Approach?
1. **No Permanent Credentials**: S3 credentials are never permanently stored
2. **Single Exposure**: S3 credentials are shown only once during generation
3. **Temporary Nature**: Generated credentials expire automatically
4. **Isolated Access**: Token and credentials are separated for security

## End-to-End Testing

To run end-to-end tests with your Hetzner Cloud token:

1. Create a `.env` file with your token:
   ```bash
   HETZNER_CLOUD_API_TOKEN="your-hetzner-cloud-token"
   ```

2. Run the test:
   ```bash
   ./test_e2e_storage.sh
   ```

See `TEST_E2E_GUIDE.md` for detailed testing instructions.

## Development

Install development dependencies:
```bash
pip install -r requirements.txt
```

Run tests:
```bash
make test
```

Run E2E tests:
```bash
make test-e2e
```

Lint code:
```bash
pylint src/
```

## License

MIT