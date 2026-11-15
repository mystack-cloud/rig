# Outputs are a separate per-name object, handed off from Ansible via a file

`rig describe` shows the Outputs that an Environment's playbooks publish. Playbooks write a `rig_outputs` dict to a controller-side file whose path rig passes as `rig_outputs_file`. After each successful non-destroy stage rig shallow-merges that file into the stored Outputs. The Outputs are stored as one object per Environment name at `remote-dev/outputs/<name>.yml` in the State bucket, separate from the Environment record.

## Considered Options

- **Parse Ansible stdout or a JSON callback**: rejected. Rig only gets a bool from `ansible-playbook` today, and parsing output is fragile and ties rig to a callback plugin.
- **Store Outputs inside the Environment record**: rejected. `_save_state` merges and rewrites the whole record on every update, and `list` scans the records prefix, so a growing and sensitive blob there raises the cost of every save and list.
- **Key Outputs by owner like records**: rejected. Outputs are a property of the Environment, so `describe <name>` returns one result for everyone in the Workspace and Profile.

## Consequences

- Playbook authors must opt in by writing `rig_outputs_file`. Rig's default playbooks ship a snippet that does this, and custom playbooks follow the documented convention.
- Environment names must be unique across owners within a State bucket, so `create` rejects a name that already exists under any owner.
- Outputs are stored as written. The bucket is shared by the whole team, so sensitive values are readable by anyone with bucket credentials. `describe` masks them by default (keys matching `password`, `secret`, `token` or `key`, or listed in `rig_outputs_sensitive`). Encrypting them with the vault shared key is a follow-up.
- `rig delete` removes the Outputs object together with the Environment record. A failed playbook writes nothing, and the destroy stage captures nothing.
