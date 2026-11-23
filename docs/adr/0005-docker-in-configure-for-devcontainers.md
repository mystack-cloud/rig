# Docker for Dev Containers is installed in configure, behind an opt-in

When `editor.devcontainers` is true in `rig.yml`, the configure Stage installs Docker (the `geerlingguy.docker` role already in the default requirements), so it is baked into the snapshot and new Environments start with it ready. The flag is off by default and adds nothing to configure when false, so Workspaces that do not use Dev Containers keep their snapshots and pay nothing.

## Considered Options

- **Install Docker at materialize**: rejected. Materialize is not snapshot-cached, so it would reach already-running Environments, but every create would pay the Docker install time again. Dev Containers are used on every Environment of a Workspace that enables them, which is what the snapshot cache is for.
- **Install Docker unconditionally in configure**: rejected. It would change the system hash and invalidate every Workspace's snapshot, including those that never use Dev Containers.

## Consequences

- Enabling `editor.devcontainers` changes the system hash, so the Workspace's snapshot is rebuilt once.
- Environments created before the flag was enabled have no Docker. `rig dev` warns, names the cause, says to re-create the Environment, and falls back to a plain Remote-SSH open. An explicit `--target` fails instead.
- `.rig/configure.yml` is the Workspace's own file, so rig cannot add the Docker task to an existing one. New Workspaces get it from the default playbook, and existing ones add it by hand (see the README).
- Disabling the flag later does not remove Docker from existing Environments or snapshots.
- Dev Container support is editor-only. Agents still run on the host (ADR 0004), not in the container.
