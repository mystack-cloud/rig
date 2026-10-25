"""
Security and authentication utilities for the control plane
"""

import os
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional
import jwt
from functools import wraps
from flask import request, jsonify

# Secret key for JWT signing - in production, this should be loaded from environment variables
SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-here')

class AuthManager:
    """Manages authentication and authorization for the control plane"""

    def __init__(self):
        self.active_tokens = {}
        self.users = {}

    def generate_token(self, user_id: str, role: str = 'user') -> str:
        """Generate a JWT token for a user"""
        payload = {
            'user_id': user_id,
            'role': role,
            'exp': datetime.utcnow() + timedelta(hours=24)
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
        self.active_tokens[token] = {
            'user_id': user_id,
            'role': role,
            'expires_at': datetime.utcnow() + timedelta(hours=24)
        }
        return token

    def verify_token(self, token: str) -> Optional[Dict]:
        """Verify a JWT token and return user info if valid"""
        try:
            decoded = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
            return decoded
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def revoke_token(self, token: str) -> bool:
        """Revoke a JWT token"""
        if token in self.active_tokens:
            del self.active_tokens[token]
            return True
        return False

    def is_valid_token(self, token: str) -> bool:
        """Check if a token is still valid"""
        if token in self.active_tokens:
            expires_at = self.active_tokens[token]['expires_at']
            return datetime.utcnow() < expires_at
        return False

# Initialize auth manager
auth_manager = AuthManager()

def require_auth(f):
    """Decorator to require valid authentication for API endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Missing authorization token'}), 401

        # Remove 'Bearer ' prefix if present
        if token.startswith('Bearer '):
            token = token[7:]

        user_info = auth_manager.verify_token(token)
        if not user_info:
            return jsonify({'error': 'Invalid or expired token'}), 401

        # Add user info to kwargs for use in the decorated function
        kwargs['user_info'] = user_info
        return f(*args, **kwargs)
    return decorated_function
