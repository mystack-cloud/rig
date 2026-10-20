# Hetzner S3-Compatible Storage Security Guide

This document explains the security considerations and credential handling for the Hetzner S3-compatible storage CLI solution.

## Overview

The CLI solution uses Hetzner Cloud Token authentication for accessing Hetzner S3-compatible storage. This approach ensures that S3 credentials are never permanently stored and are only shown once during generation.

## Security Model

### Hetzner Cloud Token Authentication

1. **Authentication Method**: Uses Hetzner Cloud API token for authentication
2. **Credential Generation**: Hetzner generates temporary S3-compatible credentials
3. **Credential Exposure**: S3 credentials are shown only once when generated
4. **Storage**: Generated credentials are securely handled by Hetzner's infrastructure

### Credential Handling Process

1. **Token Input**: User provides Hetzner Cloud Token via environment variable
2. **Authentication**: CLI authenticates with Hetzner Cloud using the token
3. **Credential Generation**: Hetzner generates temporary S3 credentials
4. **Credential Display**: S3 credentials shown to user (only once)
5. **Secure Storage**: Generated credentials are handled by Hetzner's secure storage system

## Why This Approach?

### Security Benefits

1. **No Permanent Credentials**: S3 credentials are never permanently stored
2. **Single Exposure**: S3 credentials are shown only once during generation
3. **Temporary Nature**: Generated credentials expire after a short period
4. **Token Isolation**: Hetzner Cloud token is separate from S3 credentials

### Security Best Practices

1. **Environment Variable Usage**: 
   - Store Hetzner Cloud token in environment variables
   - Never hardcode tokens in source code

2. **Token Rotation**: 
   - Regularly rotate Hetzner Cloud tokens
   - Implement token expiration policies

3. **Access Control**: 
   - Limit token scope to minimal required permissions
   - Use least privilege principle

4. **Secure Transmission**: 
   - All communications use HTTPS
   - Validate endpoints for security

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

## Implementation Details

### Token Management

```bash
# Set the Hetzner Cloud token (required)
export HZ_TOKEN="your-hetzner-cloud-token"
```

### Credential Flow

1. **Initial Setup**: 
   - User provides Hetzner Cloud token
   - CLI authenticates with Hetzner Cloud
   - Hetzner generates temporary S3 credentials

2. **Operation Execution**:
   - CLI uses temporary S3 credentials for operations
   - Credentials are used for the duration of the operation

3. **Credential Renewal**:
   - When credentials expire, CLI re-authenticates
   - New temporary credentials are generated

## Security Recommendations

### For Users

1. **Store Tokens Securely**: 
   - Use environment variables
   - Avoid hardcoding tokens
   - Use secure credential managers

2. **Token Permissions**:
   - Grant minimal required permissions
   - Regular rotation of tokens
   - Audit token usage

3. **Credential Handling**:
   - Never share S3 credentials
   - Store credentials securely
   - Monitor for unauthorized access

### For Developers

1. **Secure Implementation**:
   - Validate token inputs
   - Hide token display
   - Implement proper error handling

2. **Infrastructure Security**:
   - Use HTTPS for all communications
   - Secure credential storage
   - Implement rate limiting

## Configuration Example

```yaml
# config.yaml
hz_token: "your-hetzner-cloud-token"  # Only stored in environment variables
s3_endpoint_url: "https://fsn1.yourdomain.com"  # Default Hetzner endpoint
```

## Security Features

1. **Token Masking**: 
   - Displayed as `****token****` to prevent exposure

2. **Secure Communication**:
   - All operations use HTTPS
   - TLS encryption for all communications

3. **Temporary Credentials**:
   - Generated on-demand
   - Expire automatically
   - No persistent storage

## Compliance

This implementation follows security best practices for cloud storage access:
- Token-based authentication
- Temporary credential management
- Secure credential transmission
- No permanent credential storage

## License

MIT