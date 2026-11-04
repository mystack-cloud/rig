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
import subprocess

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cli.remote_dev import RemoteDevEnvironment, ProjectConfig, AnsibleManager


# Test repository to clone: the rig repository itself
RIG_REPO = "https://github.com/mystack-cloud/rig.git"
RIG_REPO_NAME = "rig"
RIG_CLONE_DEST = "/workspace/rig"
TEST_DIR_NAME = "test-rig-git-clone"

# Get GitHub PAT from environment (set in GitHub Actions as RIG_GITHUB_PAT)
GITHUB_PAT = os.getenv('RIG_GITHUB_PAT', None)


def _require_github_pat():
    if not GITHUB_PAT or GITHUB_PAT == 'ghp_test123':
        pytest.skip("Requires a real GitHub PAT (set RIG_GITHUB_PAT)")
    return GITHUB_PAT


def _clone_tasks():
    """Playbook tasks that install git and clone the repo with a PAT extra-var."""
    return [
        {
            'name': 'Wait for cloud-init to finish',
            'command': 'cloud-init status --wait',
            'changed_when': False,
            'failed_when': False,
        },
        {
            'name': 'Install git',
            'apt': {
                'name': 'git',
                'state': 'present',
                'update_cache': True,
            },
            'register': 'git_install',
            'retries': 12,
            'delay': 10,
            'until': 'git_install is success',
        },
        {
            'name': f'Clone {RIG_REPO_NAME} repository with GitHub PAT',
            'git': {
                'repo': "https://git:{{ git_pat }}@github.com/mystack-cloud/rig.git",
                'dest': RIG_CLONE_DEST,
                'version': 'master',
                'force': True,
            },
        },
    ]


def _assert_repo_cloned(vm_ip, ssh_key_path):
    check_git_dir = subprocess.run(
        [
            'ssh', '-i', ssh_key_path,
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'IdentitiesOnly=yes',
            '-o', 'BatchMode=yes',
            f'root@{vm_ip}',
            f'test -d {RIG_CLONE_DEST}/.git',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert check_git_dir.returncode == 0, (
        f"Repository was not cloned to {RIG_CLONE_DEST}: {check_git_dir.stderr}"
    )


@pytest.fixture
def test_workspace_dir(tmp_path):
    """Create a test workspace directory"""
    workspace_dir = tmp_path / TEST_DIR_NAME
    workspace_dir.mkdir()
    return str(workspace_dir)


@pytest.fixture
def unique_env_name(hetzner_token):
    """Generate a unique environment name that Hetzner accepts as a hostname."""
    timestamp = int(time.time())
    return f"rig-git-test-{hetzner_token[:8].lower()}-{timestamp}"


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
        git_pat = _require_github_pat()
        print(f"\n=== Testing Rig Repo Clone on Remote Dev Environment ===")
        print(f"Workspace: {test_workspace_dir}")
        print(f"Environment: {unique_env_name}")
        print(f"Repository: {RIG_REPO}")

        project_config = ProjectConfig(test_workspace_dir)
        project_config.init(name=unique_env_name, ansible_enabled=True)
        assert os.path.exists(os.path.join(test_workspace_dir, 'rig.yml'))
        assert os.path.exists(os.path.join(project_config.rig_dir, 'system.yml'))
        assert os.path.exists(os.path.join(project_config.rig_dir, 'workspace.yml'))

        env = RemoteDevEnvironment(name=unique_env_name)
        env.name = unique_env_name
        env.vm_name = unique_env_name
        assert env.setup(), "Environment setup should succeed"
        assert env.vm_id is not None
        assert env.vm_ip is not None

        try:
            ansible_manager = AnsibleManager(project_config)
            ansible_manager.configure(test_workspace_dir)
            ssh_key_path = f'/tmp/{env.ssh_key_name}'
            ansible_manager.setup_inventory(
                vm_ip=env.vm_ip,
                ssh_key_path=ssh_key_path,
                username='root'
            )
            ansible_manager.setup_playbook(custom_tasks=_clone_tasks())
            assert os.path.exists(ansible_manager.playbook_path)

            playbook_content = open(ansible_manager.playbook_path).read()
            assert git_pat not in playbook_content
            playbook_ok = ansible_manager.run_playbook(extra_vars={
                'ansible_ssh_private_key_file': ssh_key_path,
                'ansible_user': 'root',
                'git_pat': git_pat,
            })
            assert playbook_ok, "Playbook execution should succeed"
            _assert_repo_cloned(env.vm_ip, ssh_key_path)
        finally:
            env.cleanup()

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
        
        # Check that root rig.yml exists
        rig_yaml_path = os.path.join(test_workspace_dir, 'rig.yml')
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
        git_pat = _require_github_pat()

        project_config = ProjectConfig(test_workspace_dir)
        project_config.init(name=unique_env_name, ansible_enabled=True)

        env = RemoteDevEnvironment(name=unique_env_name)
        env.name = unique_env_name
        env.vm_name = unique_env_name
        assert env.setup()

        try:
            ansible_manager = AnsibleManager(project_config)
            ansible_manager.configure(test_workspace_dir)
            ssh_key_path = f'/tmp/{env.ssh_key_name}'
            ansible_manager.setup_inventory(
                vm_ip=env.vm_ip,
                ssh_key_path=ssh_key_path,
                username='root'
            )
            ansible_manager.setup_playbook(custom_tasks=_clone_tasks())
            playbook_ok = ansible_manager.run_playbook(extra_vars={
                'ansible_ssh_private_key_file': ssh_key_path,
                'ansible_user': 'root',
                'git_pat': git_pat,
            })
            assert playbook_ok, "Playbook execution should succeed"
            _assert_repo_cloned(env.vm_ip, ssh_key_path)
        finally:
            env.cleanup()


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
