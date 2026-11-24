# Rig

Rig provisions and tracks remote development environments on Hetzner Cloud for a team.

## Language

**Profile**:
A named set of credentials for one Hetzner Cloud project, stored in the developer's personal rig config.
_Avoid_: Installation, account

**State bucket**:
The storage bucket holding environment state for one Profile within one workspace, shared by everyone working in that workspace.
_Avoid_: Production bucket, installation bucket

**Workspace**:
A folder containing `rig.yml` and a `.rig/` directory, which groups zero or more Repos and owns its own State buckets. It need not itself be a git repository.
_Avoid_: Project (ambiguous with a Hetzner Cloud project)

**Repo**:
A git repository included in a Workspace and cloned onto its Environments.
_Avoid_: Project

**Environment**:
A remote development machine provisioned by rig, owned by one identity and tracked in a State bucket.
_Avoid_: Instance, VM, server

**Outputs**:
The named values that an Environment's playbooks publish for the user (URLs, ports, credentials), kept alongside the Environment and shown by `rig describe`.
_Avoid_: Facts, results

**Test bucket**:
A throwaway state bucket used only by automated test runs.
_Avoid_: Dev bucket

**Agent**:
A coding CLI that rig installs on an Environment: OpenCode, Claude Code or Cursor CLI.
_Avoid_: Coding agent type, CLI, assistant

**Agent session**:
An Agent's saved conversation, which can be resumed by its id.
_Avoid_: Chat, thread

**Live agent**:
An Agent's running process on an Environment, kept alive when no terminal is connected. Attaching joins it; ending the Agent ends it.
_Avoid_: Background agent, daemon, detached session

**Stage**:
One of the playbook steps rig runs in order when creating an Environment: provision, configure, authorize, materialize, finalize. Only provision and configure are cached in a snapshot.
_Avoid_: Phase, step

**MCP server**:
A tool server declared once in the Workspace and installed into every Agent on its Environments.
_Avoid_: Plugin, tool, integration

**MCP variable**:
A named value an MCP server needs at runtime, resolved from the vaulted variable whose name matches it ignoring case.
_Avoid_: MCP secret, MCP env

**Dev Container**:
One `devcontainer.json` definition found in a Repo, describing the container its code is developed in.
_Avoid_: Project, devbox

**Dev target**:
The Repo and Dev Container pair that `rig dev` opens on an Environment.
_Avoid_: Project, selection
