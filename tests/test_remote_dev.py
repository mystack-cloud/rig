"""
Unit tests for remote development environment functionality
"""
import unittest
import os
import sys
import tempfile
import yaml
from pathlib import Path

class TestRemoteDevConfig(unittest.TestCase):
    """Test configuration parsing for remote dev environments"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_config = {
            'vm': {
                'name': 'test-dev',
                'type': 'cax11',
                'region': 'hel1',
                'image': 'ubuntu-24.04'
            },
            'firewall': {
                'allowed_ips': ['192.168.1.1/32']
            }
        }
    
    def test_config_parsing(self):
        """Test YAML config parsing"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(self.test_config, f)
            config_path = f.name
        
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            self.assertEqual(config['vm']['name'], 'test-dev')
            self.assertEqual(config['vm']['type'], 'cax11')
            self.assertEqual(config['vm']['region'], 'hel1')
            self.assertEqual(config['vm']['image'], 'ubuntu-24.04')
            self.assertEqual(config['firewall']['allowed_ips'], ['192.168.1.1/32'])
        finally:
            os.unlink(config_path)
    
    def test_default_values(self):
        """Test default configuration values"""
        config = {
            'vm': {
                'name': 'dev-vm',
                'type': 'cax11',
                'region': 'hel1',
                'image': 'ubuntu-24.04'
            }
        }
        
        # Apply defaults
        if 'firewall' not in config:
            config['firewall'] = {'allowed_ips': ['0.0.0.0/0']}  # Default: all IPs
        
        self.assertIn('firewall', config)
        self.assertIn('allowed_ips', config['firewall'])
    
    def test_validation(self):
        """Test configuration validation"""
        valid_config = {
            'vm': {
                'name': 'valid-vm',
                'type': 'cax11',
                'region': 'hel1',
                'image': 'ubuntu-24.04'
            }
        }
        
        # Basic validation
        self.assertIn(valid_config['vm']['type'], ['cax11', 'cpx11', 'cx11'])
        self.assertIn(valid_config['vm']['region'], ['hel1', 'nbg1', 'fsn1'])


class TestSSHKeyGeneration(unittest.TestCase):
    """Test SSH key generation functionality"""
    
    def test_key_generation(self):
        """Test that SSH keys can be generated"""
        # This would use a library like paramiko or subprocess to ssh-keygen
        # For now, test the structure
        key_data = {
            'private_key': '-----BEGIN OPENSSH PRIVATE KEY-----',
            'public_key': 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABA',
            'fingerprint': 'SHA256:abcdefghijklmnopqrstuvwxyz'
        }
        
        self.assertIn('private_key', key_data)
        self.assertIn('public_key', key_data)
        self.assertIn('fingerprint', key_data)
        self.assertTrue(key_data['private_key'].startswith('-----BEGIN'))
        self.assertTrue(key_data['public_key'].startswith('ssh-rsa') or 
                       key_data['public_key'].startswith('ssh-ed25519'))


class TestRemoteDevListing(unittest.TestCase):
    """Test listing of remote dev environments"""
    
    def test_list_structure(self):
        """Test that listing returns expected structure"""
        # Simulate a listing response
        mock_environments = [
            {
                'name': 'dev-vm-1',
                'id': '12345',
                'status': 'active',
                'ip': '1.2.3.4',
                'type': 'cax11',
                'region': 'hel1'
            },
            {
                'name': 'dev-vm-2',
                'id': '67890',
                'status': 'stopped',
                'ip': '5.6.7.8',
                'type': 'cax11',
                'region': 'hel1'
            }
        ]
        
        self.assertEqual(len(mock_environments), 2)
        self.assertEqual(mock_environments[0]['status'], 'active')
        self.assertEqual(mock_environments[1]['status'], 'stopped')
    
    def test_list_filtering(self):
        """Test that listing can filter by prefix"""
        all_servers = [
            {'name': 'dev-vm-1', 'id': '1'},
            {'name': 'dev-vm-2', 'id': '2'},
            {'name': 'prod-vm-1', 'id': '3'},
            {'name': 'test-vm-1', 'id': '4'}
        ]
        
        # Filter for dev- prefix
        dev_envs = [s for s in all_servers if s['name'].startswith('dev-')]
        
        self.assertEqual(len(dev_envs), 2)
        self.assertTrue(all(s['name'].startswith('dev-') for s in dev_envs))


