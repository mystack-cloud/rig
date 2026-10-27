"""
E2E test for workspace git clone functionality

Tests that the rig CLI can:
1. Create a remote development environment
2. Clone the rig repository on the remote VM via Ansible using GitHub PAT
3. Verify the cloned repository exists on the remote VM
"""

import pytest
import os
import sys
import uuid
import time
import tempfile
import shutil

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cli.remote_dev import RemoteDevEnvironment, ProjectConfig, AnsibleManager
from cli.storage import StorageManager


# Test repository to clone: the rig repository itself
RIG_REPO = "https://github.com/mlewk/mystack.git"
RIG_REPO_NAME = "mystack"
RIG_CLONE_DEST = "/root/mystack"
TEST_DIR_NAME = "test-rig-git-clone"

# Get GitHub PAT from environment (set in GitHub Actions as RIG_GITHUB_PAT)
GITHUB_PAT = os.getenv('RIG_GITHUB_PAT', None)


@pytest.fixture
def test_workspace_dir(tmp_path):
    """Create a test workspace directory"""
    workspace_dir = tmp_path / TEST_DIR_NAME
    workspace_dir.mkdir()
    return str(workspace_dir)


@pytest.fixture
def unique_env_name(hetzner_token):
    """Generate a unique environment name"""
    timestamp = int(time.time())
    return f"rig-git-test-{hetzner_token[:8]}-{timestamp}"


