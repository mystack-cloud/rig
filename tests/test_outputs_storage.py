"""Storage-level tests for Environment Outputs and name uniqueness (ADR 0002)."""

import os
import shutil
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.remote_dev import RemoteDevManager  # noqa: E402


class MemStorage:
    """In-memory object store with the StorageManager surface rig uses."""

    def __init__(self):
        self.objects = {}

    def list_buckets(self):
        return ["rig-test"]

    def bucket_exists(self, name):
        return True

    def create_bucket(self, name):
        return True

    def list_objects(self, bucket, prefix=""):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def download_file(self, bucket, key, path):
        if key not in self.objects:
            return False
        with open(path, "wb") as f:
            f.write(self.objects[key])
        return True

    def upload_file(self, bucket, path, key):
        with open(path, "rb") as f:
            self.objects[key] = f.read()
        return True

    def delete_object(self, bucket, key):
        self.objects.pop(key, None)
        return True


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_BUCKET", "rig-test")
    monkeypatch.setenv("RIG_IDENTITY", "alice")
    storage = MemStorage()
    m = RemoteDevManager("tok", storage, workspace_dir=str(tmp_path), profile="default")
    return m, storage


def test_outputs_key_is_per_name_without_owner(mgr):
    m, _ = mgr
    assert m._outputs_key("dev-1") == "remote-dev/outputs/dev-1.yml"


def test_load_outputs_none_when_absent(mgr):
    m, _ = mgr
    assert m.load_outputs("dev-1") is None


def test_save_outputs_roundtrip_and_merge(mgr):
    m, storage = mgr
    assert m.save_outputs("dev-1", {"rig_outputs": {"a": 1, "b": 2}, "rig_outputs_sensitive": ["a"]})
    assert m.save_outputs("dev-1", {"rig_outputs": {"b": 3, "c": 4}})
    got = m.load_outputs("dev-1")
    assert got["rig_outputs"] == {"a": 1, "b": 3, "c": 4}
    assert got["rig_outputs_sensitive"] == ["a"]
    assert "updated_at" in got
    assert list(storage.objects) == ["remote-dev/outputs/dev-1.yml"]


def test_outputs_do_not_pollute_environment_records(mgr):
    m, _ = mgr
    m.save_outputs("dev-1", {"rig_outputs": {"a": 1}})
    assert m._load_state()["environments"] == []


def test_delete_state_removes_outputs(mgr):
    m, storage = mgr
    m._save_state({"name": "dev-1", "id": 1})
    m.save_outputs("dev-1", {"rig_outputs": {"a": 1}})
    m.save_outputs("dev-2", {"rig_outputs": {"a": 1}})
    assert m._delete_state("dev-1")
    assert m.load_outputs("dev-1") is None
    assert m.load_outputs("dev-2") is not None


def test_conflicting_owner_detects_other_owner(mgr, monkeypatch):
    m, storage = mgr
    m._save_state({"name": "dev-1", "id": 1})  # owned by alice
    assert m.conflicting_owner("dev-1", "alice") is None
    assert m.conflicting_owner("dev-1", "bob") == "alice"
    assert m.conflicting_owner("dev-other", "bob") is None


def test_find_record(mgr):
    m, _ = mgr
    m._save_state({"name": "dev-1", "id": 7, "ip": "1.2.3.4"})
    rec = m.find_record("dev-1")
    assert rec["id"] == 7 and rec["owner"] == "alice"
    assert m.find_record("nope") is None


# ---- create-time name uniqueness -----------------------------------------

from cli.remote_dev import RemoteDevEnvironment  # noqa: E402


def _env(monkeypatch, tmp_path, identity, storage, name="dev-1"):
    monkeypatch.setenv("STATE_BUCKET", "rig-test")
    monkeypatch.setenv("RIG_IDENTITY", identity)
    return RemoteDevEnvironment(name=name, storage_manager=storage)


def test_name_available_rejects_other_owners_name(monkeypatch, tmp_path):
    storage = MemStorage()
    alice = _env(monkeypatch, tmp_path, "alice", storage)
    alice.manager._save_state({"name": "dev-1", "id": 1})
    bob = _env(monkeypatch, tmp_path, "bob", storage)
    assert bob.name_available() is False


def test_name_available_allows_own_or_new_name(monkeypatch, tmp_path):
    storage = MemStorage()
    alice = _env(monkeypatch, tmp_path, "alice", storage)
    alice.manager._save_state({"name": "dev-1", "id": 1})
    assert alice.name_available() is True  # re-create / resume by the owner
    bob = _env(monkeypatch, tmp_path, "bob", storage, name="dev-2")
    assert bob.name_available() is True


def test_name_available_without_storage_or_bucket(monkeypatch, tmp_path):
    monkeypatch.delenv("STATE_BUCKET", raising=False)
    env = RemoteDevEnvironment(name="dev-1")
    assert env.name_available() is True
    env2 = RemoteDevEnvironment(name="dev-1", storage_manager=MemStorage())
    assert env2.name_available() is True  # no bucket configured yet


# ---- degraded paths ------------------------------------------------------------

def test_outputs_without_storage_manager():
    m = RemoteDevManager("tok", None)
    assert m.load_outputs("dev-1") is None
    assert m.save_outputs("dev-1", {"rig_outputs": {"a": 1}}) is False


def test_save_outputs_fails_when_bucket_unavailable(mgr, monkeypatch):
    m, _ = mgr
    monkeypatch.setattr(m, "_ensure_state_bucket", lambda: False)
    assert m.save_outputs("dev-1", {"rig_outputs": {"a": 1}}) is False


def test_save_outputs_swallows_upload_errors(mgr, monkeypatch):
    m, storage = mgr

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(storage, "upload_file", boom)
    assert m.save_outputs("dev-1", {"rig_outputs": {"a": 1}}) is False


def test_save_outputs_reraises_state_bucket_errors(mgr, monkeypatch):
    from cli.state_bucket import StateBucketError

    m, _ = mgr

    def boom():
        raise StateBucketError("nope")

    monkeypatch.setattr(m, "_ensure_state_bucket", boom)
    with pytest.raises(StateBucketError):
        m.save_outputs("dev-1", {"rig_outputs": {"a": 1}})


def test_setup_stops_when_name_taken(monkeypatch):
    env = RemoteDevEnvironment(name="dev-1")
    monkeypatch.setattr(env, "name_available", lambda: False)
    monkeypatch.setattr(
        env, "resolve_boot_image", lambda **k: pytest.fail("setup must stop before provisioning")
    )
    assert env.setup() is False
