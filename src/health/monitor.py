"""
Health monitoring for remote development environments
"""

import psutil
import time
from datetime import datetime
from typing import Dict, Any

class HealthMonitor:
    """Monitors system resources and service status"""

    def __init__(self):
        self.last_check = None
        self.health_data = {}

    def get_system_health(self) -> Dict[str, Any]:
        """Collect system health metrics"""
        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=1)

            # Memory usage
            memory = psutil.virtual_memory()
            memory_percent = memory.percent

            # Disk usage
            disk = psutil.disk_usage('/')
            disk_percent = (disk.used / disk.total) * 100

            # Load average (Unix/Linux only)
            try:
                load_avg = psutil.getloadavg()
            except AttributeError:
                # Windows doesn't support getloadavg
                load_avg = (0, 0, 0)

            # Network connections
            connections = len(psutil.net_connections())

            # Uptime
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            uptime = datetime.now() - boot_time

            health_data = {
                'timestamp': datetime.now().isoformat(),
                'cpu_percent': cpu_percent,
                'memory_percent': memory_percent,
                'disk_percent': disk_percent,
                'load_average': load_avg,
                'network_connections': connections,
                'uptime_seconds': uptime.total_seconds(),
                'status': 'healthy' if cpu_percent < 80 and memory_percent < 80 else 'warning'
            }

            self.health_data = health_data
            self.last_check = datetime.now()

            return health_data

        except Exception as e:
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'status': 'error'
            }

    def get_service_status(self, services: list) -> Dict[str, Any]:
        """Check status of specified services"""
        service_statuses = {}

        for service in services:
            try:
                # This is a simplified check - in practice, you'd use system-specific
                # methods to check service status (systemctl, service, etc.)
                service_statuses[service] = 'running'  # Placeholder
            except Exception as e:
                service_statuses[service] = f'error: {str(e)}'

        return service_statuses

    def get_environment_health(self, environment_name: str) -> Dict[str, Any]:
        """Get health metrics for a specific environment"""
        # This would typically query the environment's health endpoint
        # For now, we'll return a placeholder
        return {
            'environment': environment_name,
            'timestamp': datetime.now().isoformat(),
            'status': 'healthy'
        }

# Example usage function
def monitor_and_report():
    """Example function to demonstrate health monitoring"""
    monitor = HealthMonitor()

    # Get system health
    health = monitor.get_system_health()
    print("System Health Report:")
    print(f"  Timestamp: {health.get('timestamp')}")
    print(f"  CPU Usage: {health.get('cpu_percent')}%")
    print(f"  Memory Usage: {health.get('memory_percent')}%")
    print(f"  Disk Usage: {health.get('disk_percent')}%")
    print(f"  Status: {health.get('status')}")

    # Check service status
    services = ['ssh', 'docker', 'vscode-server']
    service_status = monitor.get_service_status(services)
    print("\nService Status:")
    for service, status in service_status.items():
        print(f"  {service}: {status}")

if __name__ == "__main__":
    monitor_and_report()
