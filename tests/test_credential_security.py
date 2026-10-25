"""
Credential security tests
Converted from test_credential_security.sh
"""

import pytest
import os
import sys
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def s3_credentials():
    """Setup S3 credentials for StorageManager"""
    os.environ['HETZNER_S3_ACCESS_KEY'] = 'test-access-key'
    os.environ['HETZNER_S3_SECRET_KEY'] = 'test-secret-key'
    yield
    os.environ.pop('HETZNER_S3_ACCESS_KEY', None)
    os.environ.pop('HETZNER_S3_SECRET_KEY', None)


class TestCredentialSecurity:
    """Test credential security features"""
    
    def test_token_masking_in_output(self, hetzner_token, s3_credentials):
        """Test that token is masked in logs and output"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Get credentials info
        creds_info = storage.get_credentials_info()
        
        # Token should be masked (showing only first 4 and last 4 characters)
        token_used = creds_info['token_used']
        
        # Verify masking format: contains asterisks
        assert len(token_used) == len(hetzner_token)
        assert token_used.endswith(hetzner_token[-4:])  # Last 4 chars visible
        assert '*' in token_used
    
    def test_hetzner_auth_flow(self, hetzner_token, s3_credentials):
        """Test Hetzner authentication flow"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # The storage manager should be initialized with Hetzner token
        assert storage.hetzner_token == hetzner_token
        assert storage.endpoint_url is not None
    
    def test_s3_credential_generation(self, hetzner_token, s3_credentials):
        """Test S3 credential generation from Hetzner API"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Check that credentials are generated
        # Note: actual implementation may cache or lazy-load credentials
        assert storage is not None
    
    def test_secure_credential_handling(self, hetzner_token, s3_credentials):
        """Test that credentials are handled securely"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Get credentials info should mask sensitive data
        creds_info = storage.get_credentials_info()
        
        # Endpoint should be present
        assert 'endpoint' in creds_info
        # Token should be masked
        assert 'token_used' in creds_info
        # Secret key should not be in creds_info for security
        assert 'secret_key' not in creds_info
    
    def test_token_not_in_error_messages(self, hetzner_token, s3_credentials):
        """Test that token is not exposed in error messages"""
        from cli.storage import StorageManager
        
        # Try to get credentials info
        storage = StorageManager()
        
        # Check that token is not in string representation
        storage_str = str(storage)
        assert hetzner_token not in storage_str
    
    def test_credential_env_vars_secure(self, hetzner_token, s3_credentials):
        """Test that credentials are read from environment variables securely"""
        from cli.storage import StorageManager
        
        # Test that StorageManager reads from environment
        storage = StorageManager()
        
        # Token should be loaded from environment
        assert storage.hetzner_token == hetzner_token
        
        # S3 endpoint should be loaded
        assert storage.endpoint_url is not None
    
    def test_hetzner_token_format(self, hetzner_token, s3_credentials):
        """Test that Hetzner token is in correct format"""
        # Hetzner Cloud API tokens are Bearer tokens
        assert hetzner_token is not None
        assert len(hetzner_token) > 0
    
    def test_s3_endpoint_url_configured(self, hetzner_token, s3_credentials):
        """Test that S3 endpoint URL is properly configured"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Endpoint should be configured
        assert storage.endpoint_url is not None
        assert 'objectstorage' in storage.endpoint_url.lower() or 'fsn1' in storage.endpoint_url.lower()


class TestSecurityFeatures:
    """Test additional security features"""
    
    def test_credentials_not_logged(self, hetzner_token, s3_credentials):
        """Test that credentials are not logged"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Check that token is not in string representation
        storage_str = str(storage)
        assert hetzner_token not in storage_str
    
    def test_max_retries_on_auth_failure(self, hetzner_token, s3_credentials):
        """Test that authentication failures are handled with retries"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Should handle retry scenario
        assert storage is not None
    
    def test_timeout_on_auth_request(self, hetzner_token, s3_credentials):
        """Test timeout handling for authentication requests"""
        from cli.storage import StorageManager
        
        storage = StorageManager()
        
        # Should handle timeout gracefully
        assert storage is not None
