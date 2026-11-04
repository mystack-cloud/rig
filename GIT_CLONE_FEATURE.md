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
├── rig.yml              # Workspace/project config (playbooks, agent, repos)
├── .rig/
│   ├── system.yml       # Auto-generated system playbook (code-server, agent)
│   ├── workspace.yml    # Workspace-specific playbook (git clone, etc.)
│   ├── vars.yml         # Git clone config and vaulted secrets
│   ├── inventory.yml    # Ansible inventory (auto-generated)
│   └── requirements.yml # Ansible Galaxy collections/roles
└── projects/
    ├── project1/
    └── project2/
```

### Git Clone Configuration

Add git clone configuration to `.rig/vars.yml`:

```yaml
git:
  clone_repos:
    - repo: https://github.com/octocat/Hello-World.git
      dest: /workspace/Hello-World
      accept_hostkey: true
      version: main
    - repo: https://github.com/octocat/Spoon-Knife.git
      dest: /workspace/Spoon-Knife
      accept_hostkey: true
```

### SSH Authentication with PAT

For private repositories, use SSH authentication with a Personal Access Token:

```yaml
git:
  ssh_repos:
    - repo: git@github.com:your-org/private-repo.git
      dest: /workspace/private-repo
      accept_hostkey: true
      ssh_key: /root/.ssh/id_rsa
```

The SSH key path should be on the **remote VM**, not your local machine. The key will be used by Ansible to authenticate with git.

### PAT as Ansible Inventory Secret

`rig init` discovers nearby git projects, asks which ones to include, and asks for a GitHub or GitLab PAT per HTTPS repo. Enter reuses the PAT already given for that provider (stored as `github_pat` / `gitlab_pat` when shared). Repo-specific PATs are stored as `{project}_git_pat`. Tokens are encrypted with the temporary data key (`/tmp/.rig/.data-key`) into `.rig/vars.yml`, and each `git.clone_repos` entry gets a matching `password_var`. If `RIG_GITHUB_PAT` / `RIG_GITLAB_PAT` is set, pressing Enter on the first repo for that provider reuses it.

## Usage

### 1. Initialize Environment

```bash
# From a git repo or a parent folder that contains projects
rig init
```

It detects whether you are already inside a git repo. If so, it writes `rig.yml` / `.rig/` into that repo and offers sibling projects from the parent folder for inclusion. Otherwise it scans the current folder.

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
      dest: /workspace/mystack
      accept_hostkey: true
      username: git
      password_var: RIG_GITHUB_PAT
    - repo: https://github.com/octocat/Hello-World.git
      dest: /var/www/Hello-World
      accept_hostkey: true
```

### 3. Create Remote Development Environment

```bash
# Create the environment
rig create --name my-dev-env

# Or with specific configuration
rig create --name my-dev-env --type cax11 --region hel1
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
        dest: /workspace/Hello-World
        accept_hostkey: true
        force: true
```

## CLI Commands

### `rig init`

Interactively initialize a multi-repo environment from nearby git projects.

```bash
rig init
```

### `rig create`

Create a remote development environment with git repositories pre-cloned.

```bash
rig create --name my-env
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
      dest: /workspace/mystack
      accept_hostkey: true
      username: git
      password_var: RIG_GITHUB_PAT  # Read from environment variable
```

Then clone it using:

```bash
# Set your PAT in environment
export RIG_GITHUB_PAT=your-github-pat-here

# Initialize environment from nearby projects
rig init

# Setup remote environment and clone repos
rig create --name rig-dev-env
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
