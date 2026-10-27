# Git Clone Feature for Workspace

## Overview

The rig CLI now supports cloning git repositories as part of workspace project setup. This feature allows you to:

- Auto-clone public repositories on remote development environments
- Clone the rig repository itself for development
- Support HTTPS authentication with Personal Access Tokens (PAT) for private repositories
- Configure git clone operations in your project's `.rig/vars.yml` file
- Integrate with Ansible for provisioning and configuration

## GitHub PAT Authentication

### Environment Variables

The GitHub Personal Access Token (PAT) is read from environment variables:

- **GitHub Actions**: `RIG_GITHUB_PAT` (set in repository secrets)
- **Local Development**: Set in `.env` file (see `.env.example`)

### PAT Requirements

Your PAT should have the following scopes:
- `repo` - Full access to repositories (clone private repos)
- `public_repo` - Access to public repositories
- `read:org` - Read access to organizations

## Workspace Configuration

### Directory Structure

```
your-workspace/
├── .rig/
│   ├── rig.yml          # Project metadata
│   ├── setup.yml        # Setup configuration
│   ├── vars.yml         # Git clone configuration (secret variables)
│   ├── inventory.yml    # Ansible inventory (auto-generated)
│   ├── playbook.yml     # Ansible playbook (auto-generated)
│   └── requirements.txt # Ansible Galaxy roles
├── projects/
│   ├── project1/
│   └── project2/
└── rig.yml              # Workspace configuration
```

### Git Clone Configuration

Add git clone configuration to `.rig/vars.yml`:

```yaml
git:
  clone_repos:
    - repo: https://github.com/octocat/Hello-World.git
      dest: /root/Hello-World
      accept_hostkey: true
      version: main
    - repo: https://github.com/octocat/Spoon-Knife.git
      dest: /root/Spoon-Knife
      accept_hostkey: true
```

### SSH Authentication with PAT

For private repositories, use SSH authentication with a Personal Access Token:

```yaml
git:
  ssh_repos:
    - repo: git@github.com:your-org/private-repo.git
      dest: /root/private-repo
      accept_hostkey: true
      ssh_key: /root/.ssh/id_rsa
```

The SSH key path should be on the **remote VM**, not your local machine. The key will be used by Ansible to authenticate with git.

### PAT as Ansible Inventory Secret

During `rig init`, you'll be prompted for your PAT:

```bash
$ rig init
? Enter git PAT (Personal Access Token) for git authentication: [input hidden]
? Store PAT in .rig/.vault_password? (y/n): y
```

The PAT is stored encrypted using Ansible Vault:

```bash
$ cat .rig/.vault_password
your-encrypted-pat-here
```

## Usage

### 1. Initialize Workspace

```bash
# Initialize a new workspace
rig init --workspace

# Or initialize a specific project
rig init --project my-project
```

### 2. Configure Git Repositories

Add your GitHub PAT to environment variables:

```bash
# In .env file (local development)
RIG_GITHUB_PAT=your-github-pat-here

# Or export directly
export RIG_GITHUB_PAT=your-github-pat-here
```

Edit `.rig/vars.yml` to add your git repositories:

```yaml
git:
  clone_repos:
    - repo: https://github.com/mlewk/mystack.git
      dest: /root/mystack
      accept_hostkey: true
      username: git
      password_var: RIG_GITHUB_PAT
    - repo: https://github.com/octocat/Hello-World.git
      dest: /var/www/Hello-World
      accept_hostkey: true
```

### 3. Create Remote Development Environment

```bash
# Setup the environment
rig env setup --name my-dev-env

# Or with specific configuration
rig env setup --name my-dev-env --type cax11 --region hel1
```

### 4. Run Ansible Playbook to Clone Repositories

```bash
# The playbook is auto-generated with git clone tasks
# Run it with:
rig ansible run --playbook clone_repos.yml
```

## Ansible Integration

The rig CLI automatically generates Ansible playbooks with git clone tasks:

```yaml
---
- name: Clone git repositories
  hosts: remote_vm
  gather_facts: false
  tasks:
    - name: Add GitHub SSH known hosts
      shell: ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null || true
      changed_when: false

    - name: Clone Hello-World repository
      git:
        repo: https://github.com/octocat/Hello-World.git
        dest: /root/Hello-World
        accept_hostkey: true
        force: true
```

## CLI Commands

### `rig init`

Initialize a new workspace or project with git configuration.

```bash
# Interactive mode
rig init

# With options
rig init --workspace --git-pat "your-pat-here"
```

### `rig env setup`

Create a remote development environment with git repositories pre-cloned.

```bash
rig env setup --name my-env
```

### `rig ansible run`

Run an Ansible playbook (e.g., to clone repositories).

```bash
rig ansible run --playbook clone_repos.yml
```

## Security Considerations

1. **PAT Storage**: PAT is stored in `.rig/.vault_password` encrypted with Ansible Vault
2. **SSH Keys**: SSH keys for git authentication should be on the remote VM, not copied from local
3. **Repository URLs**: Use HTTPS with PAT for simple authentication, or SSH for more security
4. **Network Access**: Ensure the remote VM has network access to clone repositories

## Examples

### Clone the Rig Repository

This is the recommended way to clone the rig repository on your remote development environment:

```yaml
git:
  clone_repos:
    - repo: https://github.com/mlewk/mystack.git
      dest: /root/mystack
      accept_hostkey: true
      username: git
      password_var: RIG_GITHUB_PAT  # Read from environment variable
```

Then clone it using:

```bash
# Set your PAT in environment
export RIG_GITHUB_PAT=your-github-pat-here

# Initialize workspace with git config
rig init --project rig-dev

# Setup remote environment and clone rig repo
rig env setup --name rig-dev-env
rig ansible run --playbook clone_repos.yml
```

### Public Repository

```yaml
git:
  clone_repos:
    - repo: https://github.com/octocat/Hello-World.git
      dest: /var/www/Hello-World
      accept_hostkey: true
```

### Private Repository with PAT

```yaml
git:
  clone_repos:
    - repo: https://github.com/your-org/private-repo.git
      dest: /var/www/private-repo
      accept_hostkey: true
      username: your-username
      password_var: git_pat
```

### Multiple Repositories

```yaml
git:
  clone_repos:
    - repo: https://github.com/octocat/Hello-World.git
      dest: /var/www/Hello-World
      accept_hostkey: true
    - repo: https://github.com/octocat/Spoon-Knife.git
      dest: /var/www/Spoon-Knife
      accept_hostkey: true
    - repo: https://github.com/octocat/Hello-World.git
      dest: /var/www/Hello-World-backup
      accept_hostkey: true
      version: main
```

## Troubleshooting

### Git clone fails with "Host key verification failed"

Add `accept_hostkey: true` to your git configuration.

### PAT not working

Ensure your PAT has the correct permissions (repo, read:org) and is stored correctly in `.rig/.vault_password`.

### SSH key not found

Ensure the SSH key exists on the remote VM at the specified path, or use `accept_hostkey: true` with HTTPS.
