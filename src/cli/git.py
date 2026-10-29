"""
Git module for workspace project management

This module provides functionality for:
- Cloning git repositories on remote development environments
- Managing git configurations
- Handling authentication with PAT and SSH keys
"""

import os
import subprocess
from typing import Dict, List, Optional, TYPE_CHECKING
import yaml

if TYPE_CHECKING:
    from cli.remote_dev import ProjectConfig


class GitManager:
    """Manages git operations for workspace projects"""

    def __init__(self, project_config: Optional["ProjectConfig"] = None):
        self.project_config = project_config
        self.repos_to_clone: List[Dict] = []
        self.ssh_repos: List[Dict] = []
        self.git_config: Dict = {}

    def load_git_config(self, config_path: Optional[str] = None) -> bool:
        """Load git configuration from vars.yml"""
        try:
            if self.project_config is None:
                return False
            
            self.project_config.load()
            git_config = self.project_config.get_vars('git')
            
            if git_config:
                self.git_config = git_config
                self.repos_to_clone = git_config.get('clone_repos', [])
                self.ssh_repos = git_config.get('ssh_repos', [])
                return True
            
            return False
        except Exception as e:
            print(f"Warning: Could not load git config: {e}")
            return False

    def get_repos_to_clone(self) -> List[Dict]:
        """Get list of repositories to clone"""
        return self.repos_to_clone + self.ssh_repos

    def generate_clone_tasks(self) -> List[Dict]:
        """Generate Ansible tasks for cloning repositories"""
        tasks = []
        
        # Add SSH known hosts setup for public repos
        for repo in self.repos_to_clone:
            repo_url = repo.get('repo', '')
            if repo_url.startswith('https://') or repo_url.startswith('http://'):
                tasks.append({
                    'name': f'Add {self._get_hostname(repo_url)} SSH known hosts',
                    'shell': f'ssh-keyscan {self._get_hostname(repo_url)} >> ~/.ssh/known_hosts 2>/dev/null || true',
                    'changed_when': False,
                    'ignore_errors': True
                })
                break  # Only add once
        
        # Add clone tasks for public repos
        for repo in self.repos_to_clone:
            tasks.append({
                'name': f'Clone {self._get_repo_name(repo.get("repo", ""))}',
                'git': {
                    'repo': repo.get('repo'),
                    'dest': repo.get('dest'),
                    'accept_hostkey': repo.get('accept_hostkey', True),
                    'force': repo.get('force', False),
                    'version': repo.get('version', 'main')
                }
            })
        
        # Add clone tasks for SSH repos
        for repo in self.ssh_repos:
            tasks.append({
                'name': f'Clone {self._get_repo_name(repo.get("repo", ""))} (SSH)',
                'git': {
                    'repo': repo.get('repo'),
                    'dest': repo.get('dest'),
                    'accept_hostkey': repo.get('accept_hostkey', True),
                    'force': repo.get('force', False),
                    'version': repo.get('version', 'main'),
                    'ssh_key': repo.get('ssh_key', '/root/.ssh/id_rsa')
                }
            })
        
        return tasks

    def _get_hostname(self, repo_url: str) -> str:
        """Extract hostname from repository URL"""
        if repo_url.startswith('https://'):
            return repo_url.split('/')[2]
        elif repo_url.startswith('http://'):
            return repo_url.split('/')[2]
        elif repo_url.startswith('git@'):
            return repo_url.split(':')[1].split('/')[0]
        return 'github.com'

    def _get_repo_name(self, repo_url: str) -> str:
        """Extract repository name from URL"""
        # Remove .git suffix
        name = repo_url.rstrip('/')
        if name.endswith('.git'):
            name = name[:-4]
        
        # Get last part of URL
        if '/' in name:
            return name.split('/')[-1]
        return name

    def setup_git_environment(self, project_dir: str) -> bool:
        """Set up git environment for a project"""
        try:
            # Ensure .rig directory exists
            rig_dir = os.path.join(project_dir, '.rig')
            if not os.path.exists(rig_dir):
                os.makedirs(rig_dir)
            
            # Load existing config if available
            if self.project_config is None:
                from cli.remote_dev import ProjectConfig
                self.project_config = ProjectConfig(project_dir)
            
            self.project_config.load()
            
            # Set default git configuration if not present
            if not self.project_config.get_vars('git'):
                self.project_config.set_vars('git.clone_repos', [])
                self.project_config.set_vars('git.ssh_repos', [])
            
            return True
        except Exception as e:
            print(f"Error setting up git environment: {e}")
            return False

    def add_repo(self, repo_url: str, dest: str, is_ssh: bool = False) -> bool:
        """Add a repository to the clone list"""
        try:
            if self.project_config is None:
                from cli.remote_dev import ProjectConfig
                self.project_config = ProjectConfig()
            
            self.project_config.load()
            
            git_config = self.project_config.get_vars('git') or {}
            clone_repos = git_config.get('clone_repos', [])
            ssh_repos = git_config.get('ssh_repos', [])
            
            repo_config = {
                'repo': repo_url,
                'dest': dest,
                'accept_hostkey': True
            }
            
            if is_ssh:
                repo_config['ssh_key'] = '/root/.ssh/id_rsa'
                ssh_repos.append(repo_config)
            else:
                clone_repos.append(repo_config)
            
            git_config['clone_repos'] = clone_repos
            git_config['ssh_repos'] = ssh_repos
            
            self.project_config.set_vars('git', git_config)
            self.project_config.save()
            
            # Update internal state
            self.repos_to_clone = clone_repos
            self.ssh_repos = ssh_repos
            
            return True
        except Exception as e:
            print(f"Error adding repository: {e}")
            return False

    def remove_repo(self, repo_url: str) -> bool:
        """Remove a repository from the clone list"""
        try:
            if self.project_config is None:
                from cli.remote_dev import ProjectConfig
                self.project_config = ProjectConfig()
            
            self.project_config.load()
            
            git_config = self.project_config.get_vars('git') or {}
            clone_repos = git_config.get('clone_repos', [])
            ssh_repos = git_config.get('ssh_repos', [])
            
            # Remove matching repos
            clone_repos = [r for r in clone_repos if r.get('repo') != repo_url]
            ssh_repos = [r for r in ssh_repos if r.get('repo') != repo_url]
            
            git_config['clone_repos'] = clone_repos
            git_config['ssh_repos'] = ssh_repos
            
            self.project_config.set_vars('git', git_config)
            self.project_config.save()
            
            # Update internal state
            self.repos_to_clone = clone_repos
            self.ssh_repos = ssh_repos
            
            return True
        except Exception as e:
            print(f"Error removing repository: {e}")
            return False