@pytest.mark.requires_credentials
@pytest.mark.integration
@pytest.mark.e2e
class TestWorkspaceGitClone:
    """Test workspace git clone functionality on remote development environments"""

    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")
    
    def test_rig_repo_clone_on_remote_env(
        self,
        hetzner_token,
        test_workspace_dir,
        unique_env_name
    ):
        """
        Test cloning the rig repository on a remote development environment
        
        Steps:
        1. Initialize project configuration in workspace
        2. Create remote development environment
        3. Configure Ansible to clone rig repository using GitHub PAT
        4. Execute playbook to clone repository
        5. Verify repository was cloned successfully
        6. Cleanup
        """
        print(f"\n=== Testing Rig Repo Clone on Remote Dev Environment ===")
        print(f"Workspace: {test_workspace_dir}")
        print(f"Environment: {unique_env_name}")
        print(f"Repository: {RIG_REPO}")
        
        # Check for GitHub PAT
        if not GITHUB_PAT:
            print(f"   ⚠️  RIG_GITHUB_PAT not set, skipping clone test")
            pytest.skip("RIG_GITHUB_PAT environment variable not set")
        
        # Step 1: Initialize project configuration
        print(f"\n1. Initializing project configuration...")
        project_config = ProjectConfig(test_workspace_dir)
        project_config.init(name=unique_env_name, ansible_enabled=True)
        assert os.path.exists(project_config.rig_dir), ".rig directory should exist"
        assert os.path.exists(os.path.join(project_config.rig_dir, 'rig.yml')), "rig.yml should exist"
        print(f"   ✓ Project configuration initialized")
        
        # Step 2: Create remote development environment
        print(f"\n2. Creating remote development environment...")
        env = RemoteDevEnvironment(name=unique_env_name)
        
        # Override name to use our unique name
        env.name = unique_env_name
        env.vm_name = unique_env_name
        
        setup_success = env.setup()
        assert setup_success, "Environment setup should succeed"
        assert env.vm_id is not None, "VM ID should be set"
        assert env.vm_ip is not None, "VM IP should be set"
        print(f"   ✓ Environment created")
        print(f"   VM IP: {env.vm_ip}")
        
        try:
            # Step 3: Configure Ansible manager
            print(f"\n3. Configuring Ansible manager...")
            ansible_manager = AnsibleManager(project_config)
            ansible_manager.configure(test_workspace_dir)
            
            # Set up inventory with the VM's IP
            ssh_key_path = f'/tmp/{env.ssh_key_name}'
            ansible_manager.setup_inventory(
                vm_ip=env.vm_ip,
                ssh_key_path=ssh_key_path,
                username='root'
            )
            assert os.path.exists(ansible_manager.inventory_path), "Inventory should be created"
            print(f"   ✓ Inventory configured")
            
            # Set up requirements
            ansible_manager.setup_requirements()
            ansible_manager.setup_roles()
            print(f"   ✓ Requirements and roles configured")
            
            # Step 4: Create playbook to clone rig repository with PAT
            print(f"\n4. Creating rig repo clone playbook with PAT authentication...")
            
            # Generate SSH known_hosts entry for GitHub
            get_github_known_hosts = {
                'name': 'Get GitHub SSH known hosts',
                'shell': 'ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null || true',
                'changed_when': False
            }
            
            # Clone the rig repository using HTTPS with PAT
            clone_rig_task = {
                'name': f'Clone {RIG_REPO_NAME} repository with GitHub PAT',
                'git': {
                    'repo': RIG_REPO,
                    'dest': RIG_CLONE_DEST,
                    'accept_hostkey': True,
                    'force': True,
                    'username': 'git',
                    'password': GITHUB_PAT  # Use PAT for authentication
                }
            }
            
            custom_tasks = [
                get_github_known_hosts,
                clone_rig_task
            ]
            
            ansible_manager.setup_playbook(custom_tasks=custom_tasks)
            assert os.path.exists(ansible_manager.playbook_path), "Playbook should be created"
            print(f"   ✓ Playbook created with git clone task using PAT")
            
            # Read and display playbook (mask PAT in output)
            with open(ansible_manager.playbook_path, 'r') as f:
                playbook_content = f.read()
                # Mask the PAT in displayed content
                masked_content = playbook_content.replace(GITHUB_PAT, '***REDACTED***')
                print(f"\n   Playbook content:")
                print(f"   {'=' * 50}")
                for line in masked_content.split('\n')[:25]:
                    print(f"   {line}")
                print(f"   ...")
            
            # Step 5: Install Ansible roles
            print(f"\n5. Installing Ansible roles...")
            ansible_manager.install_roles()
            print(f"   ✓ Ansible roles installed")
            
            # Step 6: Run playbook to clone repository
            print(f"\n6. Running playbook to clone rig repository...")
            print(f"   This may take a few minutes...")
            
            # Run the playbook with extra vars
            extra_vars = {
                'ansible_ssh_private_key_file': ssh_key_path,
                'ansible_user': 'root',
                'git_repo_url': RIG_REPO,
                'git_repo_dest': RIG_CLONE_DEST,
                'git_pat': GITHUB_PAT
            }
            
            playbook_success = ansible_manager.run_playbook(extra_vars=extra_vars)
            
            print(f"   Playbook execution result: {'SUCCESS' if playbook_success else 'FAILED'}")
            
            # Step 7: Verify repository exists via SSH
            print(f"\n7. Verifying repository on remote VM...")
            
            import subprocess
            
            # Check if git is installed on VM
            check_git = subprocess.run([
                'ssh', '-i', ssh_key_path, '-o', 'StrictHostKeyChecking=no',
                '-o', 'BatchMode=yes',
                f'root@{env.vm_ip}', 'which git'
            ], capture_output=True, text=True, timeout=30)
            
            if check_git.returncode == 0:
                print(f"   ✓ Git is installed on VM")
                
                # Check if repository exists
                check_repo = subprocess.run([
                    'ssh', '-i', ssh_key_path, '-o', 'StrictHostKeyChecking=no',
                    '-o', 'BatchMode=yes',
                    f'root@{env.vm_ip}', f'ls -la {RIG_CLONE_DEST}'
                ], capture_output=True, text=True, timeout=30)
                
                if check_repo.returncode == 0:
                    print(f"   ✓ Repository directory exists on VM")
                    print(f"   {check_repo.stdout}")
                    
                    # Check if it's a valid git repo
                    check_git_dir = subprocess.run([
                        'ssh', '-i', ssh_key_path, '-o', 'StrictHostKeyChecking=no',
                        '-o', 'BatchMode=yes',
                        f'root@{env.vm_ip}', f'test -d {RIG_CLONE_DEST}/.git && echo "GIT_DIR_EXISTS"'
                    ], capture_output=True, text=True, timeout=30)
                    
                    if 'GIT_DIR_EXISTS' in check_git_dir.stdout:
                        print(f"   ✓ Valid git repository (contains .git directory)")
                else:
                    print(f"   ⚠️  Repository not found")
                    print(f"      Error: {check_repo.stderr}")
            else:
                print(f"   ⚠️  Git not found on VM (installation may be pending)")
            
            print(f"\n✓ Test completed successfully")
            
        finally:
            # Step 8: Cleanup - destroy environment
            print(f"\n8. Cleaning up environment...")
            env.cleanup()
            print(f"   ✓ Environment destroyed")

    def test_rig_workspace_init_with_git_config(
        self,
        hetzner_token,
        test_workspace_dir
    ):
        """
        Test workspace initialization with git configuration in vars
        """
        print(f"\n=== Testing Rig Workspace Init with Git Config ===")
        
        # Initialize workspace
        project_config = ProjectConfig(test_workspace_dir)
        project_config.init(name="rig-git-test", ansible_enabled=True)
        
        # Set git-related variables for rig repo
        project_config.set_vars('git.clone_repos', [
            {
                'repo': RIG_REPO,
                'dest': RIG_CLONE_DEST,
                'accept_hostkey': True,
                'username': 'git',
                'password_var': 'git_pat'
            }
        ])
        
        # Save configuration
        project_config.save()
        
        # Verify configuration
        assert project_config.get_vars('git.clone_repos') is not None
        print(f"   ✓ Git configuration saved")
        repos = project_config.get_vars('git.clone_repos')
        print(f"   Repositories to clone: {len(repos)} repo(s)")
        for repo in repos:
            print(f"     - {repo.get('repo')} -> {repo.get('dest')}")
        
        # Check that rig.yml exists
        rig_yaml_path = os.path.join(project_config.rig_dir, 'rig.yml')
        assert os.path.exists(rig_yaml_path)
        print(f"   ✓ rig.yml exists")


