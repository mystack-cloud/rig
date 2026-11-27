[![PyPI](https://img.shields.io/pypi/v/mystack-rig)](https://pypi.org/project/mystack-rig/)
[![Tests](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml/badge.svg)](https://github.com/mystack-cloud/rig/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/mystack-cloud/rig/graph/badge.svg)](https://codecov.io/gh/mystack-cloud/rig)
[![License: MIT](https://img.shields.io/github/license/mystack-cloud/rig)](https://github.com/mystack-cloud/rig/blob/master/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

# rig

Secure, fast, cost-efficient ephemeral remotes with full local parity—so coding agents and humans can develop and e2e-test for real.

```bash
pip install mystack-rig
rig init
rig create
```

## Architecture

Run `rig` from your machine, CI/CD, or a control plane: it provisions cloud VMs you can create and dispose anytime, and you work on `/workspace` via agent TTY or editor. SSH/HTTPS reaches VMs through the firewall; HTTPS reaches the provider. The CLI stores env state in S3-compatible object storage.

![rig architecture](docs/architecture.png)

## Cloud providers

| Provider | `host.provider` | Status |
| --- | --- | --- |
| [Hetzner Cloud](https://www.hetzner.com/cloud) | `hetzner` (default) | Supported (VMs, firewall, snapshots, S3-compatible object storage) |
| [AWS](https://aws.amazon.com/) | `aws` | Coming next |
| [GCP](https://cloud.google.com/) | `gcp` | Coming next |

`rig create` reads `host.provider`. Omit it and the provider is Hetzner. `--provider` overrides the file. `aws` and `gcp` are accepted as names and refused until those clouds can provision.

State storage (environment records, outputs) is separate from the VM provider and can be any of:

| Storage | Selected by | Status |
| --- | --- | --- |
| [Hetzner Object Storage](https://www.hetzner.com/storage/object-storage) | default endpoint (`https://fsn1.your-objectstorage.com`) | Supported |
| [Cloudflare R2](https://www.cloudflare.com/developer-platform/products/r2/) | `state_endpoint_url` = `https://<account-id>.r2.cloudflarestorage.com` | Supported |

See [State backend](#state-backend-hetzner-object-storage-or-cloudflare-r2) for the settings.

## Coding agents

Chosen at `rig init` (`agent.enabled` in `rig.yml`, same multi-select as git projects: numbers, names, or `all`). Every selected agent is installed and configured on the VM. When more than one is selected, init asks which is the default for `rig attach` (`agent.default`). Pass `--agent` to open another one.

| Agent | `agent.default` | API key |
| --- | --- | --- |
| [OpenCode](https://opencode.ai) | `opencode` (default) | Optional. One agent: `coding_agent_api_key`. Several agents: `opencode_api_key`. Enter skips and uses free Zen models |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `claude` | One agent: `coding_agent_api_key` (Anthropic). Several agents: `claude_api_key` |
| [Cursor CLI](https://cursor.com/docs/cli/overview) | `cursor` | One agent: `coding_agent_api_key` (`CURSOR_API_KEY`). Several agents: `cursor_api_key` |

Each selected agent is asked for its own key. Scaffolds land under `.rig/.opencode/`, `.rig/.claude/`, and `.rig/.cursor/` (plus shared `AGENTS.md`). MCP servers are declared once in `.rig/mcp.json` ([below](#mcp-servers)). Independent of `editor` (local IDE for `rig dev`).

## MCP servers

Declare an MCP server once in `.rig/mcp.json` and every enabled agent gets it. The file uses Claude's `.mcp.json` schema, and values may reference **MCP variables** as `${NAME}` or `${NAME:-default}`:

```json
{
  "mcpServers": {
    "awx": {
      "command": "python3",
      "args": ["-u", "/home/dev/awx-mcp-stdio.py"],
      "env": { "AWX_MCP_API_KEY": "${AWX_MCP_API_KEY}" }
    },
    "context7": {
      "url": "https://mcp.context7.com/mcp",
      "headers": { "CONTEXT7_API_KEY": "${CONTEXT7_API_KEY}" }
    }
  }
}
```

```bash
rig mcp add awx --env AWX_MCP_API_KEY -- python3 -u /home/dev/awx-mcp-stdio.py
rig mcp add context7 --url https://mcp.context7.com/mcp --header 'CONTEXT7_API_KEY=${CONTEXT7_API_KEY}'
rig mcp list      # servers, and whether each variable resolves
rig mcp check     # same, exit 1 on any problem (also lists ignored legacy files)
rig mcp remove awx
```

- Bare `--env KEY` means `KEY=${KEY}`; `--env KEY=value` stores a literal. `add` prompts for each `${NAME}` that has no default and no value yet, and vaults it (shared `vars.yml`; `--personal` for `vars.<identity>.yml`).
- A variable resolves to the vaulted variable with the same name, **ignoring case**: `AWX_MCP_API_KEY` finds `awx_mcp_api_key`. Two variables that differ only by case are an error. Values come from the vars files, inventory and extra vars, not from play-level `vars:`.
- The authorize Stage renders each enabled agent's own format and writes it into `~/.claude.json` (`mcpServers`), `~/.cursor/mcp.json` and `opencode.json` (`mcp`) on the Environment. A server with the same name is replaced; servers you added by hand there are kept. Secrets are written into those files (mode 0600).
- A server whose variable has no value is **skipped with a warning**; the create still succeeds. An invalid `.rig/mcp.json` fails the Stage.
- Fields rig does not translate (anything other than `type`, `command`, `args`, `env`, `url`, `headers`) are dropped with a warning.
- `rig mcp add/remove` only edit local files. Push to a running Environment with `rig apply --authorize`; `remove` leaves the old entry on Environments that already have it until they are recreated.
- Cursor's launcher passes `--approve-mcps`, so servers rig installs never prompt.
- Support files for a server (for example a stdio wrapper script) are not managed by rig: install them from your own playbook (provision, configure or authorize) and point `command` at that path.
- The per-agent `.rig/.claude|.cursor|.opencode/mcp.json` files from earlier versions are no longer deployed. If one still declares servers, `rig apply`/`create` and `rig mcp check` warn; move them to `.rig/mcp.json`.

## Live agents

`rig attach` runs the agent as a **live agent**: a process kept running on the Environment inside its own tmux session (`tmux -L rig`, with a config rig ships, so it never touches a tmux you use yourself). Closing the terminal or losing the connection only detaches; the agent keeps working, and the next `rig attach` joins it.

```bash
rig attach                       # start the live agent, or join the running one
#   leave: close the terminal, or detach with Ctrl+b d. Ctrl+C still goes to the agent.
rig attach --restart             # replace it with a fresh process (resumes the same conversation)
rig attach --no-persist          # old behaviour: the agent ends with the SSH session
```

- One live agent per Agent type per Environment. A second `rig attach` joins the first (both terminals see the same screen). Resume flags (`-c`, `--resume`, `--session`) apply only when a live agent is *started*; joining ignores them with a warning.
- A bare `rig attach` joins whichever live agent is running. `--agent` picks another.
- `rig inspect` lists live agents. `rig apply` and `rig delete` warn when there are any: a live agent keeps the credentials and MCP config it started with until `rig attach --restart`, and deleting the Environment ends it.
- If the agent exits while nobody is attached, tmux has logged its output on the VM, and the next `rig attach` recovers the resume id from that log.

### `rig exec`

Start an agent on a task and leave:

```bash
rig exec "fix the failing tests in /workspace/api"
rig exec @task.md                        # prompt from a file
echo "review the diff" | rig exec -      # prompt from stdin
rig exec --name dev-ab12cd34 --agent claude "..."
```

`rig exec` starts the live agent with the prompt, prints how to attach, and returns. It never creates an Environment and never waits.

- **Approvals are skipped by default** (`--permission-mode bypassPermissions` for Claude Code, `--force` for Cursor, `--auto` for OpenCode), because an unattended agent that stops at a prompt just sits there. Claude Code refuses bypass mode as root unless it is told it runs in a sandbox, so rig sets `IS_SANDBOX=1` for that run (harmless for the non-root workspace user), and authorize pre-accepts Claude's first-use warning dialog (`skipDangerousModePermissionPrompt`), whose default answer is "exit". The Environment has internet access, your tokens and root, so an agent reading untrusted text could be talked into acting on it. Use `--no-auto-approve` to supervise the run; it then pauses at each approval until you attach.
- **If a live agent is already running**, `rig exec` queues the prompt into it for Claude Code, which runs it after the current turn. rig does this only when Claude's hooks report it idle or working, never while it waits at an approval dialog (typing there would answer the dialog). If it is waiting, the state is unknown, or the agent is OpenCode or Cursor (queue behaviour not yet verified), `rig exec` refuses with the reason; `--restart` replaces the live agent instead. A queued prompt runs under the existing agent's permission mode.
- The agent's startup is checked: if it exits within a couple of seconds (for example no credentials), `rig exec` prints its output and fails.

The prompt reaches the VM on stdin and a private temporary file that is deleted when the agent starts, never on a command line.

### How it works

- tmux is installed at provision. The tmux config, two small helper scripts (`rig-agent-state`, `rig-agent-queue`) and Claude Code's state hooks are deployed by `authorize.yml`, so changing them never rebuilds the cached image. The hooks are merged into `~/.claude/settings.json` next to any hooks you added.
- The hooks record `idle`, `working`, `waiting` or `ended` in `/run/rig/agents/claude.state`; `rig-agent-queue` reads it before pasting.
- A failed agent leaves its (dead) pane so the error stays readable; the next `rig attach` or `rig exec` replaces it.
- Agent output is logged to `/var/log/rig/rig-<agent>.log` (the previous run's log is kept as `.prev`).

## IDEs

`rig dev` opens the remote `/workspace`. Default is local editor + Remote-SSH; `--browser` uses code-server on the VM.

| Mode | Clients |
| --- | --- |
| Remote-SSH (default) | VS Code (`code`), VS Code Insiders (`code-insiders`), Cursor (`cursor`), VSCodium (`codium`) |
| Browser (`--browser`) | code-server (password: vaulted `vscode_browser_password`) |

Chosen at `rig init` (`editor.enabled` in `rig.yml`, same multi-select as git projects: numbers, names, or `all`). When more than one is selected, init asks which is the default for `rig dev` (`editor.default`). Pass `--editor` to open another enabled one. These are local clients: nothing extra is installed on the VM, and each IDE downloads its own remote server the first time it connects. Browser login: `rig vars decrypt vscode_browser_password`.

### Dev Containers

`rig init` looks for Dev Containers in the Repos you select and, when it finds any, asks whether to enable support (default yes). It records the answer in `rig.yml` and doesn't ask again. To enable it by hand, set `editor.devcontainers: true`; then `rig dev` looks for Dev Containers in your Repos on the VM (each Repo's root and its immediate subfolders: `.devcontainer/devcontainer.json`, `.devcontainer.json`, `.devcontainer/<name>/devcontainer.json`). One match opens straight away, several give a numbered prompt, none opens the plain folder.

- `rig dev --target api` or `--target api:backend` skips the prompt; `--select` forces it. The last choice per Environment is remembered in `/tmp/.rig/targets/`.
- Not supported with `--browser`. Agents still run on the VM, not in the container.
- Docker is installed in the configure Stage and cached in the snapshot, so turning the flag on rebuilds the Workspace's snapshot once. New Workspaces get the task in `.rig/configure.yml`. **Existing Workspaces** must add it themselves, and existing Environments must be re-created:

```yaml
- name: Install Docker for Dev Containers
  include_role:
    name: geerlingguy.docker
  when: install_docker | default(false)
```

See [ADR 0005](docs/adr/0005-docker-in-configure-for-devcontainers.md).

### Workspace user

Rig doesn't make you work as root. Provision creates one non-root account, `dev` by default, with passwordless `sudo` and (once Docker is installed) the `docker` group. It is the account `rig attach`, `rig exec`, `rig dev`, the SSH alias, agents, code-server and Dev Containers all use, so VS Code remaps the container user's uid to match it and `/workspace` is writable from the container. Ansible still connects as root. Authorize copies root's `authorized_keys` to the user on every create.

Change the name in `rig.yml` (not `root`; a valid Linux user name):

```yaml
host:
  user: dev
```

Agent config (`~/.claude`, `~/.cursor`, `~/.config/opencode`), the git token file and the git author live in that user's home. `code_server_user` is no longer used. Playbooks get `rig_user` and `rig_home`. **Existing Workspaces:** `provision.yml` and `authorize.yml` are regenerated, but your `materialize.yml` is not: add `become_user: "{{ rig_user }}"` to its clone task and `owner`/`group: "{{ rig_user }}"` to the `/workspace` task. Existing Environments predate the user and must be re-created. See [ADR 0006](docs/adr/0006-workspace-user-instead-of-root.md).

## Layout

`rig init` writes commit-friendly files into the current directory (including a subdirectory of a git repo):

```text
rig.yml                 # workspace manifest (vm, agent, playbooks, repos)
.gitignore              # adds .rig/collections, roles, inventory (via init)
.rig/
  provision.yml         # managed; create base environment (cached)
  configure.yml         # user; image/service customizations (cached)
  authorize.yml         # managed; inject credentials (never snapshotted)
  materialize.yml       # user; populate /workspace after authorize
  finalize.yml          # user; destruction hooks before managed destroy
  destroy.yml           # managed; teardown system resources before VM delete
  vars.yml              # Shared Ansible vars (vaulted with shared data key)
  vars.<identity>.yml   # Personal secrets + master-key wraps (committed)
  requirements.yml      # Galaxy collections/roles
  local_roles/          # committed Ansible roles (searched first)
  AGENTS.md             # agent instructions
  .opencode/ | .claude/ | .cursor/  # agent config
  mcp.json              # user; MCP servers for every agent (see `rig mcp`)
  inventory.yml         # generated at create/apply (gitignored)
  collections/ roles/    # Galaxy installs (gitignored)
```

Put a committed role at `.rig/local_roles/<name>/` (`tasks/main.yml` and the usual role layout). `rig init` creates the directory. Galaxy roles install into `.rig/roles/` and stay gitignored. `.rig/local_roles` comes first in the roles path. Give a committed role a name that no Galaxy role uses: if both exist, Ansible's own lookup finds the one in `.rig/roles` first. An older `.rig/galaxy_roles/` directory is moved into `roles` on the next init or apply.

`rig.yml` defaults (edit to configure):

```yaml
host:
  provider: hetzner        # hetzner (default) | aws | gcp
  type: vm                 # kind of machine; vm is the default
  server_type: cax11       # Hetzner server type
  availability_zone: hel1
  image: ubuntu-24.04
firewall:
  mode: dynamic_add   # open | static | dynamic_replace | dynamic_add
  allowed_ips: []     # static allow-list; also merged into dynamic modes
  ports: []           # extra inbound ports; each may set mode and allowed_ips
agent:
  default: opencode       # default for `rig attach`; or claude | cursor
  enabled: [opencode]     # agents installed on the VM
editor:
  default: code           # default for `rig dev`
  enabled: [code]         # local IDEs `rig dev --editor` may open
  devcontainers: false    # true: `rig dev` opens a Dev Container (see below)
playbooks:
  provision: .rig/provision.yml
  configure: .rig/configure.yml
  authorize: .rig/authorize.yml
  materialize: .rig/materialize.yml
  finalize: .rig/finalize.yml
  destroy: .rig/destroy.yml
```

Firewall modes:

| Mode | Behavior |
| --- | --- |
| `open` | SSH/ICMP from anywhere (`0.0.0.0/0`) |
| `static` | Only `allowed_ips` (no client detection) |
| `dynamic_replace` | Set allow-list to current CLI public IP (+ `allowed_ips`); drops previous IPs |
| `dynamic_add` (default) | Keep existing allow-list entries; add current CLI public IP if missing |

Attach / apply / dev / delete refresh the firewall for dynamic modes when the client IP changes, and keep `ports` in sync.

`firewall.ports` opens extra inbound listeners. A bare port or a mapping with no `mode` uses `firewall.mode` and `firewall.allowed_ips`, same as SSH (TCP by default; use `{port, protocol}` for UDP). Set `mode` to `open`, `static`, `dynamic_replace`, or `dynamic_add` for that port only. Set `allowed_ips` to replace the firewall list for that port; omit it to inherit. `allowed_ips: []` on a `static` port does not fall back to world access. SSH (22) and ICMP stay on `firewall.mode`.

```yaml
ports:
  - 8080                         # firewall.mode + firewall.allowed_ips
  - port: 443
    mode: open                   # 0.0.0.0/0 and ::/0
  - port: 9090
    mode: static
    allowed_ips: ["198.51.100.10/32"]
```

Remote project files live under `/workspace`, owned by the workspace user (not in its home).

## Lifecycle

```text
init  →  create  →  attach | dev | apply  →  delete
              ↓
         snapshots (reuse on next create)

create playbooks:
  provision → configure → [snapshot] → authorize → materialize

destroy playbooks:
  finalize → destroy → (delete VM / firewall / key)
```

| Stage | File | Owner | Meaning |
| --- | --- | --- | --- |
| **provision** | `provision.yml` | managed | Create managed base environment |
| **configure** | `configure.yml` | user | Apply user image/service customizations (cached) |
| **authorize** | `authorize.yml` | managed | Inject credentials and establish access |
| **materialize** | `materialize.yml` | user | Populate `/workspace` |
| **finalize** | `finalize.yml` | user | Run user-defined destruction hooks |
| **destroy** | `destroy.yml` | managed | Remove managed environment (before CLI deletes VM) |

| Command | What happens |
| --- | --- |
| **init** | Scaffold `rig.yml` + `.rig/`. Collect Hetzner creds, repo PATs, agent choices and a key per agent. |
| **create** | Boot VM (base/system snapshot). Run provision → configure → authorize → materialize. Snapshot after **configure** only. |
| **apply** | Default: authorize → materialize. Flags: `--provision` / `--configure` / `--authorize` / `--materialize` / `--finalize` / `--destroy` (playbooks only; the VM stays). `--tags` is passed to every playbook in the run. |
| **attach** | SSH TTY into the agent's [live agent](#live-agents) on `/workspace` (started if needed, joined if running). `--agent` picks among installed agents. |
| **exec** | Start a live agent with a prompt and return; the work continues unattended. See [Live agents](#live-agents). |
| **dev** | Open VS Code/Cursor via Remote-SSH on `/workspace` (or `--browser` for code-server). |
| **delete** | Run finalize → destroy playbooks, then delete VM / firewall / SSH key. `--tags` is passed to both playbooks. |

Put secret-free installs in `configure.yml` (cached). Put clone/setup that needs credentials in `materialize.yml`.

Last env name is remembered in `/tmp/.rig/last-env` (used when `name` is omitted).

After you leave an agent TTY, rig captures the printed session / resume id (when the agent prints one) under `/tmp/.rig/sessions/<env>`. A later bare `rig attach` reopens that session (`--session` for OpenCode, `--resume` for Claude Code / Cursor). Pass `-c` / `--session` / `--resume` to override.

### Snapshots

| Boot source | Skips |
| --- | --- |
| `rig-{version}-{hash}` | provision + configure (`authorize` + `materialize` still run) |
| base image | nothing |

**System hash** = var map (`coding_agent_type`, `coding_agent_types`, `git_providers`, `code_server_*`, `workspace_root`) + `provision.yml` + `configure.yml` + `requirements.yml` (pins Galaxy role versions). Secrets, `authorize.yml`, `materialize.yml` and the role directories (`.rig/local_roles`, `.rig/roles`) are not included, so editing a role used from `materialize.yml` does not discard the cached image. If `configure.yml` uses a local role, run `rig cache clear` after changing it.

Override with `--force-playbooks` / `--force-system` (force provision+configure) / `--force-workspace` (force materialize).

`rig cache list|clear` — show snapshots (and for clear, confirm then delete):

| Flags | Selection |
| --- | --- |
| (default) | unused system (+ legacy workspace) for current project |
| `--system` | system only |
| `--workspace` | legacy workspace snapshots (current project) |
| `--all` | include current (not only unused) |
| `--all --system` / `--all --workspace` | all of that kind, including current |

`-y` skips the confirmation on clear.

## CLI

Global: `-v` playbook tasks and debug · `-vv`/`-vvv`/`-vvvv` ansible verbosity · `-q` warnings/errors only · `--version`. A failed task is shown without `-v`.

| Command | Args | Notes |
| --- | --- | --- |
| `init` | | Interactive scaffold |
| `create` | `[--name]` `[--provider]` `[--type]` `[--availability-zone]` `[--image]` `[--no-attach]` `[--force-playbooks\|--force-system\|--force-workspace]` | Host defaults from `rig.yml`. `--provider` defaults to `hetzner`. `--type` sets `host.server_type`. `host.type` defaults to `vm`. `--region` aliases `--availability-zone`. |
| `apply` | `[name]` `[--provision\|--configure\|--authorize\|--materialize\|--finalize\|--destroy]` `[--tags TAGS]` | Default: authorize then materialize. Each flag runs that playbook (`--provision` also runs configure and authorize; `--materialize` also runs authorize). `--finalize` and `--destroy` do not delete the VM. `--tags` limits every playbook in the run |
| `attach` | `[name]` `[-c\|--continue]` `[-s\|--session ID]` `[--resume [ID]]` `[--agent opencode\|claude\|cursor]` `[--restart]` `[--no-persist]` `[-- …agent args]` | Joins the running live agent, or starts one (reopening the last remembered session). `--restart` replaces it; `--no-persist` runs the agent as the SSH session's own process |
| `exec` | `PROMPT\|@FILE\|-` `[--name]` `[--agent opencode\|claude\|cursor]` `[--restart]` `[--no-auto-approve]` | Start a live agent with the prompt and return; approvals are skipped unless `--no-auto-approve`. Queues into a running Claude Code when that is safe |
| `ssh` | `[name]` `[-- command]` | Shell on the VM in `/workspace`. A command after `--` runs instead of a login shell |
| `dev` | `[name]` `[--path /workspace]` `[--browser]` `[--editor code\|cursor\|…]` | Remote-SSH by default; `--editor` opens another IDE from `editor.enabled`. Defaults to `editor.default` |
| `list` | | Lists envs, or `No remote environments` |
| `inspect` | `[name]` `[--json]` | Technical details of one env: stored record plus live Hetzner status, and its live agents when the VM is running. Falls back to stored data (flagged stale) if the API is unreachable. Data goes to stdout |
| `describe` | `[name]` `[--json]` `[--show-secrets]` | Outputs published by the env's playbooks (see below). Sensitive values are masked unless `--show-secrets` |
| `delete` | `[name …]` `[--tags TAGS]` | Runs finalize then destroy playbooks, then deletes the VM; multi-name OK. `--tags` limits both playbooks |
| `cache` | `list\|clear` `[--system\|--workspace] [--all] [-y]` | List or clear; unused by default, `--all` includes current |
| `vars encrypt` | `[--stdin-name VAR]` `[--personal]` | Vault into shared or identity vars (after master-key unlock) |
| `vars decrypt` | `[VAR]` | Decrypt from shared or identity vars |
| `vars list` | | List vaulted names (`shared` / `identity`) |
| `vars export-shared-key` | | Print shared data key for teammate onboarding |

## Ansible variables

Playbooks load `.rig/vars.yml` then `.rig/vars.{identity}.yml` (personal wins), then runtime `rig_*` extras. Prefer `{{ rig_env_name }}` over hard-coding.

| Variable | Meaning |
| --- | --- |
| `rig_env_name` | Env / VM name (`dev-…`) |
| `rig_workspace_name` | `rig.yml` `name` |
| `rig_vm_name` | VM name (usually same as env) |
| `rig_cloud_provider` | Cloud provider (`host.provider`, default `hetzner`) |
| `rig_vm_type` | Hetzner server type (`host.server_type`) |
| `rig_availability_zone` | Hetzner location (`hel1`, …) |
| `rig_vm_image` | Boot image or snapshot id |
| `rig_vm_ip` | Public IPv4 |
| `rig_vm_id` | Hetzner server id |
| `rig_agent_type` | Default attach agent (`opencode`, `claude`, or `cursor`) |
| `rig_agent_types` | Agents installed on the VM |
| `rig_workspace_root` | `/workspace` |
| `rig_version` | Installed `rig` package version |
| `rig_boot_source` | `base` \| `system` \| `workspace` |
| `rig_action` | `create` \| `apply` \| `destroy` (`rig delete` still sets `destroy`) |
| `rig_project_dir` | Local project path |
| `rig_ssh_key_name` / `rig_ssh_key_path` | Local key under `/tmp` |
| `rig_firewall_name` | Firewall resource name |
| `rig_repos` | `[{name, url, path}, …]` (no secrets) |
| `rig_git_providers` | e.g. `github`, `gitlab` |
| `rig_identity` | Developer identity (the `.rig/vars.{identity}.yml` handle) |
| `rig_git_author_name` / `rig_git_author_email` | Git author set on the VM (see [Git credentials and author](#git-credentials-and-author)) |

Also available from vars files (defaults): `workspace_root`, `coding_agent_type`, `coding_agent_types`, `git.clone_repos`. On `rig init`, git PATs and each coding agent API key default to **personal** storage in `vars.{identity}.yml` (you are asked to confirm once); shared secrets such as `vscode_browser_password` live in `vars.yml`.

`vscode_browser_password` is auto-generated (vaulted) for code-server browser login (`rig dev --browser`). Reveal with `rig vars decrypt vscode_browser_password` (also printed when opening browser mode).

## Playbook outputs

`rig describe` shows the values your playbooks publish. A playbook publishes them by writing YAML to the path in the `rig_outputs_file` extra var, which rig sets on every run. Add a task like this to any playbook, including custom ones:

```yaml
- name: Publish outputs
  delegate_to: localhost
  become: false
  ansible.builtin.copy:
    dest: "{{ rig_outputs_file }}"
    content: "{{ {'rig_outputs': rig_outputs, 'rig_outputs_sensitive': ['my_api_key']} | to_nice_yaml }}"
  vars:
    rig_outputs:
      vscode_url: "https://{{ rig_vm_ip }}:8443"
      my_api_key: "{{ some_secret }}"
```

- `rig_outputs` (required) is a dict of name to value. Without it the file is ignored.
- `rig_outputs_sensitive` (optional) lists extra keys to mask. Keys containing `password`, `secret`, `token` or `key` are always masked.
- After each successful create-side stage (`provision`, `configure`, `authorize`, `materialize`, `finalize`) rig shallow-merges the file into the stored outputs, so later runs overwrite only the keys they emit. Failed runs and `destroy` store nothing.
- Outputs are stored as written in the State bucket at `remote-dev/outputs/<name>.yml`, readable by anyone with bucket credentials. Masking is a display default, not access control. `rig delete` removes them.
- Environment names are unique across owners in a State bucket, and `create` rejects a name another owner already uses.

See `docs/adr/0002-outputs-as-separate-object-via-file-handoff.md`.

## Credentials

| Source | Purpose |
| --- | --- |
| `~/.rig/config` | Hetzner token (+ optional S3) and `identity`. Profile via `RIG_PROFILE`. Env vars override. |
| Personal master key | Prompted on init/unlock; **never stored**. Unwraps data keys from `vars.{identity}.yml`. |
| `rig_identity_data_key` / `rig_shared_data_key` | Master-key wraps inside `vars.{identity}.yml` (committed). |
| `/tmp/.rig/.personal-data-key` / `.shared-data-key` | Materialized Ansible vault passwords after unlock (tmpfs). |
| `/tmp/<env>-key` | Per-env SSH private key (attach / apply / delete finalizers). |

### State backend: Hetzner Object Storage or Cloudflare R2

State (environment records, outputs) lives in an S3-compatible bucket. The endpoint decides the provider: set `state_endpoint_url` in `~/.rig/config` (or `RIG_STATE_ENDPOINT_URL`) to your R2 endpoint, `https://<account-id>.r2.cloudflarestorage.com` (or the `.eu.` / `.fedramp.` variant), and use an R2 API token as `state_access_key` / `state_secret_key` (`RIG_STATE_ACCESS_KEY` / `RIG_STATE_SECRET_KEY`). The older `HETZNER_S3_*`, `S3_ENDPOINT_URL` and `hetzner_s3_*` / `s3_endpoint_url` names are still read as fallbacks. rig signs R2 requests with region `auto`. The Hetzner Cloud token is still required for the servers.

`RIG_IDENTITY` overrides the configured identity. Teammates joining an existing workspace paste the shared data key once (`rig vars export-shared-key` from an unlocked machine).

OpenCode without an API key uses free Zen models. With a key, config switches to the paid provider path. When several agents are selected, keys are `opencode_api_key`, `claude_api_key`, and `cursor_api_key` (`RIG_OPENCODE_API_KEY`, `RIG_ANTHROPIC_API_KEY`, `RIG_CURSOR_API_KEY`). A single agent still uses `coding_agent_api_key` (`RIG_CODING_AGENT_API_KEY`).

## Development

From a checkout, `pip install .` installs the same `rig` command. Use an editable install so the command keeps using this tree while you change it:

```bash
pip install -e .
rig --version
```

### Code style

[Ruff](https://docs.astral.sh/ruff/) does linting and formatting (configured in `pyproject.toml`: line length 120, double quotes, Python 3.10 syntax). CI runs `ruff check .` and `ruff format --check .`.

```bash
make format   # apply ruff's formatting and safe lint fixes
make lint     # the same checks as CI
```

`mypy src/` also runs in CI. Commits that only reformat code are listed in `.git-blame-ignore-revs`; run `git config blame.ignoreRevsFile .git-blame-ignore-revs` to make local `git blame` skip them (GitHub does this automatically).

## Tests

```bash
python -m pytest tests/ -m "not requires_credentials"
```

## Release

Push a version tag to publish that version to PyPI. The package version is the tag without an optional leading `v` (`v0.2.0` publishes `0.2.0`). The workflow reads the GitHub Actions secret `PYPI_API_TOKEN` (a PyPI API token, including the `pypi-` prefix).

## License

MIT

## Git credentials and author

**Provider tokens never go into repo URLs.** `authorize.yml` (managed) takes
`github_pat` / `gitlab_pat` from the vars files and writes them to
`~/.config/rig/git-provider.env` of the workspace user (mode `0600`). From there:

- login shells get `GH_TOKEN` / `GITLAB_TOKEN` via `/etc/profile.d`, and `rig attach`
  exports them to the agent process itself (it starts the agent in a non-login
  shell, which never reads `/etc/profile.d`), so `gh` and `glab` work out of the
  box in both;
- a system-wide git credential helper (`/usr/local/bin/rig-git-credential`)
  answers `github.com` / `gitlab.com` HTTPS requests from that same file (and, for a
  Repo with its own `password_var` such as `<project>_git_pat`, requests to that
  Repo's host from `~/.config/rig/git-hosts`), so
  `git clone`, `git push` and the `materialize.yml` clone task all use plain
  URLs (`https://github.com/org/repo.git`) and nothing sits in `.git/config`
  or `git remote -v`. Other users on the VM (besides root) can't read the file and get no
  credentials.

The `materialize.yml` clone task passes the token to git through an inline
helper fed from the task's environment (a per-repo `password_var` still wins over
`github_pat` / `gitlab_pat`), so it never touches the URL or disk. Projects
scaffolded by an older rig still have the token-in-URL clone task in their
`materialize.yml` (user-owned, never overwritten): copy the `vars:` and
`environment:` blocks of the current template and set `repo: "{{ item.repo }}"`.

**Git author.** A fresh VM has no git identity, so the first commit would fail
with *Author identity unknown*. `authorize.yml` sets `user.name` / `user.email`
(global) from `rig_git_author_name` / `rig_git_author_email`, which rig derives
from your identity: the identity as the name and `<identity>@rig.local` as the
email. Set `git_author_name` / `git_author_email` in `.rig/vars.yml`, or in your
personal `.rig/vars.{identity}.yml`, to use a real name and a GitHub-linked
email (the derived `rig.local` address is deliberately not a
`users.noreply.github.com` one, which GitHub would attribute to whoever owns
that handle).
