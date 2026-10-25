[![Tests](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml/badge.svg)](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml)

# rig

Remote development environments for agents and humans made easy.

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

The project uses pytest for comprehensive test coverage.

### Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run tests excluding credential-dependent tests
python -m pytest tests/ -m "not requires_credentials"

# Run tests with verbose output
python -m pytest tests/ -v

# Run tests with coverage
python -m pytest tests/ --cov=src --cov-report=html
```

### Test Categories

- **Basic Storage Tests** (`test_storage_basic.py`) - Core storage operations
- **Credential Security Tests** (`test_credential_security.py`) - Security feature validation
- **Simulation Tests** (`test_simulation_storage.py`) - Workflow simulations
- **Real E2E Tests** (`test_real_e2e_storage.py`) - Real API integration (requires credentials)
- **Integration Tests** (`test_real_e2e_integration.py`) - Complete integration scenarios
- **Hetzner Tests** (`test_hetzner_storage.py`) - Hetzner-specific functionality

See the [pytest documentation](https://docs.pytest.org/) for more test options.

## Development

Install development dependencies:
```bash
pip install -r requirements.txt
```

Run tests:
```bash
python -m pytest tests/
```

Run E2E tests:
```bash
python -m pytest tests/ -m "not requires_credentials"
```

Lint code:
```bash
pylint src/
```

## License

MIT