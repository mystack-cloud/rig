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

**Test bucket**:
A throwaway state bucket used only by automated test runs.
_Avoid_: Dev bucket
