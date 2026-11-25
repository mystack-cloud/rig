# Environments are worked on as a workspace user, not root

Provision creates one non-root account (`dev`, or `host.user` in `rig.yml`) with passwordless sudo. SSH, `rig attach`/`exec`/`dev`, agents, code-server and Dev Containers all use it, and it replaces code-server's separate `coder` user. Ansible keeps connecting as root, since the cloud image only has root and the key; tasks that write into the home directory finish with a `chown`, and the repo clones run `become_user` the workspace user.

## Why

VS Code remaps a Dev Container user's uid to the host user's, but skips it when the host user is root. Working as root left `/workspace` and `~/.ssh` owned by uid 0, so the container user could not write to them (pre-commit and saving files failed), and every project had to work around it.

## Considered Options

- **Phase 1: editor user only**: rejected. Agents, tokens and files would live under two accounts.
- **Keep `coder` plus a second user**: rejected. Two accounts is what makes uid 1000 belong to someone else.
- **Connect Ansible as the user**: rejected. The image has only root, and provisioning needs it anyway.

## Consequences

- `authorize.yml` copies root's `authorized_keys` to the user on every create (a snapshot would carry the old VM's key) and adds the user to the `docker` group when it exists.
- Playbooks use `rig_user` / `rig_home`, injected by rig. `provision.yml` and `authorize.yml` are regenerated; user-owned `materialize.yml` must be updated by hand (see the README).
- Environments created before this change have no such user and must be re-created. Changing `host.user` changes the system hash.
- docker-group membership is root-equivalent, so passwordless sudo adds little risk.
- Copying local `~/.ssh` into the user is not done; private keys on a cloud VM stay opt-in and out of scope.
