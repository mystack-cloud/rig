"""Tests for per-workspace, per-profile state bucket resolution (ADR 0001)."""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.remote_dev import RemoteDevManager  # noqa: E402
from cli.state_bucket import (  # noqa: E402
    StateBucketError,
    StateBucketNotConfigured,
    new_state_bucket,
    read_state_bucket,
    write_state_bucket,
)

VAULT = "secret: !vault |\n  $ANSIBLE_VAULT;1.2;AES256;shared\n  3634\n"


class FakeStorage:
    def __init__(self, existing=(), create_ok=True, taken=()):
        self.buckets = list(existing)
        self.create_ok = create_ok
        self.taken = set(taken)
        self.created = []

    def list_buckets(self):
        return list(self.buckets)

    def bucket_exists(self, name):
        return name in self.taken or name in self.buckets

    def create_bucket(self, name):
        if not self.create_ok:
            return False
        self.buckets.append(name)
        self.created.append(name)
        return True


@pytest.fixture(autouse=True)
def _no_env_bucket(monkeypatch):
    monkeypatch.delenv("STATE_BUCKET", raising=False)


def make(tmp_path, storage=None, profile="default", **kw):
    return RemoteDevManager(
        "tok", storage or FakeStorage(), workspace_dir=str(tmp_path), profile=profile, **kw
    )


def test_new_state_bucket_format_and_uniqueness():
    a, b = new_state_bucket(), new_state_bucket()
    assert re.fullmatch(r"rig-[0-9a-f]{8}", a)
    assert a != b