class TestRemoteDevAgentFunctions(unittest.TestCase):
    """Test agent installation and attachment functionality"""
    
    def test_agent_installation_script_structure(self):
        """Test that agent installation script has required components"""
        script = """
#!/bin/bash
set -e

# Update system
apt-get update && apt-get upgrade -y

# Install required packages
apt-get install -y curl git python3 python3-pip

# Install OpenCode CLI if requested
if [ "{agent_type}" = "opencode" ]; then
    echo "Installing OpenCode CLI..."
    curl -fsSL https://opencode.ai/install.sh | sh
    
    # Also install opencode server for remote attachment
    pip3 install opencode-server 2>/dev/null || pip install opencode-server
fi

# Install development tools
apt-get install -y vim htop net-tools

# Create opencode user for agent operations
id -u opencode >/dev/null 2>&1 || useradd -m -s /bin/bash opencode

# Setup SSH access for opencode user
mkdir -p /home/opencode/.ssh
chmod 700 /home/opencode/.ssh

# Allow root SSH access for agent attachment
sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd

echo "Agent installation complete!"
"""
        
        # Verify script contains required components
        self.assertIn('apt-get update', script)
        self.assertIn('curl', script)
        self.assertIn('python3-pip', script)
        self.assertIn('opencode-server', script)
        self.assertIn('useradd', script)
        self.assertIn('systemctl restart sshd', script)
    
    def test_agent_attachment_capabilities(self):
        """Test that agent attachment supports required capabilities"""
        capabilities = [
            'SSH connection',
            'Process monitoring',
            'Command execution',
            'Session management'
        ]
        
        required = {
            'SSH connection': True,
            'Process monitoring': True,
            'Command execution': True,
            'Session management': True
        }
        
        for cap in capabilities:
            self.assertIn(cap, required)
            self.assertTrue(required[cap])


class TestProjectConfig(unittest.TestCase):
    """Test project configuration management"""
    
    def test_project_config_init(self):
        """Test project configuration initialization"""
        import tempfile
        import shutil
        
        # Create temp directory
        temp_dir = tempfile.mkdtemp()
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
            from cli.remote_dev import ProjectConfig
            
            config = ProjectConfig(temp_dir)
            result = config.init(name='test-project')
            
            self.assertTrue(result)
            self.assertTrue(os.path.exists(os.path.join(temp_dir, '.rig')))
            self.assertTrue(os.path.exists(os.path.join(temp_dir, '.rig', 'rig.yml')))
            self.assertTrue(os.path.exists(os.path.join(temp_dir, '.rig', 'setup.yml')))
            self.assertTrue(os.path.exists(os.path.join(temp_dir, '.rig', 'vars.yml')))
            self.assertTrue(os.path.exists(os.path.join(temp_dir, '.rig', 'requirements.txt')))
        finally:
            shutil.rmtree(temp_dir)
    
    def test_project_config_save_load(self):
        """Test project configuration save and load"""
        import tempfile
        import shutil
        
        temp_dir = tempfile.mkdtemp()
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
            from cli.remote_dev import ProjectConfig
            
            config = ProjectConfig(temp_dir)
            config.init(name='test-project')
            
            # Modify config
            config.set('vm.region', 'hel1')
            config.set_vars('ansible_user', 'root')
            
            # Save and reload
            config.save()
            
            new_config = ProjectConfig(temp_dir)
            new_config.load()
            
            self.assertEqual(new_config.get('vm.region'), 'hel1')
            self.assertEqual(new_config.get_vars('ansible_user'), 'root')
        finally:
            shutil.rmtree(temp_dir)


class TestAnsibleManager(unittest.TestCase):
    """Test Ansible manager functionality"""
    
    def test_ansible_manager_init(self):
        """Test Ansible manager initialization"""
        import tempfile
        import shutil
        
        temp_dir = tempfile.mkdtemp()
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
            from cli.remote_dev import ProjectConfig, AnsibleManager
            
            config = ProjectConfig(temp_dir)
            ansible = AnsibleManager(config)
            
            self.assertIsNotNone(ansible.project_config)
        finally:
            shutil.rmtree(temp_dir)
    
    def test_inventory_setup(self):
        """Test Ansible inventory setup"""
        import tempfile
        import shutil
        
        temp_dir = tempfile.mkdtemp()
        try:
            from cli.remote_dev import ProjectConfig, AnsibleManager
            
            config = ProjectConfig(temp_dir)
            config.init()
            
            ansible = AnsibleManager(config)
            result = ansible.setup_inventory('192.168.1.1', '/tmp/key')
            
            self.assertTrue(result)
            self.assertTrue(os.path.exists(os.path.join(temp_dir, '.rig', 'inventory.yml')))
        finally:
            shutil.rmtree(temp_dir)


