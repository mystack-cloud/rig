"""
Unit tests for the control plane functionality
"""

import unittest
from unittest.mock import Mock, patch
import sys
import os

# Add the src directory to the path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.control_plane.api import app, init_db
from src.health.monitor import HealthMonitor
from src.provisioning.ansible import AnsibleProvider
from src.cli.environment import EnvironmentManager

class TestControlPlane(unittest.TestCase):
    """Test cases for the control plane API"""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.app = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()
        
        # Initialize database
        init_db()
        
    def tearDown(self):
        """Clean up after each test method."""
        self.app_context.pop()
        
    def test_health_check_endpoint(self):
        """Test the health check endpoint"""
        response = self.app.get('/health')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue('status' in data)
        self.assertEqual(data['status'], 'healthy')
        
    def test_environment_crud_operations(self):
        """Test CRUD operations for environments"""
        # Create environment
        response = self.app.post('/environments', 
                                json={'name': 'test-env', 'provider': 'ansible'})
        self.assertEqual(response.status_code, 201)
        
        # Get environment
        response = self.app.get('/environments/test-env')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['name'], 'test-env')
        
        # Update environment
        response = self.app.put('/environments/test-env',
                               json={'name': 'test-env', 'provider': 'ansible', 'status': 'updated'})
        self.assertEqual(response.status_code, 200)
        
        # Delete environment
        response = self.app.delete('/environments/test-env')
        self.assertEqual(response.status_code, 200)

class TestHealthMonitor(unittest.TestCase):
    """Test cases for the health monitor"""
    
    def test_cpu_memory_metrics(self):
        """Test collecting CPU and memory metrics"""
        monitor = HealthMonitor()
        metrics = monitor.get_system_metrics()
        
        self.assertIn('cpu_percent', metrics)
        self.assertIn('memory_usage', metrics)
        self.assertIn('disk_space', metrics)
        
    def test_service_status(self):
        """Test service status checking"""
        monitor = HealthMonitor()
        # Mock the service status check to return a known result
        with patch('psutil.pid_exists') as mock_pid_exists:
            mock_pid_exists.return_value = True
            status = monitor.check_service_status('test_service')
            self.assertTrue(status)

class TestProvisioningProvider(unittest.TestCase):
    """Test cases for the Ansible provisioning provider"""
    
    def test_provider_initialization(self):
        """Test initializing the Ansible provider"""
        provider = AnsibleProvider()
        self.assertIsNotNone(provider)
        
    def test_provisioning_methods_exist(self):
        """Test that required provisioning methods exist"""
        provider = AnsibleProvider()
        self.assertTrue(hasattr(provider, 'create_environment'))
        self.assertTrue(hasattr(provider, 'destroy_environment'))
        self.assertTrue(hasattr(provider, 'get_environment_status'))

if __name__ == '__main__':
    unittest.main()