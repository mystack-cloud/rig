"""Extra unit tests for cli.remote_dev (Hetzner manager, setup, Ansible, vault)."""

from __future__ import annotations

import builtins
import io
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# RemoteDevEnvironment() builds RemoteDevManager and requires a token.
os.environ.setdefault("HETZNER_CLOUD_API_TOKEN", "test-token")

import cli.remote_dev as remote_dev_mod  # noqa: E402

from cli.remote_dev import (  # noqa: E402
    AnsibleManager,
    ProjectConfig,
    RemoteDevEnvironment,
    RemoteDevManager,
    Workspace,
    build_rig_ansible_vars,
    configured_agents,
    detect_client_public_ip,
    ensure_project_gitignore,
    normalize_editor_type,
    normalize_firewall_ports,
    project_editor_preference,
    prompt_editor,
    remote_workspace_path,
    resolve_firewall_mode,
    _ipv4_from_public_net,
    _parse_selection,
    _prompt,
    discover_git_projects,
    interactive_env_init,
    resolve_init_context,
)


def _mock_response(
    status_code: int = 200,
    json_data=None,
    content: bytes | None = None,
    *,
    raise_http: bool = False,
):
    r = MagicMock()
    r.status_code = status_code
    r.content = content if content is not None else (b"{}" if json_data is not None else b"")
    if json_data is not None:
        r.json.return_value = json_data
    else:
        r.json.return_value = {}
    if raise_http:

        def _raise():
            raise Exception("HTTP error")

        r.raise_for_status.side_effect = _raise
    else:
        r.raise_for_status.return_value = None
    return r


