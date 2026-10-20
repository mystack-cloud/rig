"""
Ansible provisioning provider implementation
"""

import subprocess
import json
import os
from typing import Dict, Any
from . import ProvisioningProvider

class AnsibleProvider(ProvisioningProvider):
    """Provisioning provider using Ansible"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.playbook_dir = config.get('playbook_dir', './playbooks')
        
    def create_environment(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new environment using Ansible"""
        try:
            # Ensure playbook directory exists
            os.makedirs(self.playbook_dir, exist_ok=True)
            
            # Generate playbook file
            playbook_path = os.path.join(self.playbook_dir, f"{name}.yml")
            self._generate_playbook(name, config, playbook_path)
            
            # Execute Ansible playbook
            cmd = [
                'ansible-playbook', 
                playbook_path,
                '--extra-vars', f"name={name}",
                '-v'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            return {
                'success': True,
                'message': f"Environment '{name}' created successfully",
                'output': result.stdout
            }
        except subprocess.CalledProcessError as e:
            return {
                'success': False,
                'message': f"Failed to create environment '{name}': {e.stderr}",
                'error': str(e)
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Unexpected error creating environment '{name}': {str(e)}",
                'error': str(e)
            }
    
    def destroy_environment(self, name: str) -> bool:
        """Destroy an environment using Ansible"""
        try:
            # Generate destroy playbook file
            playbook_path = os.path.join(self.playbook_dir, f"destroy_{name}.yml")
            self._generate_destroy_playbook(name, playbook_path)
            
            # Execute Ansible destroy playbook
            cmd = [
                'ansible-playbook', 
                playbook_path,
                '--extra-vars', f"name={name}",
                '-v'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            return {
                'success': True,
                'message': f"Environment '{name}' destroyed successfully",
                'output': result.stdout
            }
        except subprocess.CalledProcessError as e:
            return {
                'success': False,
                'message': f"Failed to destroy environment '{name}': {e.stderr}",
                'error': str(e)
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Unexpected error destroying environment '{name}': {str(e)}",
                'error': str(e)
            }
    
    def start_environment(self, name: str) -> bool:
        """Start an environment using Ansible"""
        try:
            # Generate start playbook file
            playbook_path = os.path.join(self.playbook_dir, f"start_{name}.yml")
            self._generate_start_playbook(name, playbook_path)
            
            # Execute Ansible start playbook
            cmd = [
                'ansible-playbook', 
                playbook_path,
                '--extra-vars', f"name={name}",
                '-v'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            return {
                'success': True,
                'message': f"Environment '{name}' started successfully",
                'output': result.stdout
            }
        except subprocess.CalledProcessError as e:
            return {
                'success': False,
                'message': f"Failed to start environment '{name}': {e.stderr}",
                'error': str(e)
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Unexpected error starting environment '{name}': {str(e)}",
                'error': str(e)
            }
    
    def stop_environment(self, name: str) -> bool:
        """Stop an environment using Ansible"""
        try:
            # Generate stop playbook file
            playbook_path = os.path.join(self.playbook_dir, f"stop_{name}.yml")
            self._generate_stop_playbook(name, playbook_path)
            
            # Execute Ansible stop playbook
            cmd = [
                'ansible-playbook', 
                playbook_path,
                '--extra-vars', f"name={name}",
                '-v'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            return {
                'success': True,
                'message': f"Environment '{name}' stopped successfully",
                'output': result.stdout
            }
        except subprocess.CalledProcessError as e:
            return {
                'success': False,
                'message': f"Failed to stop environment '{name}': {e.stderr}",
                'error': str(e)
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Unexpected error stopping environment '{name}': {str(e)}",
                'error': str(e)
            }
    
    def get_environment_status(self, name: str) -> Dict[str, Any]:
        """Get environment status using Ansible"""
        try:
            # Generate status playbook file
            playbook_path = os.path.join(self.playbook_dir, f"status_{name}.yml")
            self._generate_status_playbook(name, playbook_path)
            
            # Execute Ansible status playbook
            cmd = [
                'ansible-playbook', 
                playbook_path,
                '--extra-vars', f"name={name}",
                '-v'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            return {
                'success': True,
                'message': f"Environment '{name}' status retrieved successfully",
                'output': result.stdout
            }
        except subprocess.CalledProcessError as e:
            return {
                'success': False,
                'message': f"Failed to get status for environment '{name}': {e.stderr}",
                'error': str(e)
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Unexpected error getting status for environment '{name}': {str(e)}",
                'error': str(e)
            }
    
    def _generate_playbook(self, name: str, config: Dict[str, Any], path: str):
        """Generate Ansible playbook for environment creation"""
        playbook_content = f"""
---
- hosts: localhost
  gather_facts: false
  vars:
    environment_name: "{name}"
  tasks:
    - name: Create environment directory
      file:
        path: "/tmp/{{{{ environment_name }}}}"
        state: directory
        mode: '0755'
    
    - name: Print message
      debug:
        msg: "Environment {{{{ environment_name }}}} created"
"""
        with open(path, 'w') as f:
            f.write(playbook_content)
    
    def _generate_destroy_playbook(self, name: str, path: str):
        """Generate Ansible playbook for environment destruction"""
        playbook_content = f"""
---
- hosts: localhost
  gather_facts: false
  vars:
    environment_name: "{name}"
  tasks:
    - name: Remove environment directory
      file:
        path: "/tmp/{{{{ environment_name }}}}"
        state: absent
    
    - name: Print message
      debug:
        msg: "Environment {{{{ environment_name }}}} destroyed"
"""
        with open(path, 'w') as f:
            f.write(playbook_content)
    
    def _generate_start_playbook(self, name: str, path: str):
        """Generate Ansible playbook for environment start"""
        playbook_content = f"""
---
- hosts: localhost
  gather_facts: false
  vars:
    environment_name: "{name}"
  tasks:
    - name: Start environment
      debug:
        msg: "Environment {{{{ environment_name }}}} started"
"""
        with open(path, 'w') as f:
            f.write(playbook_content)
    
    def _generate_stop_playbook(self, name: str, path: str):
        """Generate Ansible playbook for environment stop"""
        playbook_content = f"""
---
- hosts: localhost
  gather_facts: false
  vars:
    environment_name: "{name}"
  tasks:
    - name: Stop environment
      debug:
        msg: "Environment {{{{ environment_name }}}} stopped"
"""
        with open(path, 'w') as f:
            f.write(playbook_content)
    
    def _generate_status_playbook(self, name: str, path: str):
        """Generate Ansible playbook for environment status"""
        playbook_content = f"""
---
- hosts: localhost
  gather_facts: false
  vars:
    environment_name: "{name}"
  tasks:
    - name: Get environment status
      debug:
        msg: "Environment {{{{ environment_name }}}} is running"
"""
        with open(path, 'w') as f:
            f.write(playbook_content)