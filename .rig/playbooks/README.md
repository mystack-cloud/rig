# Ansible Playbooks

This directory contains Ansible playbooks for provisioning remote development environments.

## Available Playbooks

### WireGuard VPN Gateway
- **File**: `vpn-gateway.yml`
- **Purpose**: Provisions a remote VM with WireGuard as a VPN gateway
- **Usage**: Creates a VPN gateway that allows secure access to development services

## Structure

```
.rig/playbooks/
├── vpn-gateway.yml    # WireGuard VPN gateway provisioning
└── README.md          # This file
```

## How It Works

The WireGuard VPN gateway playbook:
1. Installs WireGuard tools on the remote VM
2. Generates WireGuard key pair
3. Creates WireGuard interface configuration
4. Enables IP forwarding and masquerading
5. Starts the WireGuard service

## Configuration

The playbook uses the following variables (can be overridden):
- `vpn_gateway_ip`: VPN gateway IP address (default: 10.0.0.1/24)
- `vpn_gateway_port`: WireGuard listen port (default: 51820)
- `wg_interface_name`: WireGuard interface name (default: wg0)

## Usage

```bash
ansible-playbook .rig/playbooks/vpn-gateway.yml -i <inventory> --private-key ~/.ssh/id_rsa
```

## See Also

- [VPN CLI Commands](../src/cli/vpn.py) - Management commands for VPN tunnels
- [VPN E2E Tests](../tests/test_real_e2e_vpn_tunnel.py) - End-to-end VPN tunnel verification
