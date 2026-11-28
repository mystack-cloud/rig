# Rig: rules for agents

Vocabulary is in `GLOSSARY.md`; decisions are in `docs/adr/`.

## Fixing a bug or shipping a change

Do every step, in order. Don't stop after the PR.

1. **Sync, then branch.** `git checkout master && git pull --ff-only origin master`, then branch off it: `fix/<what>` or `feat/<what>`. Never commit to `master`.
2. **Test first.** Add a test that fails without the fix. Keep the diff to what the bug needs.
3. **Run the CI gates locally** (same versions as CI):
   - `uvx ruff@0.17.5 check .` and `uvx ruff@0.17.5 format --check .` (run `format .` to fix)
   - `uvx mypy src/ --ignore-missing-imports`
   - `uv run pytest tests/ -m "not e2e" --cov=src --cov-fail-under=90` with the same `--ignore` flags as the Unit Tests job in `.github/workflows/tests.yml`
4. **Commit and push**, then open a PR against `master` (use the `pr` skill for the body). Commit messages are conventional (`fix:`, `feat:`).
5. **Wait for every CI job to pass**: Lint, Type Check, Unit Tests, Integration Tests, E2E Tests and `codecov/patch`. Use `gh pr checks <n> --watch`. If one fails, fix it on the branch; never merge red.
6. **Coverage must not drop.** The floor is 90% (`.coveragerc`), but also don't lower the current total or leave new code untested. `codecov/patch` must pass.
7. **Merge** the PR with a merge commit (`gh pr merge <n> --merge`), then update `master`.
8. **Release.** Tag the merge commit and push the tag; the Release workflow publishes to PyPI.
   - Bug fix: bump the patch (`v1.7.0` → `v1.7.1`). New feature: bump the minor. Breaking change: bump the major.
   - `git tag -a vX.Y.Z -m "<summary>" && git push origin vX.Y.Z`, then `gh run watch` the Release run and confirm the version on PyPI.
9. **Tell the user** what was merged, the version, and anything existing Environments or workspaces must do (re-create, edit user-owned playbooks).

Ask before merging or releasing only if CI is red, coverage dropped, or the user hasn't asked for a release.

## Testing

- Unit tests mutate `HETZNER_CLOUD_API_TOKEN` in-process. Run the real-API tests (`tests/test_real_e2e_*.py`, `tests/test_hetzner_storage.py`, `tests/test_workspace_git_clone.py`) in their own pytest process, or they fail with 401.
- Real-API and real-VM tests cost money. Delete what you create (`rig delete`, then check no servers, SSH keys or firewalls are left with the Hetzner API).
- Anything that SSHes before `authorize.yml` has run must connect as `root`: the workspace user can't log in until authorize copies its key.
- `provision.yml` and `authorize.yml` are regenerated on every run; `configure.yml` and `materialize.yml` are user-owned and never overwritten, so changes to them need a README note for existing workspaces.