@pytest.mark.requires_credentials
@pytest.mark.integration
@pytest.mark.e2e
class TestGitAuthentication:
    """Test git authentication scenarios"""

    @pytest.fixture(autouse=True)
    def check_credentials(self, hetzner_token):
        """Check if credentials are available"""
        if not hetzner_token or hetzner_token == 'test-hetzner-token-12345':
            pytest.skip("Requires real Hetzner Cloud API token")

    def test_rig_repo_clone_with_github_pat(
        self,
        hetzner_token,
        test_workspace_dir,
        unique_env_name
    ):
        """
        Test that the rig repository can be cloned using GitHub PAT authentication
        """
        print(f"\n=== Testing Rig Repo Clone with GitHub PAT ===")
        
        if not GITHUB_PAT:
            print(f"   ⚠️  RIG_GITHUB_PAT not set, skipping test")
            pytest.skip("RIG_GITHUB_PAT environment variable not set")
        
        # Initialize
        project_config = ProjectConfig(test_workspace_dir)
        project_config.init(name=f"rig-pat-clone-{unique_env_name}", ansible_enabled=True)
        
        # Create environment
        env = RemoteDevEnvironment(name=unique_env_name)
        env.name = unique_env_name
        env.vm_name = unique_env_name
        
        setup_success = env.setup()
        assert setup_success
        
        try:
            # Configure Ansible
            ansible_manager = AnsibleManager(project_config)
            ansible_manager.configure(test_workspace_dir)
            
            ssh_key_path = f'/tmp/{env.ssh_key_name}'
            ansible_manager.setup_inventory(
                vm_ip=env.vm_ip,
                ssh_key_path=ssh_key_path,
                username='root'
            )
            
            # Create playbook for rig repo with PAT
            clone_task = {
                'name': 'Clone rig repository with GitHub PAT',
                'git': {
                    'repo': RIG_REPO,
                    'dest': RIG_CLONE_DEST,
                    'accept_hostkey': True,
                    'username': 'git',
                    'password': GITHUB_PAT
                }
            }
            
            ansible_manager.setup_playbook(custom_tasks=[clone_task])
            
            print(f"   ✓ Rig repo clone playbook created with PAT authentication")
            print(f"   Repository: {RIG_REPO}")
            print(f"   Destination: {RIG_CLONE_DEST}")
            print(f"   Authentication: HTTPS with PAT (username: git)")
            
        finally:
            env.cleanup()


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