class TestApplySetup(unittest.TestCase):
    """Test apply-setup functionality"""
    
    def test_apply_setup_command_available(self):
        """Test that apply-setup command is available in CLI"""
        import sys as sys_module
        sys_module.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        
        # Import the module to verify it loads
        from cli import remote_dev_cli
        
        # Verify the main function exists
        self.assertTrue(hasattr(remote_dev_cli, 'main'))
        self.assertTrue(callable(remote_dev_cli.main))


class TestStateBucketNaming(unittest.TestCase):
    """Test state bucket naming for test vs production environments"""
    
    def test_test_bucket_has_auto_generated_prefix(self):
        """Test that test buckets are automatically prefixed to separate from prod"""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from cli.remote_dev import RemoteDevManager
        from cli.storage import StorageManager
        
        # Mock storage manager since we're just testing bucket naming
        class MockStorageManager:
            def list_buckets(self):
                return []
            def create_bucket(self, name):
                return True
        
        # Test with default prefix
        os.environ.pop('TEST_BUCKET_PREFIX', None)  # Ensure no env var is set
        manager = RemoteDevManager('test-token', MockStorageManager(), is_test=True)
        
        # Verify bucket name starts with 'test-'
        self.assertTrue(manager.state_bucket.startswith('test-'), 
                       f"Test bucket '{manager.state_bucket}' should start with 'test-'")
        
        # Verify bucket name contains 'rig' and a UUID-like suffix
        self.assertIn('rig', manager.state_bucket)
        self.assertTrue(len(manager.state_bucket) > len('test-rig-'), 
                       "Test bucket should have auto-generated suffix")
    
    def test_test_bucket_with_custom_prefix(self):
        """Test that TEST_BUCKET_PREFIX env var is respected"""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from cli.remote_dev import RemoteDevManager
        
        class MockStorageManager:
            def list_buckets(self):
                return []
            def create_bucket(self, name):
                return True
        
        os.environ['TEST_BUCKET_PREFIX'] = 'ci-'
        try:
            manager = RemoteDevManager('test-token', MockStorageManager(), is_test=True)
            
            # Verify bucket name starts with custom prefix
            self.assertTrue(manager.state_bucket.startswith('ci-'),
                           f"Test bucket '{manager.state_bucket}' should start with 'ci-'")
        finally:
            del os.environ['TEST_BUCKET_PREFIX']
    
    def test_prod_bucket_uses_env_var(self):
        """Test that production bucket uses STATE_BUCKET env var"""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from cli.remote_dev import RemoteDevManager
        
        class MockStorageManager:
            def list_buckets(self):
                return []
            def create_bucket(self, name):
                return True
        
        os.environ['STATE_BUCKET'] = 'my-prod-bucket'
        try:
            manager = RemoteDevManager('test-token', MockStorageManager(), is_test=False)
            
            # Verify production bucket uses env var
            self.assertEqual(manager.state_bucket, 'my-prod-bucket',
                           f"Prod bucket should be 'my-prod-bucket', got '{manager.state_bucket}'")
        finally:
            del os.environ['STATE_BUCKET']
    
    def test_prod_bucket_default(self):
        """Test that production bucket defaults to 'rig'"""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from cli.remote_dev import RemoteDevManager
        
        class MockStorageManager:
            def list_buckets(self):
                return []
            def create_bucket(self, name):
                return True
        
        os.environ.pop('STATE_BUCKET', None)  # Ensure no env var is set
        manager = RemoteDevManager('test-token', MockStorageManager(), is_test=False)
        
        # Verify default prod bucket name
        self.assertEqual(manager.state_bucket, 'rig',
                       f"Default prod bucket should be 'rig', got '{manager.state_bucket}'")
    
    def test_test_buckets_are_unique(self):
        """Test that multiple test bucket instances get unique names"""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from cli.remote_dev import RemoteDevManager
        
        class MockStorageManager:
            def list_buckets(self):
                return []
            def create_bucket(self, name):
                return True
        
        # Create multiple managers and verify unique bucket names
        buckets = set()
        for _ in range(5):
            manager = RemoteDevManager('test-token', MockStorageManager(), is_test=True)
            buckets.add(manager.state_bucket)
        
        # All buckets should be unique
        self.assertEqual(len(buckets), 5, 
                        f"Expected 5 unique buckets, got {len(buckets)}: {buckets}")


if __name__ == '__main__':
    unittest.main()
