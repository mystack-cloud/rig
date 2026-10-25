"""
Unit tests for the CLI functionality
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the src directory to the path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cli.environment import EnvironmentManager
from cli.session import SessionManager

class TestCLIEnvironmentManager(unittest.TestCase):
    """Test cases for the CLI environment manager"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            'control_plane_url': 'http://localhost:5000'
        }
        self.env_manager = EnvironmentManager(self.config)
        
    def test_environment_manager_init(self):
        """Test initializing the environment manager"""
        self.assertIsNotNone(self.env_manager)
        self.assertEqual(self.env_manager.config['control_plane_url'], 'http://localhost:5000')
        
    @patch('requests.post')
    def test_create_environment_success(self, mock_post):
        """Test successful environment creation"""
        mock_post.return_value.status_code = 201
        result = self.env_manager.create_environment('test-env', 'ansible')
        self.assertTrue(result)
        
    @patch('requests.post')
    def test_create_environment_failure(self, mock_post):
        """Test failed environment creation"""
        mock_post.return_value.status_code = 400
        result = self.env_manager.create_environment('test-env', 'ansible')
        self.assertFalse(result)
        
    @patch('requests.get')
    def test_list_environments_success(self, mock_get):
        """Test successful environment listing"""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {'name': 'test-env', 'provider': 'ansible', 'status': 'running'}
        ]
        result = self.env_manager.list_environments()
        self.assertIsInstance(result, list)
        
    @patch('requests.delete')
    def test_destroy_environment_success(self, mock_delete):
        """Test successful environment destruction"""
        mock_delete.return_value.status_code = 200
        result = self.env_manager.destroy_environment('test-env')
        self.assertTrue(result)

class TestCLISessionManager(unittest.TestCase):
    """Test cases for the CLI session manager"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            'control_plane_url': 'http://localhost:5000'
        }
        self.session_manager = SessionManager(self.config)
        
    def test_session_manager_init(self):
        """Test initializing the session manager"""
        self.assertIsNotNone(self.session_manager)
        self.assertEqual(self.session_manager.config['control_plane_url'], 'http://localhost:5000')
        
    @patch('requests.post')
    def test_create_session_success(self, mock_post):
        """Test successful session creation"""
        mock_post.return_value.status_code = 201
        result = self.session_manager.create_session('test-env', 'test-session')
        self.assertTrue(result)
        
    @patch('requests.post')
    def test_create_session_failure(self, mock_post):
        """Test failed session creation"""
        mock_post.return_value.status_code = 400
        result = self.session_manager.create_session('test-env', 'test-session')
        self.assertFalse(result)
        
    @patch('requests.get')
    def test_list_sessions_success(self, mock_get):
        """Test successful session listing"""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {'name': 'test-session', 'environment': 'test-env', 'status': 'active'}
        ]
        result = self.session_manager.list_sessions('test-env')
        self.assertIsInstance(result, list)
        
    @patch('requests.post')
    def test_join_session_success(self, mock_post):
        """Test successful session joining"""
        mock_post.return_value.status_code = 200
        result = self.session_manager.join_session('test-session', 'user123')
        self.assertTrue(result)
        
    @patch('requests.post')
    def test_leave_session_success(self, mock_post):
        """Test successful session leaving"""
        mock_post.return_value.status_code = 200
        result = self.session_manager.leave_session('test-session', 'user123')
        self.assertTrue(result)

if __name__ == '__main__':
    unittest.main()