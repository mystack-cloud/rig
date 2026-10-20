"""
Provisioning provider interface
"""

from abc import ABC, abstractmethod
from typing import Dict, Any

class ProvisioningProvider(ABC):
    """Abstract base class for provisioning providers"""
    
    @abstractmethod
    def create_environment(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new environment"""
        pass
    
    @abstractmethod
    def destroy_environment(self, name: str) -> bool:
        """Destroy an environment"""
        pass
    
    @abstractmethod
    def start_environment(self, name: str) -> bool:
        """Start an environment"""
        pass
    
    @abstractmethod
    def stop_environment(self, name: str) -> bool:
        """Stop an environment"""
        pass
    
    @abstractmethod
    def get_environment_status(self, name: str) -> Dict[str, Any]:
        """Get environment status"""
        pass