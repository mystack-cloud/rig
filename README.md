[![Tests](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml/badge.svg)](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml)
[![Coverage](https://img.shields.io/badge/coverage-27%25-red)](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml)

# rig

Remote development environments for agents and humans made easy.

## Features

- Initialize a multi-repo workspace or a single repo
- Create and destroy remote development environments on Hetzner
- Optional WireGuard access to upstream services

## Installation

```bash
pip install .
```

## Usage

### Initialize and manage an environment

`rig init` writes root `rig.yml` and `.rig/` into the current git project (so they can be committed). Re-running init is safe: it fills in any missing files without overwriting customized ones. It still offers sibling projects from the parent folder for inclusion, stores Hetzner credentials in `~/.rig/config` (AWS-style profiles, `default` by default) when they are not already set, asks for a GitHub/GitLab PAT per included HTTPS repo (and whether to store it as the default `github_pat` / `gitlab_pat` for reuse on other repos and reinits; otherwise `{project}_git_pat`), lets you choose a coding agent (`opencode` or `claude`), and asks for that agent's API key (`coding_agent_api_key`; Enter skips — OpenCode then defaults to free Zen models such as `opencode/deepseek-v4.1-flash-free`). Init scaffolds editable agent files under `.rig/` (`AGENTS.md`, `.opencode/` or `.claude/` config + MCP); `system.yml` installs the agent and deploys those files to the remote VM with `CODING_AGENT_API_KEY` set when a key was provided. Root `rig.yml` references `.rig/system.yml` (code-server / VS Code + selected agent, plus GitHub CLI `gh` and/or GitLab CLI `glab` when workspace repos use those hosts) and `.rig/workspace.yml` (project-specific playbook).

```bash
rig -v …                         # debug logs (IDs, playbook paths, SSH details)
rig -q …                         # warnings/errors only
rig init
rig create --name my-env         # create VM (reuses snapshots when possible), attach agent TTY
rig create --name my-env --no-attach  # provision only
rig create --force-playbooks     # ignore snapshot skip; re-run system + workspace
rig create --force-system        # re-run system.yml even on system/workspace snapshot boot
rig create --force-workspace     # re-run workspace.yml even on workspace snapshot boot
rig apply                        # re-run .rig/workspace.yml on last env (project-specific playbook)
rig apply my-env --system        # re-run .rig/system.yml instead
rig attach                       # reattach to last handled env (name remembered in /tmp/.rig/last-env)
rig attach my-env                # or pass the name explicitly
rig attach -c                    # continue last coding-agent session on that env
rig attach -- --session ses_abc  # forward arbitrary agent CLI args after --
rig dev                          # open VS Code/Cursor on /workspace via Remote-SSH
rig dev --browser                # open code-server on /workspace in the browser instead
rig list
rig destroy                       # destroy last handled env
rig destroy my-env
rig cache clear                   # delete workspace snapshots for this project
rig cache --system clear          # delete system.yml snapshots (rig-{version})
rig encrypt
rig decrypt [variable_name]
```

`rig encrypt` creates a temporary Ansible vault data key at `/tmp/.rig/.data-key` when missing (cleared on reboot), then continues: asks for a variable name and value, encrypts with `ansible-vault encrypt_string`, and writes the result into `.rig/vars.yml`. Default value input is one line. For multiline input use `rig encrypt --stdin-name my_var` and finish with Ctrl+D.

`rig decrypt` reads that same temporary data key automatically (no password file flag) and prints the plaintext for a variable from `.rig/vars.yml`. Ansible playbook runs also pick up `/tmp/.rig/.data-key` when present.

Environment variables still override the config file. Select another profile with `RIG_PROFILE`.

## Security Model

### Hetzner Cloud Token Authentication
This solution uses Hetzner Cloud Token for authentication. The token is used to authenticate with Hetzner Cloud, which then generates temporary S3-compatible credentials for storage operations.

### Credential Handling
- **Authentication**: Hetzner Cloud token is used to authenticate with Hetzner Cloud API
- **Credential Generation**: Hetzner generates temporary S3-compatible credentials
- **Exposure**: S3 credentials are displayed only once during generation
- **Security**: Generated credentials are handled by Hetzner's secure infrastructure

### Why This Approach?
1. **No Permanent Credentials**: S3 credentials are never permanently stored
2. **Single Exposure**: S3 credentials are shown only once during generation
3. **Temporary Nature**: Generated credentials expire automatically
4. **Isolated Access**: Token and credentials are separated for security

## End-to-End Testing

The project uses pytest for comprehensive test coverage.

### Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run tests excluding credential-dependent tests
python -m pytest tests/ -m "not requires_credentials"

# Run tests with verbose output
python -m pytest tests/ -v

# Measure line coverage for src and refresh the README badge
python -m pytest tests/ --cov=src --cov-report=term-missing --cov-report=xml \
  --ignore=tests/test_real_e2e_integration.py \
  --ignore=tests/test_real_e2e_storage.py \
  --ignore=tests/test_hetzner_storage.py \
  --ignore=tests/test_workspace_git_clone.py \
  --ignore=tests/test_real_e2e_vpn.py \
  --ignore=tests/test_real_e2e_vpn_tunnel.py
python scripts/coverage_badge.py --write
```

### Test Categories

- **Basic Storage Tests** (`test_storage_basic.py`) - Core storage operations
- **Credential Security Tests** (`test_credential_security.py`) - Security feature validation
- **Simulation Tests** (`test_simulation_storage.py`) - Workflow simulations
- **Real E2E Tests** (`test_real_e2e_storage.py`) - Real API integration (requires credentials)
- **Integration Tests** (`test_real_e2e_integration.py`) - Complete integration scenarios
- **Hetzner Tests** (`test_hetzner_storage.py`) - Hetzner-specific functionality

See the [pytest documentation](https://docs.pytest.org/) for more test options.

## Development

Install development dependencies:
```bash
pip install -r requirements.txt
```

Run tests:
```bash
python -m pytest tests/
```

Run E2E tests:
```bash
python -m pytest tests/ -m "not requires_credentials"
```

Lint code:
```bash
pylint src/
```

## License

MIT