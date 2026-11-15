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
A project repository containing a `.rig/` directory, which owns its own State buckets.
_Avoid_: Project (ambiguous with a Hetzner Cloud project)

**Environment**:
A remote development machine provisioned by rig, owned by one identity and tracked in a State bucket.
_Avoid_: Instance, VM, server

**Outputs**:
The named values that an Environment's playbooks publish for the user (URLs, ports, credentials), kept alongside the Environment and shown by `rig describe`.
_Avoid_: Facts, results

**Test bucket**:
A throwaway state bucket used only by automated test runs.
_Avoid_: Dev bucket
