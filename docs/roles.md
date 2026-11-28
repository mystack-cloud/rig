# Bundled roles

The playbooks rig generates (`provision.yml`, `authorize.yml`, `destroy.yml`, and the starting `materialize.yml`) are short: they call **bundled roles** that ship inside the rig package (`src/cli/roles/`). The roles do the work, so rig can change what an Environment gets without rewriting your `.rig/` files, and you can skip any part of it.

The playbooks are regenerated on every run. Do not edit them; skip or override through tags, variables or your own roles (below).

## Roles

One role per concern. A role has a *Stage entry point* for each Stage it takes part in: `tasks/install.yml` runs in **provision** (cached in a snapshot) `tasks/authorize.yml` runs in **authorize** (every create and apply, never cached), `tasks/materialize.yml` in **materialize** and `tasks/destroy.yml` in **destroy**.

| Role | Provision (`install`) | Authorize | Other Stage |
| --- | --- | --- | --- |
| `rig_base` | wait for cloud-init, apt cache, base packages | | |
| `rig_workspace_user` | the [Workspace user](../GLOSSARY.md), its sudo rule, `/workspace`, `AGENTS.md` | SSH key copied from root, docker group | |
| `rig_git` | GitHub CLI and GitLab CLI, for the providers the Workspace's repos use | git author, provider tokens, git credential helper | |
| `rig_live_agent` | tmux | tmux config, helper scripts, Claude's state hooks, the default Agent's env exports | |
| `rig_code_server` | code-server and its config directory | browser password, systemd unit | |
| `rig_opencode`, `rig_claude_code`, `rig_cursor_cli` | the Agent's CLI and config; only the enabled Agents run | API key and credentials file; Claude's onboarding seed; Cursor's workspace trust | |
| `rig_mcp` | | render `.rig/mcp.json` into each enabled Agent's config | |
| `rig_workspace_repos` | | | clone the Workspace's Repos (`git.clone_repos`) into `/workspace` (the **materialize** Stage) |
| `rig_destroy` | | | nothing yet: managed teardown goes here (the **destroy** Stage) |
| `rig_home_ownership` | hands `/home/<user>` to the Workspace user; always runs, last | same, after everything that wrote into it | |

Authorize runs the roles in dependency order: the Agent roles seed `~/.claude.json` and `settings.json`, then `rig_mcp` merges into them and `rig_live_agent` adds its hooks. Each Agent role resolves only its own API key, so skipping a role also skips its key.

## Tags

Every task has three kinds of tag:

1. **Role tag**: the role's name, e.g. `rig_claude_code`. Skips or selects the whole role.
2. **Group tag**: `<role>_<group>`, e.g. `rig_claude_code_install`, `rig_claude_code_config`, `rig_git_gh`, `rig_git_glab`, `rig_workspace_user_sudo`. Skips or selects one meaningful group of tasks.
3. **Cross-cutting tags**: `packages`, `credentials`, `agents`, `mcp`, `git`. Select a kind of work across roles.

`rig_mcp` has one group (`rig_mcp_servers`) because its steps share facts. `rig_home_ownership` is also tagged `always`, so it still runs when you use `--tags`. `--skip-tags always` skips it, and also skips every role (they are all included under `always`; the tags above only act inside them).

List the exact tags with `grep -rn "tags:" src/cli/roles/<role>/` or in the installed package; a test keeps them consistent.

## Skipping

```bash
rig create --skip-tags rig_cursor_cli            # per run
rig apply --provision --skip-tags rig_git_glab
rig delete --skip-tags cleanup
```

Make it permanent for the Workspace in `rig.yml`:

```yaml
ansible:
  skip_tags: [rig_git_glab, rig_code_server]
```

`rig.yml` skip tags and `--skip-tags` are added together and passed to every playbook rig runs.

### Skipping and snapshots

- `ansible.skip_tags` in `rig.yml` is part of the snapshot hash, so a Workspace that skips something gets its own cached image.
- A run limited on the command line (`--skip-tags`, or `apply --tags`) is partial by definition: `rig create --skip-tags` boots from the base image, and no run limited that way saves a snapshot.
- Skipping a role whose output later Stages need (for example `rig_workspace_user`) breaks those Stages. Skip with care.

## Materialize is yours

`materialize.yml` and `configure.yml` are user-owned: rig writes them once and never overwrites them. New Workspaces get a `materialize.yml` that calls `rig_workspace_repos`; add your own tasks after it. **Existing Workspaces** keep the long form they were generated with, which works as before. To adopt the role, replace its two tasks (`Ensure project workspace directory exists` and `Clone configured git repositories`) with:

```yaml
    - name: Workspace repos
      include_role:
        name: rig_workspace_repos
        tasks_from: materialize
      tags: [always]
```

`destroy.yml` is rig-owned and regenerated, like `provision.yml` and `authorize.yml`.

## Overriding a role

Bundled roles are searched last on the role path, after `.rig/local_roles` and `.rig/roles`. A role of the same name in `.rig/local_roles` replaces the bundled one. This is an escape hatch, not a stable interface: the role's variables and task layout may change between releases. A shadowing role in `local_roles` is part of the snapshot hash.

## Writing your own

Put roles in `.rig/local_roles/` and call them from `configure.yml` or `materialize.yml`, which you own. Name them anything but `rig_*`.
