# Per-workspace, per-Profile generated state bucket in vars.yml

The fixed default bucket name `rig` collided with other Hetzner customers, because bucket names are unique across the whole location. Each Profile (one Hetzner Cloud project) in each Workspace now gets a generated `rig-{8 hex}` State bucket. The name is recorded in `.rig/vars.yml` under `rig_state_buckets`, keyed by Profile name, so the whole team shares it. It is not stored in the developer's `~/.rig/config`.

## Considered Options

- **Fixed name `rig`**: rejected, since it collides across customers.
- **Per-machine name in `~/.rig/config`**: rejected, since teammates would each get a different bucket and lose shared state.
- **Name derived from the account or token**: rejected, since a Hetzner token doesn't reveal its project id.

## Consequences

- The bucket is scoped to Workspace and Profile. Two Workspaces using the same Profile get different buckets, and each only sees environments created from itself. Sharing one bucket across Workspaces was considered and rejected; it could be added later as an opt-in "adopt existing bucket" at init.

- Teammates must use the same Profile names for the same Hetzner project, and Rig can't verify this.
- Resolution order is `STATE_BUCKET` env var, then `vars.yml`, then generate. Only provisioning commands generate and write a name. `list`, `status` and `destroy` fail with "no state bucket configured" if none exists.
- A generated name is retried on a name-taken error. A configured name (from `vars.yml` or `STATE_BUCKET`) never is, and fails hard with a message pointing at a likely Profile mismatch.
- If `vars.yml` can't be written, the command fails. There is no ephemeral fallback bucket.
- Generating a name edits `vars.yml`, so init prints a reminder to commit it.
- There is no migration from the legacy `rig` bucket. Existing environments there must be cleaned up manually.
- Test mode (`is_test`) uses its own Test bucket and never reads or writes `vars.yml` or `~/.rig/config`.
