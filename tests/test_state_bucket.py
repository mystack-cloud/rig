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
    return RemoteDevManager("tok", storage or FakeStorage(), workspace_dir=str(tmp_path), profile=profile, **kw)


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


class ObjStorage(FakeStorage):
    """In-memory object store with the StorageManager object API."""

    def __init__(self, objects=None, **kw):
        super().__init__(**kw)
        self.objects = dict(objects or {})

    def upload_file(self, bucket, src, key):
        with open(src) as fh:
            self.objects[key] = fh.read()
        return True

    def download_file(self, bucket, key, dest, retry_not_found=True):
        if key not in self.objects:
            return False
        with open(dest, "w") as fh:
            fh.write(self.objects[key])
        return True

    def list_objects(self, bucket, prefix=""):
        return [k for k in self.objects if k.startswith(prefix)]

    def delete_object(self, bucket, key):
        self.objects.pop(key, None)
        return True


P = "remote-dev/environments"


def test_load_state_reads_records_and_legacy(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = ObjStorage(
        {
            f"{P}/by-owner/alice/dev-a.yml": "name: dev-a\nowner: alice\n",
            f"{P}/by-owner/bob/dev-b.yml": "name: dev-b\nowner: bob\n",
            f"{P}/state.yml": "environments:\n- name: dev-old\n- name: dev-a\n",
        },
        existing=["rig-aaaaaaaa"],
    )
    manager = make(tmp_path, storage)
    names = sorted(e["name"] for e in manager._load_state()["environments"])
    assert names == ["dev-a", "dev-b", "dev-old"]
    only = manager._load_state(owner="alice")["environments"]
    assert [e["name"] for e in only] == ["dev-a"]
    # Missing bucket: empty state, and nothing is created.
    empty = ObjStorage()
    assert make(tmp_path, empty)._load_state() == {"environments": []}
    assert empty.created == []


def test_load_state_swallows_errors(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")

    class Storage(FakeStorage):
        def list_objects(self, *a, **k):
            raise RuntimeError("nope")

    assert make(tmp_path, Storage(existing=["rig-aaaaaaaa"]))._load_state() == {"environments": []}


def test_save_state_propagates_state_bucket_errors(tmp_path):
    manager = make(tmp_path, FakeStorage(create_ok=False))
    with pytest.raises(StateBucketError):
        manager._save_state({"name": "dev-x"})


def test_save_state_returns_false_when_ensure_fails(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    manager = make(tmp_path, FakeStorage(existing=["rig-aaaaaaaa"]))
    manager._ensure_state_bucket = lambda: False
    assert manager._save_state({"name": "dev-x"}) is False


def test_save_state_writes_only_own_record(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = ObjStorage(
        {f"{P}/by-owner/bob/dev-b.yml": "name: dev-b\nowner: bob\n"},
        existing=["rig-aaaaaaaa"],
    )
    manager = make(tmp_path, storage)
    assert manager._save_state({"name": "dev-x", "owner": "alice"}) is True
    assert f"{P}/by-owner/alice/dev-x.yml" in storage.objects
    assert storage.objects[f"{P}/by-owner/bob/dev-b.yml"] == "name: dev-b\nowner: bob\n"
    assert f"{P}/state.yml" not in storage.objects


def test_save_state_merges_into_existing_record(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    key = f"{P}/by-owner/alice/dev-x.yml"
    storage = ObjStorage(
        {key: "name: dev-x\nowner: alice\nip: 1.1.1.1\ncreated_at: t0\nstatus: active\n"},
        existing=["rig-aaaaaaaa"],
    )
    manager = make(tmp_path, storage)
    # Another identity adds agent info; unset fields must not erase data.
    assert (
        manager._save_state({"name": "dev-x", "owner": "bob", "ip": None, "agent_installed": True, "created_at": "t1"})
        is True
    )
    assert [k for k in storage.objects if "dev-x" in k] == [key]  # no duplicate under bob
    import yaml

    rec = yaml.safe_load(storage.objects[key])
    assert rec["owner"] == "alice" and rec["created_at"] == "t0"
    assert rec["ip"] == "1.1.1.1" and rec["agent_installed"] is True


def test_concurrent_saves_do_not_clobber(tmp_path):
    import threading

    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = ObjStorage(existing=["rig-aaaaaaaa"])
    manager = make(tmp_path, storage)
    threads = [
        threading.Thread(target=manager._save_state, args=({"name": f"dev-{i}", "owner": f"u{i}"},)) for i in range(8)
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    names = sorted(e["name"] for e in manager._load_state()["environments"])
    assert names == [f"dev-{i}" for i in range(8)]


def test_delete_state_removes_record_and_legacy_entry(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = ObjStorage(
        {
            f"{P}/by-owner/alice/dev-a.yml": "name: dev-a\n",
            f"{P}/by-owner/alice/dev-keep.yml": "name: dev-keep\n",
            f"{P}/state.yml": "environments:\n- name: dev-a\n- name: dev-z\n",
            f"{P}/environments/dev-a.yml": "vm_id: 1\n",
        },
        existing=["rig-aaaaaaaa"],
    )
    manager = make(tmp_path, storage)
    assert manager._delete_state("dev-a") is True
    assert f"{P}/by-owner/alice/dev-a.yml" not in storage.objects
    assert f"{P}/by-owner/alice/dev-keep.yml" in storage.objects
    assert f"{P}/environments/dev-a.yml" not in storage.objects
    assert "dev-z" in storage.objects[f"{P}/state.yml"]
    assert "dev-a" not in storage.objects[f"{P}/state.yml"]


def test_delete_state_without_storage_or_bucket(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    assert make(tmp_path, None)._delete_state("dev-a") is False
    assert make(tmp_path, ObjStorage())._delete_state("dev-a") is False


def test_save_state_swallows_unexpected_errors(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    manager = make(tmp_path, FakeStorage(existing=["rig-aaaaaaaa"]))
    manager._ensure_state_bucket = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    assert manager._save_state({"name": "dev-x"}) is False


def test_generate_requires_storage_manager(tmp_path):
    manager = RemoteDevManager("tok", None, workspace_dir=str(tmp_path), profile="default")
    with pytest.raises(StateBucketError):
        manager._generate_state_bucket()


def test_state_object_path_prefix_and_record(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    m = make(tmp_path, ObjStorage(existing=["rig-aaaaaaaa"]))
    assert m.state_object_path(owner="alice") == f"rig-aaaaaaaa/{P}/by-owner/alice/"
    assert m.state_object_path("dev-a").endswith("/by-owner/unknown/dev-a.yml")


def test_delete_state_no_storage_manager(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    m = make(tmp_path, ObjStorage(existing=["rig-aaaaaaaa"]))
    m.storage_manager = None
    assert m._delete_state("dev-a") is False


def test_drop_legacy_record_noops_and_errors(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = ObjStorage(existing=["rig-aaaaaaaa"])
    m = make(tmp_path, storage)
    m._drop_legacy_record("dev-a")  # no legacy file
    storage.objects[f"{P}/state.yml"] = "environments:\n- name: dev-z\n"
    m._drop_legacy_record("dev-a")  # name absent: untouched
    assert "dev-z" in storage.objects[f"{P}/state.yml"]

    def boom(*a, **k):
        raise RuntimeError("x")

    m._read_yaml_object = boom
    m._drop_legacy_record("dev-a")  # swallowed


def test_load_state_skips_non_yaml_and_nameless(tmp_path):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = ObjStorage(
        {
            f"{P}/by-owner/alice/notes.txt": "hi",
            f"{P}/by-owner/alice/bad.yml": "foo: bar\n",
            f"{P}/by-owner/alice/dev-a.yml": "name: dev-a\n",
        },
        existing=["rig-aaaaaaaa"],
    )
    names = [e["name"] for e in make(tmp_path, storage)._load_state()["environments"]]
    assert names == ["dev-a"]


def test_temp_file_cleanup_tolerates_unlink_errors(tmp_path, monkeypatch):
    write_state_bucket(str(tmp_path), "default", "rig-aaaaaaaa")
    storage = ObjStorage({f"{P}/state.yml": "environments:\n- name: dev-a\n"}, existing=["rig-aaaaaaaa"])
    m = make(tmp_path, storage)

    def bad_unlink(path):
        raise OSError("nope")

    monkeypatch.setattr("cli.remote_dev.os.unlink", bad_unlink)
    assert m._save_state({"name": "dev-b", "owner": "alice"}) is True
    m._drop_legacy_record("dev-a")
    assert m._read_yaml_object(f"{P}/state.yml") is not None


def test_download_file_skips_404_retry_when_not_found_is_expected(monkeypatch):
    from botocore.exceptions import ClientError

    from cli.storage import StorageManager

    sm = StorageManager.__new__(StorageManager)
    calls = []

    class Client:
        def download_file(self, *a):
            calls.append(a)
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    monkeypatch.setattr(sm, "_get_s3_client", lambda: Client(), raising=False)
    monkeypatch.setattr("cli.storage.time.sleep", lambda s: pytest.fail("slept"))
    assert sm.download_file("b", "k", "/tmp/x", retry_not_found=False) is False
    assert len(calls) == 1
