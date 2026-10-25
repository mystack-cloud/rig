"""
Session management for collaborative environments
"""

import requests
import json
import subprocess
import os
from typing import Optional, Dict, Any, List
from datetime import datetime


class SessionManager:
    """Manages collaborative sessions for environments"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.base_url = config.get('control_plane_url', 'http://localhost:5000')

    def create_session(self, environment_name: str, session_name: Optional[str] = None) -> bool:
        """Create a new collaborative session"""
        url = f"{self.base_url}/sessions"
        payload = {
            "environment": environment_name,
            "name": session_name or f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        }

        try:
            response = requests.post(url, json=payload)
            if response.status_code == 201:
                print(f"Session '{session_name}' created successfully for environment '{environment_name}'")
                return True
            else:
                print(f"Error creating session: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Failed to create session: {str(e)}")
            return False

    def list_sessions(self, environment_name: Optional[str] = None) -> List[str]:
        """List active sessions"""
        url = f"{self.base_url}/sessions"
        params = {}
        if environment_name:
            params['environment'] = environment_name

        try:
            response = requests.get(url, params=params)
            if response.status_code == 200:
                sessions = response.json()
                print("Active Sessions:")
                for session in sessions:
                    print(f"  - {session['name']} (Environment: {session['environment']}, Status: {session['status']})")
                return sessions
            else:
                print(f"Error listing sessions: {response.status_code} - {response.text}")
                return []
        except Exception as e:
            print(f"Failed to list sessions: {str(e)}")
            return []

    def join_session(self, session_name: str, user_id: str) -> bool:
        """Join an existing collaborative session"""
        url = f"{self.base_url}/sessions/{session_name}/join"
        payload = {
            "user_id": user_id
        }

        try:
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                print(f"Successfully joined session '{session_name}'")
                return True
            else:
                print(f"Error joining session: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Failed to join session: {str(e)}")
            return False

    def leave_session(self, session_name: str, user_id: str) -> bool:
        """Leave a collaborative session"""
        url = f"{self.base_url}/sessions/{session_name}/leave"
        payload = {
            "user_id": user_id
        }

        try:
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                print(f"Successfully left session '{session_name}'")
                return True
            else:
                print(f"Error leaving session: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Failed to leave session: {str(e)}")
            return False

    def get_session_info(self, session_name: str) -> Dict[str, Any]:
        """Get detailed information about a session"""
        url = f"{self.base_url}/sessions/{session_name}"

        try:
            response = requests.get(url)
            if response.status_code == 200:
                session_info = response.json()
                print(f"Session Info for '{session_name}':")
                for key, value in session_info.items():
                    print(f"  {key}: {value}")
                return session_info
            else:
                print(f"Error getting session info: {response.status_code} - {response.text}")
                return {}
        except Exception as e:
            print(f"Failed to get session info: {str(e)}")
            return {}