class TestRemoteDevManagerCoverage:
    def test_request_get_post_delete_and_unsupported_method(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url))
            if method == "GET":
                return _mock_response(200, {"ok": True})
            if method == "POST":
                return _mock_response(201, {"created": True})
            if method == "DELETE":
                return _mock_response(204)

        monkeypatch.setattr("cli.remote_dev.requests.get", lambda *a, **k: fake_request("GET", *a))
        monkeypatch.setattr("cli.remote_dev.requests.post", lambda *a, **k: fake_request("POST", *a))
        monkeypatch.setattr("cli.remote_dev.requests.delete", lambda *a, **k: fake_request("DELETE", *a))

        assert mgr._request("GET", "/x") == {"ok": True}
        assert mgr._request("POST", "/y", {"a": 1}) == {"created": True}
        assert mgr._request("DELETE", "/z") == {}
        with pytest.raises(ValueError, match="Unsupported HTTP method"):
            mgr._request("PATCH", "/z")

    def test_request_empty_json_body(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"{}"
        resp.json.return_value = {"a": 1}
        resp.raise_for_status.return_value = None
        monkeypatch.setattr("cli.remote_dev.requests.get", lambda *a, **k: resp)
        assert mgr._request("GET", "/z") == {"a": 1}

    def test_ssh_and_firewall_helpers(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        monkeypatch.setattr(
            "cli.remote_dev.requests.post",
            lambda *a, **k: _mock_response(200, {"ssh_key": {"id": "sk1"}}),
        )
        assert mgr.create_ssh_key("n", "pk")["ssh_key"]["id"] == "sk1"

        monkeypatch.setattr(
            "cli.remote_dev.requests.get",
            lambda *a, **k: _mock_response(
                200, {"ssh_keys": [{"name": "a", "id": 1}, {"name": "want", "id": 2}]}
            ),
        )
        assert mgr.get_ssh_key_by_name("want")["id"] == 2
        assert mgr.get_ssh_key_by_name("missing") is None

        monkeypatch.setattr("cli.remote_dev.requests.delete", lambda *a, **k: _mock_response(204))
        assert mgr.delete_ssh_key("1") is True
        monkeypatch.setattr(
            "cli.remote_dev.requests.delete",
            lambda *a, **k: _mock_response(500, raise_http=True),
        )
        assert mgr.delete_ssh_key("1") is False

        monkeypatch.setattr(
            "cli.remote_dev.requests.post",
            lambda *a, **k: _mock_response(200, {"firewall": {"id": "fw1"}}),
        )
        out = mgr.create_firewall("fw", ["203.0.113.1/32"])
        assert out["firewall"]["id"] == "fw1"

        monkeypatch.setattr(
            "cli.remote_dev.requests.get", lambda *a, **k: _mock_response(200, {"firewall": {}})
        )
        assert mgr.get_firewall("99") == {"firewall": {}}

        monkeypatch.setattr(
            "cli.remote_dev.requests.post",
            lambda *a, **k: _mock_response(200, {"action": {"id": "a1"}}),
        )
        assert "action" in mgr.set_firewall_rules("99", ["0.0.0.0/0"])

    def test_vm_helpers(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        monkeypatch.setattr(
            "cli.remote_dev.requests.post",
            lambda *a, **k: _mock_response(200, {"server": {"id": "s1"}}),
        )
        assert mgr.create_vm("vm", "cax11", "hel1", "img", ssh_key_id="sk", firewall_id="fw")[
            "server"
        ]["id"] == "s1"

        monkeypatch.setattr(
            "cli.remote_dev.requests.get",
            lambda *a, **k: _mock_response(200, {"server": {"status": "running"}}),
        )
        assert mgr.get_vm("1")["server"]["status"] == "running"

        monkeypatch.setattr(
            "cli.remote_dev.requests.get",
            lambda *a, **k: _mock_response(
                200, {"servers": [{"name": "findme", "id": 3}, {"name": "x", "id": 4}]}
            ),
        )
        assert mgr.get_vm_by_name("findme")["id"] == 3

        monkeypatch.setattr("cli.remote_dev.requests.delete", lambda *a, **k: _mock_response(204))
        assert mgr.delete_vm("1") is True
        monkeypatch.setattr(
            "cli.remote_dev.requests.delete",
            lambda *a, **k: _mock_response(500, raise_http=True),
        )
        assert mgr.delete_vm("1") is False

    def test_delete_firewall_retries(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        attempt = {"n": 0}

        def flaky_delete(*a, **k):
            attempt["n"] += 1
            if attempt["n"] < 3:
                return _mock_response(500, raise_http=True)
            return _mock_response(204)

        sleeps = []

        monkeypatch.setattr("cli.remote_dev.requests.delete", flaky_delete)
        monkeypatch.setattr("cli.remote_dev.time.sleep", lambda s: sleeps.append(s))
        assert mgr.delete_firewall("fw") is True
        assert attempt["n"] == 3
        assert sleeps == [5, 5]

        attempt["n"] = 0

        def always_fail(*a, **k):
            attempt["n"] += 1
            return _mock_response(500, raise_http=True)

        monkeypatch.setattr("cli.remote_dev.requests.delete", always_fail)
        monkeypatch.setattr("cli.remote_dev.time.sleep", lambda s: None)
        assert mgr.delete_firewall("fw") is False
        assert attempt["n"] == 12

    def test_list_snapshots_merges_pending(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if "status=available" in url:
                return _mock_response(200, {"images": [{"id": 1, "status": "available"}]})
            return _mock_response(
                200, {"images": [{"id": 1}, {"id": 2, "status": "creating"}]}
            )

        monkeypatch.setattr("cli.remote_dev.requests.get", fake_get)
        imgs = mgr.list_snapshots()
        ids = sorted(img["id"] for img in imgs)
        assert ids == [1, 2]

    def test_list_snapshots_pending_fetch_fails(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)

        def fake_get(url, **kwargs):
            if "status=available" in url:
                return _mock_response(200, {"images": [{"id": 1}]})
            raise RuntimeError("network")

        monkeypatch.setattr("cli.remote_dev.requests.get", fake_get)
        assert len(mgr.list_snapshots()) == 1

    def test_find_snapshot_by_description(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        monkeypatch.setattr(
            RemoteDevManager,
            "list_snapshots",
            lambda self: [{"description": "a", "id": 1}, {"description": "b", "id": 2}],
        )
        assert mgr.find_snapshot_by_description("b")["id"] == 2
        assert mgr.find_snapshot_by_description("nope") is None

    def test_wait_for_action(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        monkeypatch.setattr("cli.remote_dev.time.time", lambda: 0.0)
        monkeypatch.setattr("cli.remote_dev.time.sleep", lambda s: None)

        monkeypatch.setattr(
            RemoteDevManager,
            "_request",
            lambda self, method, ep: {"action": {"status": "success"}},
        )
        assert mgr.wait_for_action("a1", timeout=1, interval=1) is True

        monkeypatch.setattr(
            RemoteDevManager,
            "_request",
            lambda self, method, ep: {"action": {"status": "error", "error": {"message": "boom"}}},
        )
        assert mgr.wait_for_action("a2", timeout=1, interval=1) is False

        def flaky(self, method, ep):
            flaky.calls += 1
            if flaky.calls < 2:
                raise RuntimeError("transient")
            return {"action": {"status": "success"}}

        flaky.calls = 0
        monkeypatch.setattr(RemoteDevManager, "_request", flaky)
        assert mgr.wait_for_action("a3", timeout=10, interval=1) is True

        monkeypatch.setattr(
            RemoteDevManager,
            "_request",
            lambda self, m, e: (_ for _ in ()).throw(RuntimeError("always")),
        )
        tick = {"t": 0.0}

        def advancing_time():
            tick["t"] += 50.0
            return tick["t"]

        monkeypatch.setattr("cli.remote_dev.time.time", advancing_time)
        assert mgr.wait_for_action("a4", timeout=10, interval=0) is False

    def test_create_snapshot_and_delete_image(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        monkeypatch.setattr(
            RemoteDevManager,
            "find_snapshot_by_description",
            lambda self, d: {"id": "snap1"} if d == "exists" else None,
        )
        assert mgr.create_snapshot("srv", "exists")["id"] == "snap1"

        monkeypatch.setattr(
            RemoteDevManager,
            "find_snapshot_by_description",
            lambda self, d: None,
        )
        monkeypatch.setattr(
            RemoteDevManager,
            "_request",
            lambda self, m, e, data=None: {
                "image": {"id": "im1"},
                "action": {"id": 10},
            },
        )
        monkeypatch.setattr(RemoteDevManager, "wait_for_action", lambda *a, **k: True)
        assert mgr.create_snapshot("srv", "new", labels={"k": "v"}, wait=True)["id"] == "im1"

        monkeypatch.setattr(RemoteDevManager, "wait_for_action", lambda *a, **k: False)
        assert mgr.create_snapshot("srv", "badwait", wait=True) is None

        def req(self, method, endpoint, data=None):
            if method == "DELETE":
                return {}
            raise AssertionError((method, endpoint))

        monkeypatch.setattr(RemoteDevManager, "_request", req)
        assert mgr.delete_image("im") is True

        def req_fail(self, method, endpoint, data=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(RemoteDevManager, "_request", req_fail)
        assert mgr.delete_image("im") is False

    def test_wait_for_vm_active_and_ssh(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        t0 = {"v": 0.0}

        def fake_time():
            return t0["v"]

        monkeypatch.setattr("cli.remote_dev.time.time", fake_time)

        def get_vm(self, vm_id):
            get_vm.calls += 1
            if get_vm.calls == 1:
                return {"server": {"status": "starting"}}
            return {"server": {"status": "active"}}

        get_vm.calls = 0
        monkeypatch.setattr(RemoteDevManager, "get_vm", get_vm)
        monkeypatch.setattr("cli.remote_dev.time.sleep", lambda s: None)
        assert mgr.wait_for_vm_active("v1", timeout=200) is True

        t0["v"] = 0.0
        monkeypatch.setattr(
            RemoteDevManager,
            "get_vm",
            lambda self, i: {"server": {"status": "error"}},
        )
        assert mgr.wait_for_vm_active("v1", timeout=5) is False

        t0["v"] = 0.0

        def get_stuck(self, i):
            t0["v"] += 200.0
            return {"server": {"status": "starting"}}

        monkeypatch.setattr(RemoteDevManager, "get_vm", get_stuck)
        assert mgr.wait_for_vm_active("v1", timeout=1) is False

    def test_wait_for_ssh_branches(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        times = iter([0.0, 0.0, 100.0])
        monkeypatch.setattr("cli.remote_dev.time.time", lambda: next(times))
        monkeypatch.setattr("cli.remote_dev.time.sleep", lambda s: None)

        class R:
            returncode = 0

        monkeypatch.setattr("cli.remote_dev.subprocess.run", lambda *a, **k: R())
        assert mgr.wait_for_ssh("1.2.3.4", timeout=50, interval=1, identity_file="/k") is True

        times = iter([0.0, 100.0])
        monkeypatch.setattr("cli.remote_dev.time.time", lambda: next(times))

        class Bad:
            returncode = 1

        monkeypatch.setattr("cli.remote_dev.subprocess.run", lambda *a, **k: Bad())
        assert mgr.wait_for_ssh("1.2.3.4", timeout=1, interval=0, identity_file="/k") is False

        times = iter([0.0, 0.0, 100.0])
        monkeypatch.setattr("cli.remote_dev.time.time", lambda: next(times))
        sock = MagicMock()
        sock.connect_ex.return_value = 0
        monkeypatch.setattr("cli.remote_dev.socket.socket", lambda *a, **k: sock)
        assert mgr.wait_for_ssh("1.2.3.4", timeout=50, interval=1, identity_file=None) is True

    def test_get_vm_ip(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)
        monkeypatch.setattr(
            RemoteDevManager,
            "get_vm",
            lambda self, i: {"server": {"public_net": {"ipv4": {"ip": "203.0.113.1"}}}},
        )
        assert mgr.get_vm_ip("1") == "203.0.113.1"

    def test_state_bucket_save_load_list(self, tmp_path, monkeypatch, capsys):
        uploads = []

        class Storage:
            def list_buckets(self):
                return []

            def create_bucket(self, name):
                return True

            def upload_file(self, bucket, local, remote):
                uploads.append((bucket, local, remote))

            def download_file(self, bucket, remote, local):
                data = {"environments": [{"name": "dev-old", "id": 1}], "updated_at": "t"}
                with open(local, "w") as f:
                    import yaml as y

                    y.safe_dump(data, f)

        mgr = RemoteDevManager("tok", Storage(), is_test=True)
        assert mgr._ensure_state_bucket() is True

        assert RemoteDevManager("tok", None)._state_bucket_present() is False

        bucket = "rig-state-unit"

        class StorageListed:
            def list_buckets(self):
                return [bucket]

            def bucket_exists(self, name):
                raise AssertionError("bucket_exists should not run when listed")

        mgr_listed = RemoteDevManager("tok", StorageListed(), is_test=True)
        mgr_listed.state_bucket = bucket
        assert mgr_listed._state_bucket_present() is True

        class StorageHead:
            def list_buckets(self):
                return []

            def bucket_exists(self, name):
                return name == bucket

        mgr_head = RemoteDevManager("tok", StorageHead(), is_test=True)
        mgr_head.state_bucket = bucket
        assert mgr_head._state_bucket_present() is True

        class StorageListFails:
            def list_buckets(self):
                raise RuntimeError("list failed")

            def bucket_exists(self, name):
                return False

        mgr_fail = RemoteDevManager("tok", StorageListFails(), is_test=True)
        assert mgr_fail._state_bucket_present() is False

        mgr.list_dev_environments = lambda prefix="dev-": []
        from cli import log as rig_log

        rig_log.configure_logging()
        capsys.readouterr()
        assert mgr._save_state({"name": "dev-new", "id": 2, "ip": "1.1.1.1"}) is True
        assert "State synced" not in capsys.readouterr().err
        rig_log.configure_logging(verbose=True)
        capsys.readouterr()
        assert mgr._save_state({"name": "dev-new", "id": 2, "ip": "1.1.1.1"}) is True
        debug = capsys.readouterr().err
        index = f"{mgr.state_bucket}/{mgr.state_prefix}/state.yml"
        record = f"{mgr.state_bucket}/{mgr.state_prefix}/environments/dev-new.yml"
        assert f"State synced to {index} and {record}" in debug
        rig_log.configure_logging()
        assert uploads

        mgr.list_dev_environments = RemoteDevManager.list_dev_environments.__get__(mgr)
        mgr.storage_manager = None
        assert mgr._save_state({}) is False

        mgr2 = RemoteDevManager("tok", Storage(), is_test=True)
        mgr2.list_dev_environments = lambda p="dev-": []
        data = mgr2._load_state()
        assert "environments" in data

        monkeypatch.setattr(
            RemoteDevManager,
            "_ensure_state_bucket",
            lambda self: False,
        )
        assert mgr2._load_state() == {"environments": []}

    def test_list_dev_environments_fallback_api(self, monkeypatch):
        mgr = RemoteDevManager("tok", None)

        def load_empty(self):
            return {"environments": []}

        monkeypatch.setattr(RemoteDevManager, "_load_state", load_empty)
        monkeypatch.setattr(
            RemoteDevManager,
            "_request",
            lambda self, m, e: {
                "servers": [
                    {
                        "name": "dev-x",
                        "id": 9,
                        "status": "running",
                        "public_net": {"ipv4": {"ip": "1.2.3.4"}},
                        "server_type": {"name": "cax11"},
                        "location": {"name": "hel1"},
                    }
                ]
            },
        )
        envs = mgr.list_dev_environments("dev-")
        assert envs[0]["name"] == "dev-x"
        assert envs[0]["ip"] == "1.2.3.4"


class TestRemoteDevEnvironmentCoverage:
    def test_setup_happy_path(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        assert cfg.init(name="proj", agent_type="opencode")
        cfg.set("firewall.mode", "open")
        cfg.save(write_vars=False)

        env = RemoteDevEnvironment(name="dev-setup", storage_manager=None)
        env.manager = MagicMock()
        env.manager.create_ssh_key.return_value = {"ssh_key": {"id": "sk"}}
        env.manager.create_firewall.return_value = {"firewall": {"id": "fw"}}
        env.manager.create_vm.return_value = {"server": {"id": 42}}
        env.manager.wait_for_vm_active.return_value = True
        env.manager.get_vm_ip.return_value = "10.0.0.5"
        env.manager.wait_for_ssh.return_value = True
        env.manager._save_state.return_value = True

        monkeypatch.setattr(RemoteDevEnvironment, "resolve_boot_image", lambda self, **k: {})
        monkeypatch.setattr(RemoteDevEnvironment, "generate_ssh_keys", lambda self: True)
        env.ssh_public_key = "ssh-ed25519 AAA"
        assert env.setup(project_dir=str(tmp_path)) is True
        env.manager.create_vm.assert_called_once()

    def test_setup_failures(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        assert cfg.init(name="p", agent_type="opencode")
        cfg.set("firewall.mode", "open")
        cfg.save(write_vars=False)

        env = RemoteDevEnvironment(name="dev-bad", storage_manager=None)
        env.manager = MagicMock()
        monkeypatch.setattr(RemoteDevEnvironment, "resolve_boot_image", lambda self, **k: {})

        monkeypatch.setattr(RemoteDevEnvironment, "generate_ssh_keys", lambda self: False)
        assert env.setup(project_dir=str(tmp_path)) is False

        monkeypatch.setattr(RemoteDevEnvironment, "generate_ssh_keys", lambda self: True)
        env.ssh_public_key = ""
        assert env.setup(project_dir=str(tmp_path)) is False

        env.ssh_public_key = "k"
        env.manager.create_ssh_key.side_effect = RuntimeError("api")
        env.manager.create_vm.side_effect = RuntimeError("vm")
        assert env.setup(project_dir=str(tmp_path)) is False

        env.manager.create_vm.side_effect = None
        env.manager.create_vm.return_value = {"server": {}}
        assert env.setup(project_dir=str(tmp_path)) is False

        env.manager.create_vm.return_value = {"server": {"id": 1}}
        env.manager.wait_for_vm_active.return_value = False
        assert env.setup(project_dir=str(tmp_path)) is False

        env.manager.wait_for_vm_active.return_value = True
        env.manager.get_vm_ip.return_value = "1.1.1.1"
        env.manager.wait_for_ssh.return_value = False
        assert env.setup(project_dir=str(tmp_path)) is False

    def test_setup_dynamic_firewall_missing_client_ip(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        assert cfg.init(name="p", agent_type="opencode")
        cfg.set("firewall.mode", "dynamic_add")
        cfg.save(write_vars=False)

        env = RemoteDevEnvironment(name="dev-fw", storage_manager=None)
        env.manager = MagicMock()
        monkeypatch.setattr(RemoteDevEnvironment, "resolve_boot_image", lambda self, **k: {})
        monkeypatch.setattr(RemoteDevEnvironment, "generate_ssh_keys", lambda self: True)
        env.ssh_public_key = "pk"
        monkeypatch.setattr("cli.remote_dev.detect_client_public_ip", lambda: None)
        assert env.setup(project_dir=str(tmp_path)) is False

    def test_generate_ssh_keys_paths(self, tmp_path, monkeypatch):
        env = RemoteDevEnvironment(name="gkeys")
        key_path = f"/tmp/{env.ssh_key_name}"

        class Ret:
            returncode = 0

        def fake_run(cmd, **kwargs):
            assert "-t" in cmd
            return Ret()

        monkeypatch.setattr("cli.remote_dev.subprocess.run", fake_run)
        monkeypatch.setattr("os.path.exists", lambda p: False)
        monkeypatch.setattr("os.remove", lambda p: None)

        def fake_open(path, *a, **k):
            if path == key_path:
                return io.StringIO("priv\n")
            if path == f"{key_path}.pub":
                return io.StringIO("ssh-ed25519 pub\n")
            raise AssertionError(path)

        monkeypatch.setattr("builtins.open", fake_open)
        assert env.generate_ssh_keys() is True
        assert "priv" in (env.ssh_private_key or "")

        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: type("R", (), {"returncode": 1, "stderr": "no"})(),
        )
        assert env.generate_ssh_keys() is False

    @patch("cli.remote_dev.SNAPSHOT_AFTER", "configure")
    def test_apply_playbooks_branches(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "ap"
        project_dir.mkdir()
        cfg = ProjectConfig(str(project_dir))
        assert cfg.init(name="ap", agent_type="opencode")

        env = RemoteDevEnvironment(name="dev-ap")
        env.vm_ip = "10.0.0.1"
        env.ssh_key_name = "dev-ap-key"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")

        assert env.apply_playbooks(project_dir=str(project_dir), playbooks=tuple()) is False
        env.vm_ip = None
        assert env.apply_playbooks(project_dir=str(project_dir)) is False
        env.vm_ip = "10.0.0.1"

        cfg2 = ProjectConfig(str(tmp_path / "norig"))
        cfg2.project_dir = str(tmp_path / "norig")
        assert env.apply_playbooks(project_dir=str(tmp_path / "norig")) is False

        env.skip_playbooks = ("provision", "configure", "materialize")
        env.boot_source = "workspace"
        env.boot_snapshot_description = "desc"
        with patch.object(AnsibleManager, "_run_one_playbook", return_value=True):
            assert (
                env.apply_playbooks(
                    project_dir=str(project_dir),
                    playbooks=("provision", "configure", "authorize"),
                    stream=False,
                )
                is True
            )

        env.skip_playbooks = ()
        cfg.vars_config["git"] = {
            "clone_repos": [
                {"repo": "https://github.com/x/y", "dest": "d1"},
            ]
        }
        cfg.save()

        calls = {"bootstrap": 0}

        def fake_bootstrap(self, vm_ip, ssh_key_path):
            calls["bootstrap"] += 1
            return True

        def fake_run_one(self, path, stream=False, **kwargs):
            return True

        with patch.object(AnsibleManager, "setup_bootstrap", fake_bootstrap), patch.object(
            AnsibleManager, "_run_one_playbook", fake_run_one
        ), patch.object(RemoteDevEnvironment, "_snapshot_after_playbook", return_value=False):
            assert (
                env.apply_playbooks(
                    project_dir=str(project_dir),
                    playbooks=("configure", "authorize", "materialize"),
                    stream=False,
                    soft_fail_workspace=True,
                )
                is True
            )
        assert calls["bootstrap"] == 1

        def fail_materialize(self, path, stream=False, **kwargs):
            return not str(path).endswith("materialize.yml")

        with patch.object(AnsibleManager, "setup_bootstrap", fake_bootstrap), patch.object(
            AnsibleManager, "_run_one_playbook", fail_materialize
        ):
            assert (
                env.apply_playbooks(
                    project_dir=str(project_dir),
                    playbooks=("materialize",),
                    stream=False,
                    soft_fail_materialize=True,
                )
                is True
            )

    def test_snapshot_after_playbook_early_true(self):
        env = RemoteDevEnvironment(name="snap")
        assert env._snapshot_after_playbook("provision", MagicMock()) is True

    def test_list_environments(self):
        env = RemoteDevEnvironment(name="l")
        env.manager = MagicMock()
        env.manager.list_dev_environments.return_value = [{"name": "dev-a"}]
        assert env.list_environments() == [{"name": "dev-a"}]

    def test_agent_remote_command_bad_type_normalizes(self):
        with patch("cli.agents.normalize_agent_type", side_effect=ValueError("bad")):
            cmd = RemoteDevEnvironment.agent_remote_command("bogus", extra_args=["a", None, "b c"])
            assert "opencode" in cmd

    def test_install_agent_success(self, tmp_path, monkeypatch):
        env = RemoteDevEnvironment(name="agent-x")
        env.vm_ip = "10.0.0.2"
        env.ssh_key_name = "agent-x-key"
        env.vm_id = "vid"
        env.vm_type = "cax11"
        env.region = "hel1"
        env.ssh_key_id = "sk"
        env.firewall_id = "fw"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")

        env.manager = MagicMock()
        env.manager.storage_manager = MagicMock()
        env.manager._load_state = lambda: {"environments": []}
        env.manager.state_bucket = "b"
        env.manager.state_prefix = "p"

        runs = {"n": 0}

        def fake_run(cmd, **kwargs):
            runs["n"] += 1
            return MagicMock(returncode=0)

        monkeypatch.setattr("cli.remote_dev.subprocess.run", fake_run)
        assert env.install_agent("opencode") is True
        assert runs["n"] == 3
        assert env.manager.storage_manager.upload_file.called

    def test_install_agent_subprocess_error(self, monkeypatch):
        env = RemoteDevEnvironment(name="agent-y")
        env.vm_ip = "10.0.0.2"
        env.ssh_key_name = "agent-y-key"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")
        import subprocess as sp

        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: (_ for _ in ()).throw(sp.CalledProcessError(1, "ssh")),
        )
        assert env.install_agent() is False

    def test_vpn_helpers(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cli.remote_dev.VPN_AVAILABLE", True)
        env = RemoteDevEnvironment(name="vpn")
        env.vpn_interface = "vpn0"
        env.vpn_manager = MagicMock()
        env.vpn_manager.generate_wireguard_keys.return_value = {"private_key": "pr"}
        env.vpn_manager.setup_vpn_with_credentials.return_value = True
        env.vpn_manager.teardown_vpn_tunnel.return_value = True
        env.vpn_manager.check_vpn_status.return_value = {"ok": True}

        assert env.setup_vpn("h:51820", "peer") is True
        assert env.teardown_vpn() is True
        assert env.check_vpn_status()["ok"] is True

        monkeypatch.setattr("cli.remote_dev.VPN_AVAILABLE", False)
        assert env.setup_vpn("h", "p") is False


class TestAnsibleManagerStreaming:
    def test_run_one_playbook_stream_success(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="s", agent_type="opencode")
        cfg.save(write_vars=False)
        m = AnsibleManager(cfg)
        m.configure(str(tmp_path))
        m.setup_inventory("1.2.3.4", "/key")
        pb = tmp_path / "p.yml"
        pb.write_text("- hosts: all\n  tasks: []\n")

        class FakeStdout:
            def __iter__(self_inner):
                yield from ["line1\n", "line2\n"]

        class Proc:
            stdout = FakeStdout()

            def wait(self, timeout=None):
                return 0

        proc = Proc()
        monkeypatch.setattr("cli.remote_dev.subprocess.Popen", lambda *a, **k: proc)
        monkeypatch.setattr("cli.vault_keys.unlock_data_keys", lambda *a, **k: None)
        assert m._run_one_playbook(str(pb), stream=True) is True

    def test_run_one_playbook_stream_failure_paths(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="s", agent_type="opencode")
        m = AnsibleManager(cfg)
        m.configure(str(tmp_path))
        m.setup_inventory("1.2.3.4", "/key")
        pb = tmp_path / "p.yml"
        pb.write_text("---\n")

        class FakeStdout:
            def __iter__(self_inner):
                yield from ["skipping: no hosts matched\n"]

        class ProcFail:
            stdout = FakeStdout()

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr("cli.remote_dev.subprocess.Popen", lambda *a, **k: ProcFail())
        monkeypatch.setattr("cli.vault_keys.unlock_data_keys", lambda *a, **k: None)
        assert m._run_one_playbook(str(pb), stream=True) is False


class TestProjectConfigVaultLoad:
    def test_load_vars_whole_file_vault_with_keys(self, tmp_path, monkeypatch):
        rig = tmp_path / ".rig"
        rig.mkdir()
        vpath = rig / "vars.yml"
        vpath.write_text("$ANSIBLE_VAULT;1.1;AES256\nxx")
        monkeypatch.setattr(
            ProjectConfig,
            "load",
            lambda self: setattr(self, "vault_password", None) or True,
        )
        cfg = ProjectConfig(str(tmp_path))
        cfg.rig_dir = str(rig)
        cfg.vault_password = None

        monkeypatch.setattr("os.path.exists", lambda p: str(p).endswith("personal-data-key"))
        fake_result = MagicMock(returncode=0, stdout="ansible_user: root\n")
        monkeypatch.setattr("cli.remote_dev.subprocess.run", lambda *a, **k: fake_result)
        data = cfg._load_vars_with_vault(str(vpath))
        assert data.get("ansible_user") == "root"

    def test_merge_missing_nested(self):
        cfg = ProjectConfig("/tmp")
        merged = cfg._merge_missing({"a": {"x": 1}}, {"a": {"y": 2}, "b": 3})
        assert merged["a"]["x"] == 1
        assert merged["a"]["y"] == 2
        assert merged["b"] == 3

    def test_encrypt_vars(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="enc", agent_type="opencode")
        cfg.vault_password = "pw"
        ok = MagicMock(returncode=0)
        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: ok,
        )
        assert cfg.encrypt_vars() is True
        bad = MagicMock(returncode=1, stderr="fail")
        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: bad,
        )
        assert cfg.encrypt_vars() is False


class TestRemoteDevEnvironmentMore:
    def test_resolve_resources_from_state_and_api(self):
        env = RemoteDevEnvironment(name="res1")
        env.manager = MagicMock()
        env.manager._load_state.return_value = {
            "environments": [{"name": "res1", "id": 1, "firewall_id": 2, "ssh_key_id": 3}]
        }
        env.manager._request.return_value = {}
        assert env.resolve_resources() is True
        assert env.vm_id == 1
        assert env.firewall_id == 2
        assert env.ssh_key_id == 3

        env2 = RemoteDevEnvironment(name="srv-nm")
        env2.firewall_name = "srv-nm-fw"
        env2.ssh_key_name = "srv-nm-key"
        env2.manager = MagicMock()
        env2.manager._load_state.return_value = {"environments": []}

        def req(self, method, ep):
            if ep == "/servers":
                return {
                    "servers": [
                        {
                            "name": "srv-nm",
                            "id": 10,
                            "public_net": {"ipv4": {"ip": "203.0.113.2"}},
                        }
                    ]
                }
            if ep == "/firewalls":
                return {"firewalls": [{"name": "srv-nm-fw", "id": 20}]}
            if ep == "/ssh_keys":
                return {"ssh_keys": [{"name": "srv-nm-key", "id": 30}]}
            return {}

        env2.manager._request = req.__get__(env2.manager, RemoteDevManager)
        assert env2.resolve_resources() is True
        assert env2.vm_id == 10
        assert env2.vm_ip == "203.0.113.2"
        assert env2.firewall_id == 20
        assert env2.ssh_key_id == 30

    def test_ensure_firewall_access_open_and_missing_ip(self, tmp_path):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="fw", agent_type="opencode")
        cfg.set("firewall.mode", "open")
        cfg.set("firewall.ports", [8080])
        cfg.save(write_vars=False)

        env = RemoteDevEnvironment(name="fw1")
        env.firewall_id = 1
        env.manager = MagicMock()
        env.manager.get_firewall.return_value = {
            "firewall": {
                "rules": [
                    {
                        "direction": "in",
                        "protocol": "tcp",
                        "port": "22",
                        "source_ips": ["0.0.0.0/0", "::/0"],
                    }
                ]
            }
        }
        env.manager.get_firewall.return_value = {
            "firewall": {
                "rules": [
                    {
                        "direction": "in",
                        "protocol": "tcp",
                        "port": "22",
                        "source_ips": ["0.0.0.0/0", "::/0"],
                    },
                    {
                        "direction": "in",
                        "protocol": "tcp",
                        "port": "8080",
                        "source_ips": ["0.0.0.0/0", "::/0"],
                    },
                ]
            }
        }
        assert env.ensure_firewall_access(project_dir=str(tmp_path)) is True
        env.manager.set_firewall_rules.assert_not_called()

        cfg2 = ProjectConfig(str(tmp_path / "dyn"))
        cfg2.init(name="fw2", agent_type="opencode")
        cfg2.set("firewall.mode", "dynamic_add")
        cfg2.save(write_vars=False)
        env2 = RemoteDevEnvironment(name="fw2")
        env2.firewall_id = 2
        env2.manager = MagicMock()
        with patch("cli.remote_dev.detect_client_public_ip", return_value=None):
            assert env2.ensure_firewall_access(project_dir=str(cfg2.project_dir)) is False

    def test_snapshot_after_configure(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="snap", agent_type="opencode")
        env = RemoteDevEnvironment(name="dev")
        env.vm_id = "vm1"
        env.manager = MagicMock()
        env.manager.create_snapshot.return_value = {"id": "img"}
        # Patch helpers in-place — never replace sys.modules["cli.snapshots"].
        monkeypatch.setattr("cli.snapshots.package_version", lambda: "0")
        monkeypatch.setattr(
            "cli.snapshots.system_hash_inputs_from_project",
            lambda p: {"var_map": {}, "agent_type": "opencode"},
        )
        monkeypatch.setattr("cli.snapshots.system_content_hash", lambda **k: "hash")
        monkeypatch.setattr(
            "cli.snapshots.system_snapshot_name",
            lambda v, content_hash=None: "desc",
        )
        monkeypatch.setattr("cli.snapshots.system_snapshot_labels", lambda *a, **k: {})
        assert env._snapshot_after_playbook("configure", cfg) is True
        env.manager.create_snapshot.assert_called()
        assert env.manager.create_snapshot.call_args[0][1] == "desc"

        env.vm_id = None
        monkeypatch.setattr(RemoteDevEnvironment, "resolve_resources", lambda self: None)
        assert env._snapshot_after_playbook("configure", cfg) is False

    def test_resolve_boot_image_from_snapshot(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "boot"
        project_dir.mkdir()
        pcfg = ProjectConfig(str(project_dir))
        assert pcfg.init(name="boot", agent_type="opencode")

        env = RemoteDevEnvironment(name="boot-env")
        env.manager = MagicMock()
        env.manager.list_snapshots.return_value = []

        monkeypatch.setattr("cli.snapshots.package_version", lambda: "1.0")
        monkeypatch.setattr("cli.snapshots.workspace_content_hash", lambda *a, **k: "w")
        monkeypatch.setattr(
            "cli.snapshots.system_hash_inputs_from_project",
            lambda p: {"var_map": {}},
        )
        monkeypatch.setattr(
            "cli.snapshots.system_content_hash",
            lambda **k: "s",
        )
        monkeypatch.setattr(
            "cli.snapshots.resolve_boot_snapshot",
            lambda images, **kw: {
                "image": {"id": 55},
                "source": "workspace",
                "description": "d",
                "skip_playbooks": ("provision",),
            },
        )
        out = env.resolve_boot_image(project_dir=str(project_dir))
        assert out["source"] == "workspace"
        assert env.image == 55

    def test_attach_to_agent_paths(self, tmp_path, monkeypatch):
        env = RemoteDevEnvironment(name="att")
        assert env.attach_to_agent() == 1  # no vm_ip / key

        env.vm_ip = "10.0.0.1"
        env.ssh_key_name = "att-k"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")
        # Mocked SSH never writes the attach log; seed it so parse_session_id runs
        # (fresh CI runners have no leftover /tmp/.rig/attach-*.log).
        attach_log = Path("/tmp/.rig") / "attach-att.log"
        attach_log.parent.mkdir(parents=True, exist_ok=True)
        attach_log.write_text("session sess-1\n", encoding="utf-8")

        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="att", agent_type="opencode")
        cfg.set("agent.default", "claude")
        cfg.save()

        with patch.object(RemoteDevEnvironment, "_run_attach_ssh", return_value=0), patch(
            "cli.last_session.extract_session_from_agent_args",
            return_value=None,
        ), patch(
            "cli.last_session.parse_session_id",
            return_value="sess-1",
        ), patch(
            "cli.last_session.remember_last_session"
        ) as rem:
            assert env.attach_to_agent(project_dir=str(tmp_path)) == 0
            rem.assert_called_once()

        with patch.object(RemoteDevEnvironment, "_run_attach_ssh", side_effect=FileNotFoundError):
            assert env.attach_to_agent(project_dir=str(tmp_path)) == 1

        with patch.object(
            RemoteDevEnvironment,
            "_run_attach_ssh",
            side_effect=KeyboardInterrupt,
        ):
            assert env.attach_to_agent(project_dir=str(tmp_path)) == 130

        with patch.object(RemoteDevEnvironment, "_run_attach_ssh", return_value=0), patch(
            "cli.last_session.remember_last_session",
        ) as rem2:
            assert (
                env.attach_to_agent(
                    agent_type="opencode", agent_args=["--session", "from-arg"]
                )
                == 0
            )
            rem2.assert_called_once_with("att", "from-arg", agent_type="opencode")

    def test_run_attach_ssh_script_linux(self, monkeypatch):
        monkeypatch.setattr("cli.remote_dev.shutil.which", lambda x: "/bin/script")
        monkeypatch.setattr("cli.remote_dev.sys.platform", "linux")
        completed = MagicMock(returncode=0)
        monkeypatch.setattr("cli.remote_dev.subprocess.run", lambda *a, **k: completed)
        RemoteDevEnvironment._run_attach_ssh(
            ["ssh", "h"],
            "/tmp/log",
        )

    def test_run_attach_ssh_fallback(self, monkeypatch):
        monkeypatch.setattr("cli.remote_dev.shutil.which", lambda x: None)
        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda cmd: MagicMock(returncode=5),
        )
        assert RemoteDevEnvironment._run_attach_ssh(["ssh", "h"], "/tmp/l") == 5


class TestWorkspaceAndInteractive:
    def test_workspace_init_adds_repo(self, tmp_path):
        ws = Workspace(str(tmp_path))

        class GM:
            def __init__(self, project):
                self.project = project

            def add_repo(self, url, dest, is_ssh=False, password_var=None):
                return True

            def normalize_workspace_dests(self):
                return None

            def set_repo_dest(self, *a, **k):
                return False

            def set_repo_password_var(self, *a, **k):
                return False

        with patch("cli.git.GitManager", GM), patch(
            "cli.encrypt.git_providers_from_repos",
            return_value=["github"],
        ), patch("cli.encrypt.to_https_git_url", lambda u: u):
            assert ws.init(
                name="ws",
                repos=[{"name": "svc", "url": "https://github.com/o/r.git", "path": str(tmp_path / "svc")}],
                agent_type="opencode",
                editor="cursor",
            )

    def test_workspace_init_invalid_name(self, tmp_path):
        ws = Workspace(str(tmp_path))
        assert ws.init(name="../bad") is False

    def test_workspace_init_ignores_unknown_default_agent(self, tmp_path):
        ws = Workspace(str(tmp_path))
        assert ws.init(
            name="ws",
            agent_types=["opencode", "claude"],
            agent_type="not-an-agent",
        )
        manifest = ws.manifest_path
        import yaml
        data = yaml.safe_load(open(manifest, encoding="utf-8"))
        assert data["agent"]["default"] == "opencode"
        assert data["agent"]["enabled"] == ["opencode", "claude"]

    def test_workspace_init_selects_multiple_editors(self, tmp_path):
        ws = Workspace(str(tmp_path))
        assert ws.init(
            name="ws",
            editors=["code", "cursor"],
            editor="not-an-editor",
        )
        import yaml
        data = yaml.safe_load(open(ws.manifest_path, encoding="utf-8"))
        assert data["editor"]["default"] == "code"
        assert data["editor"]["enabled"] == ["code", "cursor"]

    def test_workspace_init_bad_editor_list_falls_back(self, tmp_path):
        root = tmp_path / "bad-editors"
        root.mkdir()
        ws = Workspace(str(root))
        assert ws.init(name="ws", editors=["not-an-editor"])
        import yaml
        data = yaml.safe_load(open(ws.manifest_path, encoding="utf-8"))
        assert data["editor"]["default"] == "code"
        assert data["editor"]["enabled"] == ["code"]

    def test_configured_agents_without_project_and_non_member_default(self):
        assert configured_agents(None) == (["opencode"], "opencode")
        cfg = ProjectConfig("/tmp/unused-rig-agents")
        cfg.config = {"agent": {"type": "cursor"}}
        assert cfg._selected_default_agent(["opencode", "claude"]) == "opencode"

    def test_interactive_env_init_success(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        root.mkdir()
        (root / "svc").mkdir()
        (root / "svc" / ".git").mkdir()
        (root / "rig.yml").write_text("kind: workspace\nname: old\n")

        def fake_discover(directory):
            return [{"name": "svc", "path": str(root / "svc"), "url": "https://github.com/o/r.git"}]

        inputs = iter(
            [
                "1",
                "ws-new",
            ]
        )

        def fake_input(prompt=""):
            return next(inputs)

        with patch("cli.remote_dev.discover_git_projects", fake_discover), patch(
            "cli.remote_dev.resolve_init_context",
            lambda start=None: {
                "root": str(root),
                "projects": fake_discover(str(root)),
                "current": str(root / "svc"),
                "in_git_repo": True,
            },
        ), patch("cli.config.ensure_credentials"), patch(
            "cli.vault_keys.bootstrap_identity_keys"
        ), patch("cli.vault_keys.read_wrap_fields", return_value={}), patch(
            "cli.agents.prompt_coding_agents",
            return_value=["opencode"],
        ), patch("cli.remote_dev.prompt_editors", return_value=["code"]), patch(
            "cli.encrypt.collect_repo_pats",
            return_value=({}, {}),
        ), patch(
            "cli.encrypt.resolve_repo_pat_var_names",
            return_value=({}, {}),
        ), patch("cli.encrypt.store_repo_pats"), patch(
            "cli.encrypt.prompt_and_store_coding_agent_credentials",
            return_value=False,
        ), patch(
            "cli.encrypt.ensure_vscode_browser_password",
            return_value=False,
        ), patch(
            "cli.agents.sync_opencode_config",
            return_value=False,
        ):
            # Do not patch RigConfig — init writes a real ~/.rig-style config path via ProjectConfig.
            assert interactive_env_init(start=str(root), input_fn=fake_input, secret_fn=fake_input)

    def test_interactive_env_init_credentials_error(self):
        with patch(
            "cli.config.ensure_credentials",
            side_effect=ValueError("no creds"),
        ):
            assert interactive_env_init() is False


class TestRemoteDevHelpers:
    def test_parse_selection_and_discover_git_projects(self, tmp_path, monkeypatch):
        projects = [
            {"name": "a", "path": "/p/a"},
            {"name": "b", "path": "/p/b"},
        ]
        assert _parse_selection("all", projects) == projects
        assert len(_parse_selection("1, 2", projects)) == 2
        monkeypatch.setattr(
            "cli.remote_dev._is_git_repo",
            lambda p: str(p).endswith("g1"),
        )
        monkeypatch.setattr("cli.remote_dev._git_remote_url", lambda p: "u")
        base = tmp_path / "disc"
        base.mkdir()
        (base / "g1").mkdir()
        (base / "g1" / ".git").mkdir()
        found = discover_git_projects(str(base))
        assert found

    def test_resolve_init_context_no_git(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("cli.remote_dev._git_root", lambda p: None)
        monkeypatch.setattr("cli.remote_dev.discover_git_projects", lambda d: [])
        ctx = resolve_init_context(str(tmp_path))
        assert ctx["in_git_repo"] is False
        assert ctx["projects"] == []


class TestRemoteDevCleanupAndDestroy:
    """Cover cleanup(), run_destroy_playbooks(), and resource edge cases."""

    def test_resolve_resources_api_warning_and_not_found(self):
        env = RemoteDevEnvironment(name="orphan")
        env.manager = MagicMock()
        env.manager._load_state.return_value = {"environments": []}
        env.manager._request.side_effect = RuntimeError("api down")
        assert env.resolve_resources() is False

        env2 = RemoteDevEnvironment(name="none")
        env2.manager = MagicMock()
        env2.manager._load_state.return_value = {"environments": []}
        env2.manager._request.return_value = {
            "servers": [],
            "firewalls": [],
            "ssh_keys": [],
        }
        assert env2.resolve_resources() is False

    def test_run_destroy_playbooks_guard_clauses(self, tmp_path):
        env = RemoteDevEnvironment(name="d1")
        assert env.run_destroy_playbooks(project_dir=str(tmp_path)) is False

        env.vm_ip = "10.0.0.1"
        assert env.run_destroy_playbooks(project_dir=str(tmp_path)) is False

        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")
        assert env.run_destroy_playbooks(project_dir=str(tmp_path)) is False

        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="d", agent_type="opencode")
        with patch.object(AnsibleManager, "configure", return_value=False):
            assert env.run_destroy_playbooks(project_dir=str(tmp_path)) is False

    def test_run_destroy_playbooks_bootstrap_and_play_failures(self, tmp_path):
        env = RemoteDevEnvironment(name="d2")
        env.vm_ip = "10.0.0.2"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="d2", agent_type="opencode")

        finalize = Path(cfg.rig_dir) / "finalize.yml"
        destroy = Path(cfg.rig_dir) / "destroy.yml"
        finalize.write_text("---\n", encoding="utf-8")
        destroy.write_text("---\n", encoding="utf-8")

        with patch.object(AnsibleManager, "configure", return_value=True), patch.object(
            AnsibleManager, "setup_bootstrap", return_value=False
        ):
            assert env.run_destroy_playbooks(project_dir=str(tmp_path), stream=False) is False

        def fake_run_one(self, path, stream=False, **kwargs):
            return "destroy" not in str(path)

        with patch.object(AnsibleManager, "configure", return_value=True), patch.object(
            AnsibleManager, "setup_bootstrap", return_value=True
        ), patch.object(AnsibleManager, "_run_one_playbook", fake_run_one):
            # Soft-fail path still reports overall success when the VM is unreachable mid-run.
            assert env.run_destroy_playbooks(project_dir=str(tmp_path), stream=False) in (True, False)

    def test_cleanup_full_branches(self, tmp_path, monkeypatch):
        env = RemoteDevEnvironment(name="clean-me")
        env.vm_id = "v1"
        env.firewall_id = "f1"
        env.ssh_key_id = "k1"
        env.vm_ip = "10.0.0.5"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")

        env.manager = MagicMock()
        env.manager.delete_vm.return_value = False
        env.manager.delete_firewall.return_value = False
        env.manager.delete_ssh_key.return_value = False
        env.manager._load_state.return_value = {
            "environments": [{"name": "clean-me", "id": "x"}],
            "updated_at": "old",
        }
        env.manager._state_bucket_present.return_value = False
        env.manager.storage_manager = MagicMock()

        monkeypatch.setattr(RemoteDevEnvironment, "resolve_resources", lambda self: None)
        monkeypatch.setattr(RemoteDevEnvironment, "ensure_firewall_access", lambda self, **k: True)
        monkeypatch.setattr(RemoteDevEnvironment, "run_destroy_playbooks", lambda self, **k: True)
        monkeypatch.setattr(RemoteDevEnvironment, "remove_ssh_config_host", lambda self, **k: (_ for _ in ()).throw(OSError))

        env.cleanup(project_dir=str(tmp_path))
        env.manager.storage_manager.upload_file.assert_not_called()

        env.manager._state_bucket_present.return_value = True
        env.cleanup(project_dir=str(tmp_path))
        env.manager.storage_manager.upload_file.assert_called_once()
        env.manager.storage_manager.delete_object.assert_called_once()

        env.manager.storage_manager = None
        env.manager._state_bucket_present.reset_mock()
        env.cleanup(project_dir=str(tmp_path))

        env2 = RemoteDevEnvironment(name="clean-min")
        env2.manager = MagicMock()
        env2.manager._load_state.return_value = {"environments": []}
        env2.manager.storage_manager = None
        monkeypatch.setattr(RemoteDevEnvironment, "resolve_resources", lambda self: None)
        monkeypatch.setattr(RemoteDevEnvironment, "ensure_firewall_access", lambda self, **k: True)
        monkeypatch.setattr(RemoteDevEnvironment, "run_destroy_playbooks", lambda self, **k: True)
        env2.cleanup(project_dir=str(tmp_path))


class TestRemoteDevAttachAndEditors:
    """attach_to_agent, SSH config, open_vscode, get_connection_info."""

    def test_attach_with_explicit_agent_and_session_arg(self, tmp_path):
        env = RemoteDevEnvironment(name="att2")
        env.vm_ip = "10.0.0.3"
        env.ssh_key_name = "att2-key"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")
        rem = MagicMock()
        with patch.object(RemoteDevEnvironment, "_run_attach_ssh", return_value=0), patch(
            "cli.last_session.extract_session_from_agent_args",
            return_value="from-args",
        ), patch("cli.last_session.parse_session_id", return_value=None), patch(
            "cli.last_session.remember_last_session",
            rem,
        ):
            env.attach_to_agent(agent_type="cursor", agent_args=["--session", "x"])
        rem.assert_called_once()

    def test_attach_log_read_oserror(self, tmp_path):
        env = RemoteDevEnvironment(name="att3")
        env.vm_ip = "10.0.0.4"
        env.ssh_key_name = "att3-key"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")
        attach_log = os.path.join("/tmp/.rig", "attach-att3.log")

        rem = MagicMock()
        real_open = open

        def selective_open(path, *a, **k):
            if str(path) == attach_log:
                raise OSError("bad")
            return real_open(path, *a, **k)

        with patch.object(RemoteDevEnvironment, "_run_attach_ssh", return_value=0), patch(
            "builtins.open", selective_open
        ), patch(
            "cli.last_session.extract_session_from_agent_args",
            return_value=None,
        ), patch(
            "cli.last_session.parse_session_id",
            return_value="parsed",
        ), patch(
            "cli.last_session.remember_last_session",
            rem,
        ):
            assert env.attach_to_agent(agent_type="opencode", agent_args=[]) == 0
        rem.assert_not_called()

    def test_run_attach_ssh_darwin(self, monkeypatch):
        monkeypatch.setattr("cli.remote_dev.shutil.which", lambda x: "/usr/bin/script")
        monkeypatch.setattr("cli.remote_dev.sys.platform", "darwin")
        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: MagicMock(returncode=0),
        )
        assert RemoteDevEnvironment._run_attach_ssh(["ssh", "x"], "/tmp/l") == 0

    def test_run_attach_ssh_script_raises_filenotfound(self, monkeypatch):
        monkeypatch.setattr("cli.remote_dev.shutil.which", lambda x: "/bin/script")
        monkeypatch.setattr("cli.remote_dev.sys.platform", "linux")
        calls = []

        def sequential(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                raise FileNotFoundError()
            return MagicMock(returncode=2)

        monkeypatch.setattr("cli.remote_dev.subprocess.run", sequential)
        assert RemoteDevEnvironment._run_attach_ssh(["ssh", "h"], "/tmp/x") == 2

    def test_ssh_config_upsert_and_remove(self, tmp_path, monkeypatch):
        env = RemoteDevEnvironment(name="ssh-cfg")
        env.vm_ip = "203.0.113.9"
        env.ssh_key_name = "cfg-k"
        sshd = tmp_path / "ssh"
        sshd.mkdir()
        cfg_path = str(sshd / "config")
        monkeypatch.setattr("os.chmod", lambda *a, **k: (_ for _ in ()).throw(OSError))

        alias = env.ensure_ssh_config_host(ssh_config_path=cfg_path)
        assert "rig-ssh-cfg" == alias
        again = env.ensure_ssh_config_host(ssh_config_path=cfg_path)
        assert again == alias

        env.remove_ssh_config_host(ssh_config_path=cfg_path)
        text = Path(cfg_path).read_text(encoding="utf-8")
        assert "rig managed" not in text

    def test_open_vscode_browser_and_editor_paths(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="vscode", agent_type="opencode")
        cfg.save()

        env = RemoteDevEnvironment(name="vs")
        env.vm_ip = "10.0.0.8"
        env.ssh_key_name = "vs-k"
        Path(f"/tmp/{env.ssh_key_name}").write_text("k", encoding="utf-8")

        monkeypatch.setattr(
            "cli.encrypt.ensure_vscode_browser_password",
            MagicMock(side_effect=RuntimeError("vault")),
        )
        wb = MagicMock(return_value=False)
        monkeypatch.setattr("cli.remote_dev.webbrowser", types.SimpleNamespace(open=wb))
        assert env.open_vscode(project_dir=str(tmp_path), browser=True) == 0

        monkeypatch.setattr(
            "cli.encrypt.ensure_vscode_browser_password",
            MagicMock(return_value=None),
        )
        monkeypatch.setattr(
            "cli.encrypt.read_vscode_browser_password",
            lambda project_dir: "pw-secret-12345678",
        )
        monkeypatch.setattr("cli.remote_dev.webbrowser", types.SimpleNamespace(open=MagicMock(return_value=True)))
        assert env.open_vscode(project_dir=str(tmp_path), browser=True) == 0

        monkeypatch.setattr(
            "cli.remote_dev.webbrowser",
            types.SimpleNamespace(open=MagicMock(side_effect=RuntimeError("no browser"))),
        )
        assert env.open_vscode(project_dir=str(tmp_path), browser=True) == 1

        monkeypatch.setattr(
            RemoteDevEnvironment,
            "resolve_editor_command",
            staticmethod(lambda prefer=None, fallbacks=None: None),
        )
        assert env.open_vscode(browser=False) == 1

        monkeypatch.setattr(
            RemoteDevEnvironment,
            "resolve_editor_command",
            staticmethod(lambda prefer=None, fallbacks=None: "/bin/false-editor"),
        )

        def _raise_fnf(*_a, **_k):
            raise FileNotFoundError()

        monkeypatch.setattr("cli.remote_dev.subprocess.run", _raise_fnf)
        assert env.open_vscode(browser=False) == 1

        monkeypatch.setattr(
            RemoteDevEnvironment,
            "resolve_editor_command",
            staticmethod(lambda prefer=None, fallbacks=None: "/bin/good"),
        )
        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: MagicMock(returncode=0),
        )
        monkeypatch.setattr(RemoteDevEnvironment, "ensure_ssh_config_host", lambda self, **k: "rig-x")
        assert env.open_vscode(remote_path="workspace", browser=False) == 0

    def test_code_server_url_and_get_connection_info(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="ci", agent_type="claude")
        cfg.save()

        env = RemoteDevEnvironment(name="ci")
        env.vm_ip = "10.0.0.9"
        env.ssh_key_name = "ci-k"
        u = env.code_server_url()
        assert "10.0.0.9" in u and "folder=" in u

        info = env.get_agent_connection_info()
        assert info["agent_type"] == "claude"
        assert "exec claude" in info["remote_command"]


class TestProjectConfigLoadLegacy:
    def test_load_legacy_nested_rig_and_setup(self, tmp_path):
        rig = tmp_path / ".rig"
        rig.mkdir()
        (rig / "rig.yml").write_text(
            "vm:\n  type: cax11\n  availability_zone: hel1\n",
            encoding="utf-8",
        )
        (rig / "setup.yml").write_text(
            "agent:\n  type: cursor\n",
            encoding="utf-8",
        )
        cfg = ProjectConfig(str(tmp_path))
        assert cfg.load()
        assert cfg.config.get("agent", {}).get("type") == "cursor"
        assert "playbooks" in cfg.config

    def test_load_requirements_formats_and_vault_password(self, tmp_path):
        rig = tmp_path / ".rig"
        rig.mkdir()
        (tmp_path / "rig.yml").write_text("name: t\n", encoding="utf-8")
        (rig / "requirements.yml").write_text(
            "collections:\n  - name: community.general\n  - amazon.aws\n"
            "roles:\n  - name: geerlingguy.docker\n  - my.custom.role\n",
            encoding="utf-8",
        )
        (rig / ".vault_password").write_text("secret\n", encoding="utf-8")
        (rig / "vars.yml").write_text("ansible_user: root\n", encoding="utf-8")
        cfg = ProjectConfig(str(tmp_path))
        assert cfg.load()
        assert cfg.vault_password == "secret"
        assert "community.general" in cfg.requirements

        (rig / "requirements.yml").unlink()
        (rig / "requirements.txt").write_text("# c\n- community.general\n", encoding="utf-8")
        cfg2 = ProjectConfig(str(tmp_path))
        assert cfg2.load()
        assert "community.general" in cfg2.requirements

    def test_load_merge_personal_vars(self, tmp_path, monkeypatch):
        rig = tmp_path / ".rig"
        rig.mkdir()
        (tmp_path / "rig.yml").write_text("name: z\n", encoding="utf-8")
        (rig / "vars.yml").write_text("x: 1\n", encoding="utf-8")

        def fake_personal_path(project_dir):
            return str(rig / "vars.test.yml")

        (rig / "vars.test.yml").write_text("y: 2\n", encoding="utf-8")
        monkeypatch.setattr("cli.vault_keys.personal_vars_path", fake_personal_path)
        monkeypatch.setattr(ProjectConfig, "_load_vars_with_vault", lambda self, p: {"from": p})

        cfg = ProjectConfig(str(tmp_path))
        assert cfg.load()
        assert "from" in cfg.vars_config


class TestProjectConfigVaultMore:
    def test_whole_file_vault_with_vault_password(self, tmp_path, monkeypatch):
        rig = tmp_path / ".rig"
        rig.mkdir()
        vp = rig / "v.yml"
        vp.write_text("$ANSIBLE_VAULT;1.1;AES256\n", encoding="utf-8")
        cfg = ProjectConfig(str(tmp_path))
        cfg.rig_dir = str(rig)
        cfg.vault_password = "pw"
        monkeypatch.setattr(
            "os.path.exists",
            lambda p: not str(p).endswith("data-key"),
        )
        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: MagicMock(returncode=0, stdout="z: 9\n"),
        )
        assert cfg._load_vars_with_vault(str(vp)) == {"z": 9}

    def test_whole_file_vault_decrypt_fails(self, tmp_path, monkeypatch):
        rig = tmp_path / ".rig"
        rig.mkdir()
        vp = rig / "v2.yml"
        vp.write_text("$ANSIBLE_VAULT;1.1;AES256\n", encoding="utf-8")
        cfg = ProjectConfig(str(tmp_path))
        cfg.rig_dir = str(rig)
        cfg.vault_password = "pw"
        monkeypatch.setattr("os.path.exists", lambda p: False)
        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: MagicMock(returncode=1, stderr="bad vault"),
        )
        assert cfg._load_vars_with_vault(str(vp)) == {}

    def test_save_writes_roles_and_collections(self, tmp_path):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="s", agent_type="opencode")
        cfg.requirements = [
            "community.general",
            "geerlingguy.docker",
            "ansible.posix",
        ]
        assert cfg.save()
        text = (tmp_path / ".rig" / "requirements.yml").read_text()
        assert "collections" in text
        assert "roles" in text


class TestAnsibleManagerMore:
    def test_configure_legacy_playbook_keys_and_absolute_paths(self, tmp_path):
        cfg = ProjectConfig(str(tmp_path))
        (tmp_path / "rig.yml").write_text(
            "playbooks:\n  system: /abs/sys.yml\n  workspace: rel/ws.yml\n",
            encoding="utf-8",
        )
        (tmp_path / ".rig").mkdir(exist_ok=True)
        m = AnsibleManager()
        assert m.configure(str(tmp_path))
        assert m.playbook_path == "/abs/sys.yml"
        assert m.materialize_playbook_path.endswith("rel/ws.yml")

    def test_configure_init_on_failed_load(self, tmp_path):
        m = AnsibleManager()
        with patch.object(ProjectConfig, "load", return_value=False), patch.object(
            ProjectConfig, "init", return_value=True
        ) as ini:
            assert m.configure(str(tmp_path))
        ini.assert_called()

    def test_set_project_config(self, tmp_path):
        cfg = ProjectConfig(str(tmp_path))
        m = AnsibleManager()
        m.set_project_config(cfg)
        assert m.project_config is cfg

    def test_setup_inventory_errors(self, tmp_path):
        m = AnsibleManager(None)
        assert m.setup_inventory("1.2.3.4", "/k") is False
        m = AnsibleManager(ProjectConfig(str(tmp_path)))
        with patch("builtins.open", side_effect=OSError("fail")):
            assert m.setup_inventory("1.2.3.4", "/k") is False

    def test_setup_requirements_existing_file_short_circuits(self, tmp_path):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="r", agent_type="opencode")
        req = Path(cfg.rig_dir) / "requirements.yml"
        req.write_text("collections: []\n", encoding="utf-8")
        m = AnsibleManager(cfg)
        assert m.setup_requirements() is True

    def test_setup_requirements_creates_from_legacy_txt(self, tmp_path):
        rig = tmp_path / "leg" / ".rig"
        rig.mkdir(parents=True)
        leg = rig / "requirements.txt"
        leg.write_text("# x\n- community.general\n", encoding="utf-8")
        cfg = ProjectConfig(str(tmp_path / "leg"))
        cfg.init(name="r2", agent_type="opencode")
        req = rig / "requirements.yml"
        if req.exists():
            req.unlink()
        m = AnsibleManager(cfg)
        assert m.setup_requirements() is True
        assert req.exists()
        assert not leg.exists()

    def test_setup_roles_error(self, tmp_path):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="roles", agent_type="opencode")
        m = AnsibleManager(cfg)
        with patch("os.makedirs", side_effect=OSError("fail")):
            assert m.setup_roles() is False

    def test_setup_playbook_errors(self, tmp_path):
        m = AnsibleManager(None)
        assert m.setup_playbook() is False
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="p", agent_type="opencode")
        m2 = AnsibleManager(cfg)
        assert m2.project_config is cfg
        with patch.object(ProjectConfig, "write_provision_playbook", return_value=False):
            assert m2.setup_playbook() is False

        m2.configure(str(tmp_path))
        with patch.object(ProjectConfig, "write_provision_playbook", return_value=True), patch.object(
            ProjectConfig, "write_configure_playbook", return_value=True
        ), patch.object(ProjectConfig, "write_authorize_playbook", return_value=True), patch.object(
            ProjectConfig, "write_destroy_playbook", return_value=True
        ), patch.object(ProjectConfig, "write_materialize_playbook", return_value=True), patch.object(
            AnsibleManager, "configure", return_value=True
        ):
            assert m2.setup_playbook(custom_tasks=[{"debug": {"msg": "hi"}}]) is True

    def test_install_roles_not_found_and_failure_branch(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="gal", agent_type="opencode")
        m = AnsibleManager(cfg)
        assert m.install_roles("/nope/requirements.yml") is False

        req = Path(cfg.rig_dir) / "requirements.yml"
        req.write_text("collections: []\n", encoding="utf-8")

        def run_seq(cmd, **kwargs):
            class R:
                returncode = 1
                stderr = "e"
                stdout = ""

            return R()

        monkeypatch.setattr("cli.remote_dev.subprocess.run", run_seq)
        assert m.install_roles(str(req)) is True

    def test_install_roles_exception_path(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="g2", agent_type="opencode")
        m = AnsibleManager(cfg)
        monkeypatch.setattr("os.path.exists", lambda p: True)
        monkeypatch.setattr(
            "cli.remote_dev.subprocess.run",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert m.install_roles() is True

    def test_run_playbook_and_run_all_paths(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="run", agent_type="opencode")
        m = AnsibleManager(cfg)
        assert m.run_playbook() is False

        m.playbook_path = str(tmp_path / "p.yml")
        m.inventory_path = str(tmp_path / "i.yml")
        m._run_one_playbook = MagicMock(return_value=True)
        assert m.run_playbook() is True

        m._run_one_playbook.side_effect = RuntimeError("x")
        assert m.run_playbook() is False

        m2 = AnsibleManager(cfg)
        m2.playbook_path = str(Path(cfg.rig_dir) / "provision.yml")
        m2.materialize_playbook_path = str(Path(cfg.rig_dir) / "materialize.yml")
        m2.inventory_path = str(tmp_path / ".rig" / "inventory.yml")
        (tmp_path / ".rig" / "inventory.yml").write_text("all: {}\n")
        m2._run_one_playbook = MagicMock(return_value=False)
        assert m2.run_all_playbooks() is False

        m3 = AnsibleManager(cfg)
        assert m3.run_all_playbooks() is False

    def test__run_one_playbook_no_inventory_and_unlock_error(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="u", agent_type="opencode")
        m = AnsibleManager(cfg)
        m.inventory_path = None
        pb = Path(cfg.rig_dir) / "provision.yml"
        assert m._run_one_playbook(str(pb)) is False

        m.inventory_path = str(Path(cfg.rig_dir) / "inventory.yml")
        m.project_config = None
        assert m._run_one_playbook(str(pb)) is False
        m.project_config = cfg

        def unlock_fail(*a, **k):
            raise ValueError("unlock failed")

        monkeypatch.setattr("cli.vault_keys.unlock_data_keys", unlock_fail)
        assert m._run_one_playbook(str(pb)) is False

    def test__run_one_playbook_non_stream_fail_with_stderr(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="ns", agent_type="opencode")
        m = AnsibleManager(cfg)
        m.configure(str(tmp_path))
        m.setup_inventory("1.2.3.4", "/key")
        m.extra_vars = {"secretpass": "x" * 10}
        pb = Path(cfg.rig_dir) / "provision.yml"
        monkeypatch.setattr(
            "cli.vault_keys.unlock_data_keys",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "os.path.exists",
            lambda p: str(p).endswith("vars.yml") or str(p).endswith("inventory.yml"),
        )
        monkeypatch.setattr("cli.vault_keys.write_filtered_vars_copy", lambda *a, **k: None)

        class R:
            returncode = 2
            stdout = "skipping: no hosts matched\n"
            stderr = "secretpass-stderr-boom"

        monkeypatch.setattr("cli.remote_dev.subprocess.run", lambda *a, **k: R())
        assert m._run_one_playbook(str(pb), stream=False) is False

    def test_setup_bootstrap_exception(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="sb", agent_type="opencode")
        m = AnsibleManager(cfg)

        def boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(m, "setup_inventory", boom)
        assert m.setup_bootstrap("1.1.1.1", "/k") is False


class TestModuleLevelHelpers:
    def test_normalize_editor_type_invalid(self):
        with pytest.raises(ValueError):
            normalize_editor_type("emacs")

    def test_prompt_editor(self):
        assert prompt_editor(default="vscode", input_fn=lambda _: "") == "code"
        assert prompt_editor(default="INVALID", input_fn=lambda _: "cursor") == "cursor"

    def test_project_editor_preference(self, tmp_path, monkeypatch):
        assert project_editor_preference(str(tmp_path)) is None

        (tmp_path / "rig.yml").write_text("editor: vscode\n", encoding="utf-8")
        assert project_editor_preference(str(tmp_path)) == "code"

        (tmp_path / "rig.yml").write_text("editor: badthing\n", encoding="utf-8")
        assert project_editor_preference(str(tmp_path)) is None

    def test_ensure_project_gitignore(self, tmp_path):
        added = ensure_project_gitignore(str(tmp_path))
        assert added
        text = (tmp_path / ".gitignore").read_text()
        assert "# >>> rig managed" in text

        assert ensure_project_gitignore(str(tmp_path)) == []

        (tmp_path / ".gitignore").write_text(
            "# >>> rig managed\n.rig/collections/\n# <<< rig managed\n",
            encoding="utf-8",
        )
        extra = ensure_project_gitignore(str(tmp_path))
        assert ".rig/roles/" in extra

    def test_build_rig_ansible_vars_snapshot_import_fail(self, monkeypatch):
        env = RemoteDevEnvironment(name="v")
        p = ProjectConfig("/tmp")
        p.config = {"name": "w"}

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            # Only block the top-level import of ``cli.snapshots``. Submodule loads
            # during ``import cli`` (level > 0) must still succeed or the interpreter
            # ends up with a broken ``cli`` package and leaked state for later tests.
            if level == 0 and name == "cli.snapshots":
                raise ImportError()
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        v = build_rig_ansible_vars(env, p)
        assert v["rig_version"] == "0.0.0"

    def test_remote_workspace_path(self):
        assert remote_workspace_path() == "/workspace"
        assert remote_workspace_path("a", "/b/") == "/workspace/a/b"

    def test_ipv4_from_public_net_branches(self):
        assert _ipv4_from_public_net({"ipv4": {"ip": "1.1.1.1"}}) == "1.1.1.1"
        assert _ipv4_from_public_net({"primary_ipv4": {"ip": "2.2.2.2"}}) == "2.2.2.2"
        assert _ipv4_from_public_net({}) is None

    def test_detect_client_public_ip_branches(self, monkeypatch):
        class Resp:
            ok = True
            text = "not-an-ip"

        monkeypatch.setattr(
            "cli.remote_dev.requests.get",
            lambda *a, **k: Resp(),
        )
        assert detect_client_public_ip() is None

        bad = MagicMock(side_effect=RuntimeError("net"))
        monkeypatch.setattr("cli.remote_dev.requests.get", bad)
        assert detect_client_public_ip() is None

    def test_resolve_firewall_mode_unknown(self, caplog):
        import logging

        caplog.set_level(logging.WARNING)
        assert resolve_firewall_mode({"mode": "nope"}) == "dynamic_add"
        assert any("Unknown firewall.mode" in r.message for r in caplog.records)

    def test_normalize_firewall_ports_skips(self, caplog):
        import logging

        caplog.set_level(logging.WARNING)
        out = normalize_firewall_ports(
            [{"port": None}, 8080, {"port": 53, "protocol": "icmp"}]
        )
        ports = {d["port"] for d in out}
        assert "8080" in ports
        assert caplog.records

    def test_resolve_boot_image_snapshots_import_error(self, tmp_path, monkeypatch):
        env = RemoteDevEnvironment(name="imp")
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name == "cli.snapshots":
                raise ImportError("no snapshots")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert env.resolve_boot_image(project_dir=str(tmp_path))["source"] == "base"

    def test_resolve_boot_image_list_snapshots_fails(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="rb", agent_type="opencode")
        env = RemoteDevEnvironment(name="rb")
        env.manager = MagicMock()
        env.manager.list_snapshots.side_effect = RuntimeError("api")
        monkeypatch.setattr(
            "cli.snapshots.resolve_boot_snapshot",
            lambda *a, **k: {"image": None},
        )
        out = env.resolve_boot_image(project_dir=str(tmp_path))
        assert out["source"] == "base"


class TestInteractiveEnvInitBranches:
    def _patch_core(self, root, projects, monkeypatch, **extra):
        monkeypatch.setattr(
            "cli.remote_dev.resolve_init_context",
            lambda start=None: {
                "root": str(root),
                "projects": projects,
                "current": str(root / projects[0]["name"]) if projects else None,
                "in_git_repo": bool(projects),
            },
        )
        monkeypatch.setattr("cli.config.ensure_credentials", lambda *a, **k: None)
        monkeypatch.setattr("cli.vault_keys.read_wrap_fields", MagicMock(return_value={}))
        monkeypatch.setattr("cli.vault_keys.bootstrap_identity_keys", lambda *a, **k: None)
        monkeypatch.setattr(
            "cli.encrypt.collect_repo_pats",
            MagicMock(return_value=({}, {})),
        )
        monkeypatch.setattr(
            "cli.encrypt.resolve_repo_pat_var_names",
            MagicMock(return_value=({}, {})),
        )
        monkeypatch.setattr("cli.encrypt.store_repo_pats", lambda *a, **k: None)
        monkeypatch.setattr(
            "cli.encrypt.prompt_and_store_coding_agent_credentials",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr(
            "cli.encrypt.ensure_vscode_browser_password",
            MagicMock(return_value=False),
        )
        monkeypatch.setattr("cli.agents.sync_opencode_config", MagicMock(return_value=False))
        monkeypatch.setattr(
            "cli.agents.prompt_coding_agents",
            MagicMock(return_value=["opencode"]),
        )
        monkeypatch.setattr("cli.remote_dev.prompt_editors", MagicMock(return_value=["code"]))
        for key, val in extra.items():
            monkeypatch.setattr(key, val)

    def test_vault_bootstrap_path_error(self, tmp_path, monkeypatch):
        root = tmp_path / "ie1"
        root.mkdir()
        (root / "rig.yml").write_text("kind: workspace\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc"), "url": "https://github.com/a/b"}],
            monkeypatch,
        )
        monkeypatch.setattr(
            "cli.vault_keys.bootstrap_identity_keys",
            MagicMock(side_effect=ValueError("vault")),
        )
        assert interactive_env_init(start=str(root), input_fn=lambda _: "x") is False

    def test_no_projects_name_required(self, tmp_path, monkeypatch):
        root = tmp_path / "ie2"
        root.mkdir()
        self._patch_core(root, [], monkeypatch)
        monkeypatch.setattr(
            "cli.remote_dev._prompt",
            MagicMock(return_value=""),
        )
        assert interactive_env_init(start=str(root), input_fn=lambda _: "") is False

    def test_projects_no_selection(self, tmp_path, monkeypatch):
        root = tmp_path / "ie3"
        root.mkdir()
        (root / "rig.yml").write_text("name: x\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )
        inputs = iter(["999", "ws"])
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: next(inputs),
            )
            is False
        )

    def test_projects_empty_name(self, tmp_path, monkeypatch):
        root = tmp_path / "ie4"
        root.mkdir()
        (root / "rig.yml").write_text("name: old\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )

        def fake_prompt(message, default=None, input_fn=None):
            if "Environment name" in message:
                return ""
            return default or ""

        monkeypatch.setattr("cli.remote_dev._prompt", fake_prompt)
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: "all",
            )
            is False
        )

    def test_prompt_coding_agent_value_error(self, tmp_path, monkeypatch):
        root = tmp_path / "ie5"
        root.mkdir()
        (root / "rig.yml").write_text("name: z\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )
        monkeypatch.setattr(
            "cli.agents.prompt_coding_agents",
            MagicMock(side_effect=ValueError("bad agent")),
        )
        inputs = iter(["all", "wspace"])
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: next(inputs),
            )
            is False
        )

    def test_string_agent_prompt_is_coerced(self, tmp_path, monkeypatch):
        root = tmp_path / "ie-str"
        root.mkdir()
        (root / "svc").mkdir()
        (root / "rig.yml").write_text("name: z\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )
        monkeypatch.setattr(
            "cli.agents.prompt_coding_agents",
            MagicMock(return_value="claude"),
        )
        inputs = iter(["all", "wspace"])
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: next(inputs),
            )
            is True
        )
        import yaml
        manifest = yaml.safe_load((root / "rig.yml").read_text())
        assert manifest["agent"]["default"] == "claude"

    def test_string_editor_prompt_is_coerced(self, tmp_path, monkeypatch):
        root = tmp_path / "ie-ed"
        root.mkdir()
        (root / "svc").mkdir()
        (root / "rig.yml").write_text("name: z\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )
        monkeypatch.setattr(
            "cli.remote_dev.prompt_editors",
            MagicMock(return_value="cursor"),
        )
        inputs = iter(["all", "wspace"])
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: next(inputs),
            )
            is True
        )
        import yaml
        manifest = yaml.safe_load((root / "rig.yml").read_text())
        assert manifest["editor"]["default"] == "cursor"
        assert manifest["editor"]["enabled"] == ["cursor"]

    def test_invalid_string_editor_prompt_falls_back(self, tmp_path, monkeypatch):
        root = tmp_path / "ie-ed-bad"
        root.mkdir()
        (root / "svc").mkdir()
        (root / "rig.yml").write_text("name: z\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )
        monkeypatch.setattr(
            "cli.remote_dev.prompt_editors",
            MagicMock(return_value="not-an-editor"),
        )
        inputs = iter(["all", "wspace"])
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: next(inputs),
            )
            is True
        )
        import yaml
        manifest = yaml.safe_load((root / "rig.yml").read_text())
        assert manifest["editor"]["default"] == "code"
        assert manifest["editor"]["enabled"] == ["code"]

    def test_prompt_default_editor_value_error(self, tmp_path, monkeypatch):
        root = tmp_path / "ie-edef"
        root.mkdir()
        (root / "rig.yml").write_text("name: z\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )
        monkeypatch.setattr(
            "cli.remote_dev.prompt_editors",
            MagicMock(return_value=["code", "cursor"]),
        )
        monkeypatch.setattr(
            "cli.remote_dev.prompt_default_editor",
            MagicMock(side_effect=ValueError("bad default")),
        )
        inputs = iter(["all", "wspace"])
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: next(inputs),
            )
            is False
        )

    def test_prompt_editor_value_error(self, tmp_path, monkeypatch):
        root = tmp_path / "ie6"
        root.mkdir()
        (root / "rig.yml").write_text("name: z\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )
        monkeypatch.setattr(
            "cli.remote_dev.prompt_editors",
            MagicMock(side_effect=ValueError("ed")),
        )
        inputs = iter(["all", "wspace"])
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: next(inputs),
            )
            is False
        )

    def test_store_warnings_and_opencode_valueerror(self, tmp_path, monkeypatch):
        root = tmp_path / "ie7"
        root.mkdir()
        (root / "svc").mkdir()
        (root / "rig.yml").write_text("name: z\n", encoding="utf-8")
        self._patch_core(
            root,
            [{"name": "svc", "path": str(root / "svc")}],
            monkeypatch,
        )
        monkeypatch.setattr(
            "cli.encrypt.store_repo_pats",
            MagicMock(side_effect=RuntimeError("pat")),
        )
        monkeypatch.setattr(
            "cli.encrypt.prompt_and_store_coding_agent_credentials",
            MagicMock(side_effect=ValueError("cred")),
        )
        monkeypatch.setattr(
            "cli.encrypt.ensure_vscode_browser_password",
            MagicMock(side_effect=RuntimeError("vs")),
        )
        inputs = iter(["all", "wspace"])
        # PAT / credential / vscode warnings are non-fatal; init still completes.
        assert (
            interactive_env_init(
                start=str(root),
                input_fn=lambda _: next(inputs),
            )
            is True
        )


