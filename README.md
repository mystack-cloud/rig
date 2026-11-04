[![Tests](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml/badge.svg)](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml)
[![Coverage](https://img.shields.io/badge/coverage-27%25-red)](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml)

# rig

Remote environments on Hetzner for coding agents and humans.

```bash
pip install .
rig init
rig create
```

## Layout

`rig init` writes commit-friendly files into the current git project:

```text
rig.yml                 # workspace manifest (vm, agent, playbooks, repos)
.rig/
  system.yml            # auto-generated; code-server + agent + gh/glab
  workspace.yml         # custom; clone repos / project setup
  finalizers.yml        # custom; release off-VM resources before destroy
  vars.yml              # Ansible vars (vaulted secrets OK)
  requirements.yml      # Galaxy collections/roles
  AGENTS.md             # agent instructions
  .opencode/ | .claude/ # agent config + MCP
  inventory.yml         # generated at create/apply (not hashed)
```

`rig.yml` defaults (edit to configure):

```yaml
vm:
  type: cax11
  availability_zone: hel1
  image: ubuntu-24.04
agent:
  type: opencode          # or claude
playbooks:
  system: .rig/system.yml
  workspace: .rig/workspace.yml
  finalizers: .rig/finalizers.yml
```

Remote project files live under `/workspace` (not `/root`).

## Lifecycle

```text
init  →  create  →  attach | dev | apply  →  destroy
              ↓
         snapshots (reuse on next create)
```

| Stage | What happens |
| --- | --- |
| **init** | Scaffold `rig.yml` + `.rig/`. Collect Hetzner creds (`~/.rig/config`), repo PATs, agent choice/key. Idempotent: fills missing files only. |
| **create** | Boot VM (base image, or matching snapshot). Run `system.yml` then `workspace.yml` unless skipped by snapshot. Snapshot after each. Attach agent TTY unless `--no-attach`. |
| **apply** | Re-run `workspace.yml` (or `--system`) on an existing env. |
| **attach** | SSH TTY into `opencode` / `claude` on `/workspace`. |
| **dev** | Open VS Code/Cursor via Remote-SSH on `/workspace` (or `--browser` for code-server). |
| **destroy** | Run `finalizers.yml`, then delete VM / firewall / SSH key. |

Last env name is remembered in `/tmp/.rig/last-env` (used when `name` is omitted).

### Snapshots

| Boot source | Skips |
| --- | --- |
| `workspace-{name}-{hash}` | system + workspace |
| `rig-{version}` | system only |
| base image | nothing |

Workspace hash covers durable inputs (`rig.yml`, `workspace.yml`, `finalizers.yml`, vars, agent scaffolds, requirements). Excludes `inventory.yml`, auto-generated `system.yml`, and Galaxy installs.

Override with `--force-playbooks` / `--force-system` / `--force-workspace`. Clear with `rig cache clear` or `rig cache --system clear`.

## CLI

Global: `-v` debug · `-q` warnings/errors only · `--version`

| Command | Args | Notes |
| --- | --- | --- |
| `init` | | Interactive scaffold |
| `create` | `[--name]` `[--type]` `[--availability-zone]` `[--image]` `[--no-attach]` `[--force-playbooks\|--force-system\|--force-workspace]` | VM defaults from `rig.yml`; flags override. `--region` aliases `--availability-zone`. |
| `apply` | `[name]` `[--system]` | Default: workspace playbook |
| `attach` | `[name]` `[-c\|--continue]` `[-s\|--session ID]` `[--resume [ID]]` `[-- …agent args]` | Forwards flags to the agent |
| `dev` | `[name]` `[--path /workspace]` `[--browser]` `[--editor code\|cursor\|…]` | Remote-SSH by default |
| `list` | | Lists envs, or `No remote environments` |
| `destroy` | `[name …]` | Runs finalizers first; multi-name OK |
| `cache` | `[--system] clear` | Delete workspace or system snapshots |
| `encrypt` | `[--stdin-name VAR]` | Vault into `.rig/vars.yml` via `/tmp/.rig/.data-key` |
| `decrypt` | `[VAR]` | Decrypt from `.rig/vars.yml` |

## Ansible variables

Playbooks load `.rig/vars.yml` first, then runtime `rig_*` extras (`-e` JSON). Prefer `{{ rig_env_name }}` over hard-coding.

| Variable | Meaning |
| --- | --- |
| `rig_env_name` | Env / VM name (`dev-…`) |
| `rig_workspace_name` | `rig.yml` `name` |
| `rig_vm_name` | VM name (usually same as env) |
| `rig_vm_type` | Hetzner server type |
| `rig_availability_zone` | Hetzner location (`hel1`, …) |
| `rig_vm_image` | Boot image or snapshot id |
| `rig_vm_ip` | Public IPv4 |
| `rig_vm_id` | Hetzner server id |
| `rig_agent_type` | `opencode` or `claude` |
| `rig_workspace_root` | `/workspace` |
| `rig_version` | Installed `rig` package version |
| `rig_boot_source` | `base` \| `system` \| `workspace` |
| `rig_action` | `create` \| `apply` \| `destroy` |
| `rig_project_dir` | Local project path |
| `rig_ssh_key_name` / `rig_ssh_key_path` | Local key under `/tmp` |
| `rig_firewall_name` | Firewall resource name |
| `rig_repos` | `[{name, url, path}, …]` (no secrets) |
| `rig_git_providers` | e.g. `github`, `gitlab` |

Also available from `vars.yml` (defaults): `workspace_root`, `coding_agent_type`, `git.clone_repos`, vaulted PATs / `coding_agent_api_key`.

## Credentials

| Source | Purpose |
| --- | --- |
| `~/.rig/config` | Hetzner token (+ optional S3). Profile via `RIG_PROFILE` (default `default`). Env vars override. |
| `/tmp/.rig/.data-key` | Ansible vault password for `.rig/vars.yml` (tmpfs; cleared on reboot). |
| `/tmp/<env>-key` | Per-env SSH private key (needed for attach / apply / destroy finalizers). |

OpenCode without `coding_agent_api_key` uses free Zen models. With a key, config switches to the paid provider path.

## Tests

```bash
python -m pytest tests/ -m "not requires_credentials"
```

## License

MIT
