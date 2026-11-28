# Managed playbook content ships as bundled roles

The tasks behind rig's managed Stages used to be inline in the generated `provision.yml` and `authorize.yml` (hundreds of lines each). They now live in **bundled roles** inside the rig package (`src/cli/roles/`), and the generated playbooks only list which roles run. Each role covers one concern (`rig_code_server`, `rig_claude_code`, `rig_git`, ...) and has an entry point per Stage it takes part in (`tasks/install.yml` for provision, `tasks/authorize.yml` for authorize). Every task is tagged with its role, a `<role>_<group>` group and, where it applies, a cross-cutting tag, so users can skip any part with `--skip-tags` or `ansible.skip_tags` in `rig.yml`.

## Why

A fixed 800-line playbook could not be adjusted without rig changing it, and users had no way to leave out part of it (no GitLab CLI, no Cursor, no code-server). Roles give each concern a name, defaults and tags. They ship in the release package for now rather than as separate projects, so a rig release is the version of everything.

## Considered Options

- **Roles per Stage** (`rig_provision_code_server`, `rig_authorize_code_server`): rejected. A concern's install and credentials halves would live apart, and every Stage would repeat the same names. One role per concern with Stage entry points keeps them together.
- **Per-role boolean variables** (`rig_claude_code_enabled: false`): rejected. Tags already skip at every granularity; a second mechanism means two things to document and test.
- **Run bundled playbooks straight from the package, no `.rig/` files**: rejected. Users read `.rig/provision.yml` to see what runs, and the per-Stage file model, `--provision`/`--authorize` and the snapshot hash all assume the files.
- **Role tag on the `include_role`** (`apply: tags`): rejected. It cannot reach group tags, so `--tags rig_x_group` would select nothing. The includes are tagged `always` and the tags live inside the roles.

## Consequences

- Bundled roles are last on `ANSIBLE_ROLES_PATH`, so a Workspace role of the same name in `.rig/local_roles` shadows one. This is an escape hatch, not an interface.
- The provision snapshot hash covers every bundled role that has `tasks/install.yml`, minus its `authorize`, `materialize` and `destroy` task files, so editing credential logic never discards a cached image. It also covers any shadowing role and `ansible.skip_tags`. The first release with roles invalidates every cached snapshot once.
- `--tags` and `--skip-tags` on the command line make a run partial: `rig create --skip-tags` boots from the base image, and no such run saves a snapshot. A Workspace's own `ansible.skip_tags` builds its own cached image.
- Tag names become public once users pass them to `--skip-tags`; renaming one is a breaking change.
- `--skip-tags always` skips every role, because the includes carry that tag.