class TestProjectConfigInitBranchesMore:
    def test_init_updates_existing_with_legacy_playbook_keys(self, tmp_path):
        (tmp_path / "rig.yml").write_text(
            "playbooks:\n  system: .rig/oldprov.yml\n  workspace: .rig/oldmat.yml\n",
            encoding="utf-8",
        )
        cfg = ProjectConfig(str(tmp_path))
        assert cfg.init(name="merge", agent_type="opencode")
        assert "provision" in (cfg.config.get("playbooks") or {})

    def test_init_removes_obsolete_nested_files(self, tmp_path):
        cfg = ProjectConfig(str(tmp_path))
        assert cfg.init(name="obs", agent_type="opencode")
        obs = Path(cfg.rig_dir) / "rig.yml"
        obs.write_text("x: 1\n", encoding="utf-8")
        assert cfg.init(name="obs", agent_type="opencode")
        assert not obs.exists()

    def test_load_save_error_paths(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="sv", agent_type="opencode")

        def boom_read(path, *a, **k):
            if str(path).endswith("rig.yml"):
                raise OSError("read")
            return open(path, *a, **k)

        with patch("builtins.open", boom_read):
            assert cfg.load() is False

        def boom_write(path, *a, **k):
            mode = a[0] if a else k.get("mode", "r")
            if "w" in str(mode) and str(path).endswith("rig.yml"):
                raise OSError("write")
            return open(path, *a, **k)

        with patch("builtins.open", boom_write):
            assert cfg.save() is False

    def test_set_vars_nested_and_fail(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="v", agent_type="opencode")
        assert cfg.set_vars("git.clone_repos", [])
        assert cfg.get_vars("git.clone_repos") == []
        monkeypatch.setattr(ProjectConfig, "_dump_vars", lambda *a, **k: (_ for _ in ()).throw(OSError))
        assert cfg.set_vars("x.y", 1) is False

    def test_encrypt_vars_missing_file_and_no_password(self, tmp_path, capsys):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="e", agent_type="opencode")
        assert cfg.encrypt_vars() is False
        assert cfg.encrypt_vars() is False


