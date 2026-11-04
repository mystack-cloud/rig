"""
Unit tests for git clone functionality in workspace

Tests the git clone logic without requiring actual remote VM or credentials
"""

import pytest
import os
import sys
import uuid
import tempfile
import shutil

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cli.remote_dev import ProjectConfig, AnsibleManager


class TestProjectConfigGitSetup:
    """Test project configuration with git settings"""

    def test_init_with_git_config(self, tmp_path):
        """Test initializing project with git configuration"""
        workspace_dir = str(tmp_path / f"test-git-init-{uuid.uuid4().hex[:8]}")
        
        project_config = ProjectConfig(workspace_dir)
        project_config.init(name="git-test", ansible_enabled=True)
        
        # Verify directory structure
        assert os.path.exists(project_config.rig_dir)
        assert os.path.exists(os.path.join(workspace_dir, 'rig.yml'))
        assert os.path.exists(os.path.join(project_config.rig_dir, 'system.yml'))
        assert os.path.exists(os.path.join(project_config.rig_dir, 'workspace.yml'))
        assert os.path.exists(os.path.join(project_config.rig_dir, 'vars.yml'))
        assert not os.path.exists(os.path.join(project_config.rig_dir, 'rig.yml'))
        assert not os.path.exists(os.path.join(project_config.rig_dir, 'setup.yml'))
        
        # Set git configuration
        project_config.set_vars('git.clone_repos', [
            {
                'repo': 'https://github.com/octocat/Hello-World.git',
                'dest': '/workspace/Hello-World',
                'accept_hostkey': True
            }
        ])
        
        # Verify git config was saved
        git_repos = project_config.get_vars('git.clone_repos')
        assert git_repos is not None
        assert len(git_repos) == 1
        assert git_repos[0]['repo'] == 'https://github.com/octocat/Hello-World.git'

    def test_multiple_repos_config(self, tmp_path):
        """Test configuring multiple repositories to clone"""
        workspace_dir = str(tmp_path / f"test-multi-repos-{uuid.uuid4().hex[:8]}")
        
        project_config = ProjectConfig(workspace_dir)
        project_config.init(name="multi-git-test", ansible_enabled=True)
        
        repos = [
            {
                'repo': 'https://github.com/octocat/Hello-World.git',
                'dest': '/workspace/Hello-World',
                'accept_hostkey': True
            },
            {
                'repo': 'https://github.com/octocat/Spoon-Knife.git',
                'dest': '/workspace/Spoon-Knife',
                'accept_hostkey': True
            }
        ]
        
        project_config.set_vars('git.clone_repos', repos)
        git_repos = project_config.get_vars('git.clone_repos')
        
        assert len(git_repos) == 2
        assert git_repos[0]['dest'] == '/workspace/Hello-World'
        assert git_repos[1]['dest'] == '/workspace/Spoon-Knife'