def test_write_then_read_roundtrip_per_profile(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    write_state_bucket(str(tmp_path), "staging", "rig-bbbbbbbb")
    write_state_bucket(str(tmp_path), "default", "rig-cccccccc")
    assert read_state_bucket(str(tmp_path), "default") == "rig-cccccccc"
    assert read_state_bucket(str(tmp_path), "staging") == "rig-bbbbbbbb"
    assert read_state_bucket(str(tmp_path), "other") is None


def test_write_preserves_vault_content(tmp_path):
    vars_yml = tmp_path / ".rig" / "vars.yml"
    vars_yml.parent.mkdir()
    vars_yml.write_text(VAULT)
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    text = vars_yml.read_text()
    assert text.startswith(VAULT)
    assert read_state_bucket(str(tmp_path), "default") == "rig-aaaaaaaa"


def test_write_rejects_bad_profile_name(tmp_path):
    with pytest.raises(StateBucketError):
        write_state_bucket(str(tmp_path), "bad: name", "rig-aaaaaaaa")


def test_unconfigured_state_bucket_raises(tmp_path):
    with pytest.raises(StateBucketNotConfigured):
        make(tmp_path).state_bucket


def test_env_overrides_vars_yml(tmp_path, monkeypatch):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    monkeypatch.setenv("STATE_BUCKET", "from-env")
    assert make(tmp_path).state_bucket == "from-env"


def test_reads_bucket_from_vars_yml(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    assert make(tmp_path).state_bucket == "rig-aaaaaaaa"


def test_ensure_generates_creates_and_records(tmp_path):
    storage = FakeStorage()
    manager = make(tmp_path, storage)
    assert manager._ensure_state_bucket() is True
    assert storage.created == [manager.state_bucket]
    assert read_state_bucket(str(tmp_path), "default") == manager.state_bucket
    # A second manager in the same workspace reuses it.
    assert make(tmp_path, storage).state_bucket == manager.state_bucket


def test_two_workspaces_get_different_buckets(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    ma, mb = make(a), make(b)
    ma._ensure_state_bucket(), mb._ensure_state_bucket()
    assert ma.state_bucket != mb.state_bucket


def test_generate_skips_taken_names(tmp_path, monkeypatch):
    names = iter(["rig-taken000", "rig-free0000"])
    monkeypatch.setattr("cli.state_bucket.new_state_bucket", lambda: next(names))
    storage = FakeStorage(taken={"rig-taken000"})
    manager = make(tmp_path, storage)
    manager._ensure_state_bucket()
    assert manager.state_bucket == "rig-free0000"


def test_generate_gives_up_after_three_attempts(tmp_path):
    manager = make(tmp_path, FakeStorage(create_ok=False))
    with pytest.raises(StateBucketError):
        manager._ensure_state_bucket()
    assert read_state_bucket(str(tmp_path), "default") is None


def test_configured_name_is_never_regenerated(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = FakeStorage(create_ok=False)
    manager = make(tmp_path, storage)
    with pytest.raises(StateBucketError, match="not accessible"):
        manager._ensure_state_bucket()
    assert read_state_bucket(str(tmp_path), "default") == "rig-aaaaaaaa"


def test_unwritable_vars_yml_fails_hard(tmp_path):
    (tmp_path / ".rig").write_text("not a directory")
    with pytest.raises(StateBucketError):
        make(tmp_path)._ensure_state_bucket()


def test_read_paths_never_generate(tmp_path):
    storage = FakeStorage()
    manager = make(tmp_path, storage)
    with pytest.raises(StateBucketNotConfigured):
        manager._load_state()
    with pytest.raises(StateBucketNotConfigured):
        manager._state_bucket_present()
    assert storage.created == []
    assert read_state_bucket(str(tmp_path), "default") is None


def test_test_mode_never_touches_vars_yml(tmp_path):
    manager = make(tmp_path, is_test=True)
    assert re.fullmatch(r"test-rig-[0-9a-f]{8}", manager.state_bucket)
    manager._ensure_state_bucket()
    assert not (tmp_path / ".rig").exists()


# --- additional branch coverage -------------------------------------------

from cli.state_bucket import find_workspace_dir  # noqa: E402


def test_find_workspace_dir_walks_up_and_falls_back(tmp_path):
    (tmp_path / ".rig").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_workspace_dir(str(nested)) == str(tmp_path)
    # No .rig anywhere above: falls back to the start dir.
    import cli.state_bucket as sb

    orig = sb.os.path.isdir
    sb.os.path.isdir = lambda p: False
    try:
        assert find_workspace_dir(str(nested)) == str(nested)
    finally:
        sb.os.path.isdir = orig


def test_read_handles_odd_vars_yml(tmp_path):
    vars_yml = tmp_path / ".rig" / "vars.yml"
    vars_yml.parent.mkdir()
    vars_yml.write_text("- just\n- a list\n")
    assert read_state_bucket(str(tmp_path), "default") is None
    vars_yml.write_text("rig_state_buckets: nope\n")
    assert read_state_bucket(str(tmp_path), "default") is None


def test_write_appends_entry_to_existing_block(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    vars_yml = tmp_path / ".rig" / "vars.yml"
    vars_yml.write_text(vars_yml.read_text().rstrip("\n"))  # no trailing newline
    write_state_bucket(str(tmp_path), "staging", "rig-bbbbbbbb")
    assert read_state_bucket(str(tmp_path), "default") == "rig-aaaaaaaa"
    assert read_state_bucket(str(tmp_path), "staging") == "rig-bbbbbbbb"


def test_write_appends_block_when_file_lacks_trailing_newline(tmp_path):
    vars_yml = tmp_path / ".rig" / "vars.yml"
    vars_yml.parent.mkdir()
    vars_yml.write_text("other: 1")
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    assert vars_yml.read_text().startswith("other: 1\n")
    assert read_state_bucket(str(tmp_path), "default") == "rig-aaaaaaaa"


def test_ensure_without_storage_returns_false(tmp_path):
    manager = RemoteDevManager("tok", None, workspace_dir=str(tmp_path), profile="default")
    assert manager._ensure_state_bucket() is False


def test_ensure_creates_missing_configured_bucket(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = FakeStorage()
    assert make(tmp_path, storage)._ensure_state_bucket() is True
    assert storage.created == ["rig-aaaaaaaa"]


def test_ensure_test_mode_create_failure_returns_false(tmp_path):
    assert make(tmp_path, FakeStorage(create_ok=False), is_test=True)._ensure_state_bucket() is False


def test_ensure_swallows_unexpected_errors(tmp_path):
    class Boom(FakeStorage):
        def list_buckets(self):
            raise RuntimeError("boom")

    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    assert make(tmp_path, Boom())._ensure_state_bucket() is False


def test_load_state_happy_and_missing_bucket(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")

    class Storage(FakeStorage):
        def download_file(self, bucket, key, dest):
            with open(dest, "w") as fh:
                fh.write("environments:\n- name: dev-x\n")

    assert make(tmp_path, Storage(existing=["rig-aaaaaaaa"]))._load_state() == {
        "environments": [{"name": "dev-x"}]
    }
    # Bucket missing: empty state, and nothing is created.
    empty = Storage()
    assert make(tmp_path, empty)._load_state() == {"environments": []}
    assert empty.created == []


def test_load_state_swallows_download_errors(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")

    class Storage(FakeStorage):
        def download_file(self, *a):
            raise RuntimeError("nope")

    assert make(tmp_path, Storage(existing=["rig-aaaaaaaa"]))._load_state() == {
        "environments": []
    }


def test_save_state_propagates_state_bucket_errors(tmp_path):
    manager = make(tmp_path, FakeStorage(create_ok=False))
    with pytest.raises(StateBucketError):
        manager._save_state({"name": "dev-x"})


def test_save_state_returns_false_when_ensure_fails(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    manager = make(tmp_path, FakeStorage(existing=["rig-aaaaaaaa"]))
    manager._ensure_state_bucket = lambda: False
    assert manager._save_state({"name": "dev-x"}) is False


def test_save_state_uploads_and_dedupes(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")

    class Storage(FakeStorage):
        uploads = []

        def upload_file(self, bucket, src, key):
            self.uploads.append((bucket, key))

    storage = Storage(existing=["rig-aaaaaaaa"])
    manager = make(tmp_path, storage)
    manager.list_dev_environments = lambda *a, **k: [{"name": "dev-x"}]
    assert manager._save_state({"name": "dev-x"}) is True
    assert ("rig-aaaaaaaa", "remote-dev/environments/state.yml") in storage.uploads


def test_save_state_swallows_unexpected_errors(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    manager = make(tmp_path, FakeStorage(existing=["rig-aaaaaaaa"]))
    manager.list_dev_environments = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    assert manager._save_state({"name": "dev-x"}) is False


def test_generate_requires_storage_manager(tmp_path):
    manager = RemoteDevManager("tok", None, workspace_dir=str(tmp_path), profile="default")
    with pytest.raises(StateBucketError):
        manager._generate_state_bucket()