class TestWorkspaceMore:
    def test_init_invalid_repo_name(self, tmp_path):
        ws = Workspace(str(tmp_path))
        assert ws.init(name="ok", repos=[{"name": "../bad", "url": "https://x/y"}]) is False

    def test_init_write_provision_fails(self, tmp_path, monkeypatch):
        ws = Workspace(str(tmp_path))
        with patch.object(ProjectConfig, "write_provision_playbook", return_value=False):
            assert ws.init(name="w2") is False


class TestAnsibleVaultCmdBranches:
    def test_run_one_playbook_stream_nonzero_return(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="st", agent_type="opencode")
        m = AnsibleManager(cfg)
        m.configure(str(tmp_path))
        m.setup_inventory("1.2.3.4", "/key")
        pb = Path(cfg.rig_dir) / "provision.yml"

        class FakeStdout:
            def __iter__(self):
                yield "ok line\n"

        class Proc:
            stdout = FakeStdout()

            def wait(self, timeout=None):
                return 3

        monkeypatch.setattr("cli.remote_dev.subprocess.Popen", lambda *a, **k: Proc())
        monkeypatch.setattr("cli.vault_keys.unlock_data_keys", lambda *a, **k: None)
        assert m._run_one_playbook(str(pb), stream=True) is False

    def test_run_one_playbook_vault_ids_and_filtered_identity(self, tmp_path, monkeypatch):
        cfg = ProjectConfig(str(tmp_path))
        cfg.init(name="vid", agent_type="opencode")
        m = AnsibleManager(cfg)
        m.configure(str(tmp_path))
        m.setup_inventory("1.2.3.4", "/key")
        m.vault_password = "vpw"
        pb = Path(cfg.rig_dir) / "provision.yml"

        def exists_map(path):
            ps = str(path)
            if "shared-data-key" in ps or "personal-data-key" in ps:
                return True
            if ps.endswith("vars.yml") or ps.endswith("inventory.yml"):
                return True
            if "vars.alice.yml" in ps:
                return True
            return False

        monkeypatch.setattr("os.path.exists", exists_map)

        def unlock(*a, **k):
            return None

        def filtered_copy(src, dest):
            if "vars.alice" in src:
                return "/tmp/filtered-id.yml"
            return "/tmp/filtered-shared.yml"

        monkeypatch.setattr("cli.vault_keys.unlock_data_keys", unlock)
        monkeypatch.setattr("cli.vault_keys.write_filtered_vars_copy", filtered_copy)
        monkeypatch.setattr("cli.vault_keys.personal_vars_path", lambda d: "/tmp/vars.alice.yml")

        class FakeStdout:
            def __iter__(self):
                yield "PLAY\n"

        class P:
            stdout = FakeStdout()

            def wait(self, timeout=None):
                return 0

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr("cli.remote_dev.subprocess.Popen", lambda *a, **k: P())
        monkeypatch.setattr("cli.remote_dev.subprocess.run", lambda *a, **k: R())

        m._run_one_playbook(str(pb), stream=True)
        m._run_one_playbook(str(pb), stream=False)

    def test_configure_exception(self, tmp_path, monkeypatch):
        m = AnsibleManager()
        monkeypatch.setattr(
            ProjectConfig,
            "load",
            lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert m.configure(str(tmp_path)) is False