class TestAnsibleGitPlaybook:
    """Test Ansible playbook generation for git clone operations"""

    def test_generate_git_clone_task(self, tmp_path):
        """Test generating a git clone task for Ansible playbook"""
        workspace_dir = str(tmp_path / f"test-ansible-git-{uuid.uuid4().hex[:8]}")
        
        project_config = ProjectConfig(workspace_dir)
        project_config.init(name="git-playbook-test", ansible_enabled=True)
        
        ansible_manager = AnsibleManager(project_config)
        ansible_manager.configure(workspace_dir)
        
        # Create git clone task
        git_task = {
            'name': 'Clone Hello-World repository',
            'git': {
                'repo': 'https://github.com/octocat/Hello-World.git',
                'dest': '/workspace/Hello-World',
                'accept_hostkey': True,
                'force': True
            }
        }
        
        # Create playbook with git task
        ansible_manager.setup_playbook(custom_tasks=[git_task])
        
        # Verify playbook was created
        assert os.path.exists(ansible_manager.playbook_path)
        
        # Read and verify playbook content
        with open(ansible_manager.playbook_path, 'r') as f:
            playbook_content = f.read()
        
        assert 'git:' in playbook_content
        assert 'https://github.com/octocat/Hello-World.git' in playbook_content
        assert '/workspace/Hello-World' in playbook_content

    def test_generate_git_clone_with_ssh_known_hosts(self, tmp_path):
        """Test generating playbook with SSH known hosts setup"""
        workspace_dir = str(tmp_path / f"test-git-ssh-{uuid.uuid4().hex[:8]}")
        
        project_config = ProjectConfig(workspace_dir)
        project_config.init(name="git-ssh-test", ansible_enabled=True)
        
        ansible_manager = AnsibleManager(project_config)
        ansible_manager.configure(workspace_dir)
        
        # Tasks: get SSH known hosts + clone repo
        tasks = [
            {
                'name': 'Add GitHub SSH known hosts',
                'shell': 'ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null || true',
                'changed_when': False
            },
            {
                'name': 'Clone repository',
                'git': {
                    'repo': 'https://github.com/octocat/Hello-World.git',
                    'dest': '/workspace/Hello-World',
                    'accept_hostkey': True
                }
            }
        ]
        
        ansible_manager.setup_playbook(custom_tasks=tasks)
        
        with open(ansible_manager.playbook_path, 'r') as f:
            playbook_content = f.read()
        
        assert 'ssh-keyscan' in playbook_content
        assert 'github.com' in playbook_content
        assert 'git:' in playbook_content

    def test_setup_inventory_for_git_clone(self, tmp_path):
        """Test setting up inventory for VM with git clone capability"""
        workspace_dir = str(tmp_path / f"test-inventory-git-{uuid.uuid4().hex[:8]}")
        
        project_config = ProjectConfig(workspace_dir)
        project_config.init(name="git-inventory-test", ansible_enabled=True)
        
        ansible_manager = AnsibleManager(project_config)
        ansible_manager.configure(workspace_dir)
        
        # Create a temporary SSH key for testing
        ssh_key_dir = tmp_path / "ssh_keys"
        ssh_key_dir.mkdir()
        ssh_key_path = str(ssh_key_dir / "test_key")
        
        # Create a dummy SSH key file
        with open(ssh_key_path, 'w') as f:
            f.write("-----BEGIN OPENSSH PRIVATE KEY-----\n")
            f.write("b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz\n")
            f.write("c2gtZWQyNTUxOQAAACDHIz4VqyN+qXUwq5rLqHqHqHqHqHqHqHqHqHqHqHqHqA==\n")
            f.write("-----END OPENSSH PRIVATE KEY-----\n")
        
        vm_ip = "192.168.1.100"
        
        ansible_manager.setup_inventory(
            vm_ip=vm_ip,
            ssh_key_path=ssh_key_path,
            username='root'
        )
        
        assert os.path.exists(ansible_manager.inventory_path)
        
        with open(ansible_manager.inventory_path, 'r') as f:
            inventory_content = f.read()
        
        assert vm_ip in inventory_content
        assert ssh_key_path in inventory_content


class TestGitRepositoryConfig:
    """Test git repository configuration validation"""

    def test_valid_repo_config(self, tmp_path):
        """Test validating a valid git repository configuration"""
        workspace_dir = str(tmp_path / f"test-valid-repo-{uuid.uuid4().hex[:8]}")
        
        project_config = ProjectConfig(workspace_dir)
        project_config.init(name="valid-repo-test", ansible_enabled=True)
        
        valid_config = {
            'repo': 'https://github.com/octocat/Hello-World.git',
            'dest': '/workspace/Hello-World',
            'accept_hostkey': True,
            'version': 'main'
        }
        
        project_config.set_vars('git.repo_config', valid_config)
        repo_config = project_config.get_vars('git.repo_config')
        
        assert repo_config['repo'] == valid_config['repo']
        assert repo_config['dest'] == valid_config['dest']

    def test_repo_config_with_ssh(self, tmp_path):
        """Test git configuration with SSH authentication (PAT)"""
        workspace_dir = str(tmp_path / f"test-ssh-repo-{uuid.uuid4().hex[:8]}")
        
        project_config = ProjectConfig(workspace_dir)
        project_config.init(name="ssh-repo-test", ansible_enabled=True)
        
        # SSH-based config (for private repos with PAT)
        ssh_config = {
            'repo': 'git@github.com:octocat/Hello-World.git',
            'dest': '/workspace/Hello-World-ssh',
            'accept_hostkey': True,
            'ssh_key': '/root/.ssh/id_rsa'  # Path on remote VM
        }
        
        project_config.set_vars('git.ssh_repos', [ssh_config])
        ssh_repos = project_config.get_vars('git.ssh_repos')
        
        assert len(ssh_repos) == 1
        assert ssh_repos[0]['repo'].startswith('git@')

    def test_repo_config_with_branch(self, tmp_path):
        """Test git configuration with specific branch"""
        workspace_dir = str(tmp_path / f"test-branch-repo-{uuid.uuid4().hex[:8]}")
        
        project_config = ProjectConfig(workspace_dir)
        project_config.init(name="branch-repo-test", ansible_enabled=True)
        
        branch_config = {
            'repo': 'https://github.com/octocat/Hello-World.git',
            'dest': '/workspace/Hello-World',
            'version': 'main'  # Specific branch
        }
        
        project_config.set_vars('git.branch_repos', [branch_config])
        branch_repos = project_config.get_vars('git.branch_repos')
        
        assert branch_repos[0]['version'] == 'main'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
