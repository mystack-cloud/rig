# MCP servers are declared once and rendered per Agent at authorize

A Workspace declares MCP servers once in `.rig/mcp.json`, in Claude's `.mcp.json` schema with `${NAME}` MCP variables. The authorize Stage renders each enabled Agent's own config from it and writes the resolved values into the Environment. Each MCP variable resolves to the vaulted variable with the same name, ignoring case, using an Ansible filter plugin that ships with rig.

## Considered Options

- **Per-agent files** (`.rig/.claude/mcp.json`, `.cursor/`, `.opencode/`): rejected. One server had to be written three times in three syntaxes, and an Agent enabled later silently lacked servers added earlier. They are retired; rig warns when one still declares servers.
- **Deploy MCP config in provision**: rejected. Provision is cached in a snapshot and `mcp.json` is not part of the snapshot hash, so edits would not reach new Environments. It also cannot hold secrets.
- **Leave placeholders in the Agent configs and write the values to an env file**: rejected. It only works if every launch path sources the file and each Agent expands its own syntax. OpenCode turns an unset variable into an empty string without a warning, and Cursor leaves it as literal text.
- **Resolve secrets in rig and pass them to Ansible as extra vars**: rejected. It would put secrets on the `ansible-playbook` command line.

## Consequences

- Secrets are written into the Agents' config files on the Environment (mode 0600), as well as sitting in the vault.
- A server whose variable has no value is skipped with a warning and does not fail the create. An invalid `.rig/mcp.json` does fail it.
- A same-name server is replaced whole; servers added by hand on the Environment are kept.
- Removing a server from `.rig/mcp.json` does not remove it from Environments that already have it.
- rig does not install a server's support files (for example a stdio wrapper script). The Workspace's own playbooks do.
