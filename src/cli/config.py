"""
Configuration management for the CLI
"""

import yaml
import os
from typing import Dict, Any

class Config:
    """Manages CLI configuration"""

    def __init__(self, config_file: str = "config.yaml"):
        self.config_file = config_file
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or use defaults"""
        default_config = {
            'control_plane_url': 'http://localhost:5000',
            'api_token': os.getenv('CONTROL_PLANE_TOKEN', ''),
            'default_provider': 'ansible'
        }

        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                file_config = yaml.safe_load(f) or {}
                # Merge with default config
                merged_config = default_config.copy()
                merged_config.update(file_config)
                return merged_config
        else:
            return default_config

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value"""
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value"""
        self.config[key] = value
        self._save_config()

    def _save_config(self) -> None:
        """Save configuration to file"""
        with open(self.config_file, 'w') as f:
            yaml.dump(self.config, f)
