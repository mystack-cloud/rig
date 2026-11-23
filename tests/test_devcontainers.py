"""Tests for Dev Container discovery and selection."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.devcontainers import DevTarget, find_devcontainers


def _fake_remote(output: str):
    calls: list[str] = []

    def run_remote(command: str) -> str:
        calls.append(command)
        return output

    run_remote.calls = calls  # type: ignore[attr-defined]
    return run_remote


def test_finds_devcontainer_in_repo_root():
    run = _fake_remote("/workspace/api/.devcontainer/devcontainer.json\n")
    found = find_devcontainers(run, [("api", "/workspace/api")])
    assert found == [DevTarget("api", ".devcontainer/devcontainer.json")]


def test_finds_all_three_spec_locations_and_one_subfolder_level():
    run = _fake_remote(
        "\n".join(
            [
                "/workspace/mono/.devcontainer/devcontainer.json",
                "/workspace/mono/.devcontainer.json",
                "/workspace/mono/.devcontainer/backend/devcontainer.json",
                "/workspace/mono/services/api/.devcontainer/devcontainer.json",
                "/workspace/mono/web/.devcontainer.json",
            ]
        )
    )
    found = find_devcontainers(run, [("mono", "/workspace/mono")])
    assert [t.config for t in found] == [
        ".devcontainer.json",
        ".devcontainer/backend/devcontainer.json",
        ".devcontainer/devcontainer.json",
        "web/.devcontainer.json",
    ]


def test_ignores_files_that_are_not_dev_containers_or_too_deep():
    run = _fake_remote(
        "\n".join(
            [
                "/workspace/mono/docs/devcontainer.json",
                "/workspace/mono/.devcontainer/a/b/devcontainer.json",
                "/workspace/mono/a/b/.devcontainer.json",
                "/workspace/mono/node_modules/.devcontainer/devcontainer.json",
                "/workspace/mono/.git/.devcontainer.json",
            ]
        )
    )
    assert find_devcontainers(run, [("mono", "/workspace/mono")]) == []


def test_results_are_sorted_by_repo_then_config():
    outputs = {
        "/workspace/web": "/workspace/web/.devcontainer/devcontainer.json\n",
        "/workspace/api": (
            "/workspace/api/.devcontainer/z/devcontainer.json\n"
            "/workspace/api/.devcontainer/a/devcontainer.json\n"
        ),
    }

    def run(command: str) -> str:
        return next(v for k, v in outputs.items() if k in command)

    found = find_devcontainers(run, [("web", "/workspace/web"), ("api", "/workspace/api")])
    assert [(t.repo, t.config) for t in found] == [
        ("api", ".devcontainer/a/devcontainer.json"),
        ("api", ".devcontainer/z/devcontainer.json"),
        ("web", ".devcontainer/devcontainer.json"),
    ]


import pytest  # noqa: E402

from cli.devcontainers import DevContainerError, choose_target  # noqa: E402

API_DEFAULT = DevTarget("api", ".devcontainer/devcontainer.json")
API_BACKEND = DevTarget("api", ".devcontainer/backend/devcontainer.json")
WEB_SUB = DevTarget("web", "app/.devcontainer.json")
MONO_NESTED = DevTarget("mono", "svc/.devcontainer/x/devcontainer.json")


def _no_input(_prompt: str) -> str:
    raise AssertionError("must not prompt")


def test_labels_name_the_repo_and_container():
    assert API_DEFAULT.label == "api"
    assert DevTarget("api", ".devcontainer.json").label == "api"
    assert API_BACKEND.label == "api:backend"
    assert WEB_SUB.label == "web:app"
    assert MONO_NESTED.label == "mono:svc/x"


def test_no_candidates_means_no_target():
    assert choose_target([], None, None, False, False, _no_input) is None


def test_single_candidate_opens_without_prompt():
    assert choose_target([API_DEFAULT], None, None, False, True, _no_input) == API_DEFAULT


def test_requested_target_skips_prompt():
    got = choose_target([API_DEFAULT, API_BACKEND], None, "api:backend", False, True, _no_input)
    assert got == API_BACKEND


def test_requested_repo_alone_works_when_repo_has_one_container():
    got = choose_target([API_DEFAULT, WEB_SUB], None, "web", False, False, _no_input)
    assert got == WEB_SUB


def test_requested_target_that_does_not_exist_is_an_error():
    with pytest.raises(DevContainerError, match="api:nope"):
        choose_target([API_DEFAULT], None, "api:nope", False, True, _no_input)


def test_non_interactive_with_several_candidates_lists_them():
    with pytest.raises(DevContainerError) as exc:
        choose_target([API_DEFAULT, API_BACKEND], None, None, False, False, _no_input)
    assert "api:backend" in str(exc.value) and "--target" in str(exc.value)


def _answers(*values: str):
    it = iter(values)
    return lambda _prompt: next(it)


def test_prompt_enter_takes_remembered_target():
    got = choose_target([API_DEFAULT, API_BACKEND], API_BACKEND, None, False, True, _answers(""))
    assert got == API_BACKEND


def test_prompt_enter_without_memory_takes_first():
    got = choose_target([API_DEFAULT, API_BACKEND], None, None, False, True, _answers(""))
    assert got == API_DEFAULT


def test_prompt_accepts_number_or_label_and_retries_on_garbage():
    both = [API_DEFAULT, API_BACKEND]
    assert choose_target(both, None, None, False, True, _answers("2")) == API_BACKEND
    assert choose_target(both, None, None, False, True, _answers("api")) == API_DEFAULT
    assert choose_target(both, None, None, False, True, _answers("9", "x", "2")) == API_BACKEND


def test_select_prompts_even_with_one_candidate():
    got = choose_target([API_DEFAULT], None, None, True, True, _answers("1"))
    assert got == API_DEFAULT


def test_ambiguous_requested_repo_prompts_within_that_repo():
    got = choose_target([API_DEFAULT, API_BACKEND, WEB_SUB], None, "api", False, True, _answers("2"))
    assert got == API_BACKEND


def _decode_uri(uri: str):
    import json
    import re

    m = re.fullmatch(r"vscode-remote://dev-container\+([0-9a-f]+)@ssh-remote\+(\S+?)(/\S*)", uri)
    assert m, uri
    return json.loads(bytes.fromhex(m.group(1))), m.group(2), m.group(3)


def test_uri_opens_repo_root_container_over_the_ssh_host():
    from cli.devcontainers import devcontainer_folder_uri

    uri = devcontainer_folder_uri("rig-env1", "/workspace/api", API_DEFAULT)
    payload, host, folder = _decode_uri(uri)
    assert host == "rig-env1"
    assert folder == "/workspaces/api"
    assert payload["hostPath"] == "/workspace/api"
    assert payload["configFile"] == {
        "$mid": 1,
        "path": "/workspace/api/.devcontainer/devcontainer.json",
        "scheme": "vscode-remote",
        "authority": "ssh-remote+rig-env1",
    }


def test_uri_for_subfolder_container_uses_that_folder_as_host_path():
    from cli.devcontainers import devcontainer_folder_uri

    uri = devcontainer_folder_uri("rig-env1", "/workspace/mono", MONO_NESTED)
    payload, _host, folder = _decode_uri(uri)
    assert payload["hostPath"] == "/workspace/mono/svc"
    assert payload["configFile"]["path"] == "/workspace/mono/svc/.devcontainer/x/devcontainer.json"
    assert folder == "/workspaces/svc"


# --- Docker in configure (ADR 0005) -----------------------------------------


def test_flag_on_derives_install_docker_and_off_derives_nothing():
    from cli.remote_dev import derive_config_vars

    assert derive_config_vars({"editor": {"devcontainers": True}})["install_docker"] is True
    assert "install_docker" not in derive_config_vars({"editor": {"devcontainers": False}})
    assert "install_docker" not in derive_config_vars({"editor": {"default": "code"}})
    assert "install_docker" not in derive_config_vars({})


def test_enabling_the_flag_changes_the_system_hash_but_leaving_it_off_does_not():
    from cli.remote_dev import derive_config_vars
    from cli.snapshots import SYSTEM_SNAPSHOT_VAR_KEYS, system_content_hash

    def system_hash(config):
        derived = derive_config_vars(config)
        var_map = {k: derived[k] for k in SYSTEM_SNAPSHOT_VAR_KEYS if k in derived}
        return system_content_hash(var_map=var_map, playbook_body="same")

    assert system_hash({"editor": {"devcontainers": True}}) != system_hash({})
    assert system_hash({"editor": {"devcontainers": False}}) == system_hash({})


def test_default_configure_playbook_installs_docker_only_when_asked():
    from cli.remote_dev import CONFIGURE_PLAYBOOK

    assert "geerlingguy.docker" in CONFIGURE_PLAYBOOK
    assert "install_docker | default(false)" in CONFIGURE_PLAYBOOK
