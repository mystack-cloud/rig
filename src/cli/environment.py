"""
Environment management for the CLI
"""

import requests
import json
import subprocess
import os
from typing import Optional

class EnvironmentManager:
    """Manages remote development environments"""
    
    def __init__(self, config):
        self.config = config
        self.base_url = config.get('control_plane_url', 'http://localhost:5000')
        
    def create_environment(self, name: str, provider: str = "ansible", template: Optional[str] = None):
        """Create a new environment"""
        url = f"{self.base_url}/environments"
        payload = {
            "name": name,
            "provider": provider,
            "template": template
        }
        
        try:
            response = requests.post(url, json=payload)
            if response.status_code == 201:
                print(f"Environment '{name}' created successfully")
                return response.json()
            else:
                print(f"Error creating environment: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            print(f"Failed to create environment: {str(e)}")
            return None
    
    def list_environments(self, status: Optional[str] = None):
        """List all environments"""
        url = f"{self.base_url}/environments"
        params = {}
        if status:
            params['status'] = status
            
        try:
            response = requests.get(url, params=params)
            if response.status_code == 200:
                environments = response.json()
                print("Environments:")
                for env in environments:
                    print(f"  - {env['name']} ({env['status']})")
                return environments
            else:
                print(f"Error listing environments: {response.status_code} - {response.text}")
                return []
        except Exception as e:
            print(f"Failed to list environments: {str(e)}")
            return []
    
    def destroy_environment(self, name: str):
        """Destroy an environment"""
        url = f"{self.base_url}/environments/{name}"
        
        try:
            response = requests.delete(url)
            if response.status_code == 200:
                print(f"Environment '{name}' destroyed successfully")
                return True
            else:
                print(f"Error destroying environment: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Failed to destroy environment: {str(e)}")
            return False
    
    def start_environment(self, name: str):
        """Start an environment"""
        url = f"{self.base_url}/environments/{name}/start"
        
        try:
            response = requests.put(url)
            if response.status_code == 200:
                print(f"Environment '{name}' started successfully")
                return True
            else:
                print(f"Error starting environment: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Failed to start environment: {str(e)}")
            return False
    
    def stop_environment(self, name: str):
        """Stop an environment"""
        url = f"{self.base_url}/environments/{name}/stop"
        
        try:
            response = requests.put(url)
            if response.status_code == 200:
                print(f"Environment '{name}' stopped successfully")
                return True
            else:
                print(f"Error stopping environment: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Failed to stop environment: {str(e)}")
            return False
    
    def connect_to_environment(self, name: str):
        """Connect to an environment using SSH"""
        url = f"{self.base_url}/environments/{name}/connect"
        
        try:
            response = requests.get(url)
            if response.status_code == 200:
                connection_info = response.json()
                ssh_command = connection_info.get('ssh_command')
                if ssh_command:
                    print(f"Connecting to environment '{name}'...")
                    # Execute SSH command
                    subprocess.run(ssh_command, shell=True)
                    return True
                else:
                    print("No SSH command available for this environment")
                    return False
            else:
                print(f"Error connecting to environment: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Failed to connect to environment: {str(e)}")
            return False