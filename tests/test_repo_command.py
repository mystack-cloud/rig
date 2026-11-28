"""`rig repo`: add / remove / list the Repos in rig.yml."""

from types import SimpleNamespace

import pytest
import yaml

from cli import main as main_mod
from cli import repos
from cli.main import main


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "rig.yml").write_text("name: t\nrepos:\n- name: a\n  url: https://github.com/o/a.git\n")
    (tmp_path / ".rig").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    for var in ("RIG_GITHUB_PAT", "RIG_GITLAB_PAT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(repos, "_vault_names", lambda _d: set())
    return tmp_path


def read_repos(project):
    return yaml.safe_load((project / "rig.yml").read_text())["repos"]


@pytest.mark.parametrize(
    "url, name",
    [
        ("https://github.com/o/app.git", "app"),
        ("https://github.com/o/app/", "app"),
        ("git@gitlab.com:o/tool.git", "tool"),
        ("https://example.com/o/plain", "plain"),
    ],
)
def test_repo_name_from_url(url, name):
    assert repos.repo_name_from_url(url) == name


def test_add_writes_only_repos_and_converts_ssh(project, capsys):
    assert main(["repo", "add", "git@github.com:o/b.git", "--no-pat"]) == 0
    assert read_repos(project)[-1] == {"name": "b", "url": "https://github.com/o/b.git"}
    assert "Converted remote to HTTPS" in capsys.readouterr().out
    assert not (project / ".rig" / "vars.yml").exists()


def test_add_name_and_dest(project):
    assert main(["repo", "add", "https://example.com/o/x.git", "--name", "xx", "--dest", "/srv/x"]) == 0
    assert read_repos(project)[-1] == {"name": "xx", "url": "https://example.com/o/x.git", "dest": "/srv/x"}


@pytest.mark.parametrize(
    "args", [["https://github.com/o/a.git", "--name", "other"], ["https://github.com/o/z.git", "--name", "a"]]
)
def test_add_rejects_duplicates(project, args, capsys):
    assert main(["repo", "add", *args, "--no-pat"]) == 1
    assert "already in rig.yml" in capsys.readouterr().err
    assert len(read_repos(project)) == 1


@pytest.mark.parametrize("name", ["..", ".hidden", "a/b"])
def test_add_rejects_invalid_name(project, name, capsys):
    assert main(["repo", "add", "https://github.com/o/z.git", "--name", name, "--no-pat"]) == 1
    assert "invalid repo name" in capsys.readouterr().err


def test_add_noninteractive_pat_from_env_is_stored(project, monkeypatch, capsys):
    monkeypatch.setenv("RIG_GITHUB_PAT", "tok")
    stored = {}
    monkeypatch.setattr(repos, "store_repo_pats", lambda to_store, **kw: stored.update(to_store) or True)
    assert main(["repo", "add", "https://github.com/o/b.git"]) == 0
    assert stored == {"github_pat": "tok"}
    assert "password_var" not in read_repos(project)[-1]  # provider default covers it


def test_add_without_pat_available_still_adds(project, capsys):
    assert main(["repo", "add", "https://github.com/o/b.git"]) == 0
    assert "No PAT stored" in capsys.readouterr().out
    assert read_repos(project)[-1]["name"] == "b"


def test_add_skips_pat_when_provider_default_exists(project, monkeypatch):
    monkeypatch.setattr(repos, "_vault_names", lambda _d: {"github_pat"})
    monkeypatch.setattr(repos, "store_repo_pats", lambda *a, **k: pytest.fail("must not store"))
    assert main(["repo", "add", "https://github.com/o/b.git"]) == 0


def test_add_unsupported_host_skips_pat(project, capsys):
    assert main(["repo", "add", "https://example.com/o/b.git"]) == 0
    assert "unsupported git host" in capsys.readouterr().out


def test_add_interactive_repo_specific_pat_sets_password_var(project, monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(repos, "collect_repo_pats", lambda r: ({"b": "tok"}, {}))
    monkeypatch.setattr(repos, "store_repo_pats", lambda *a, **k: True)
    assert main(["repo", "add", "https://github.com/o/b.git"]) == 0
    assert read_repos(project)[-1]["password_var"] == "b_git_pat"


def test_add_pat_storage_failure_warns_but_adds(project, monkeypatch, capsys):
    monkeypatch.setenv("RIG_GITHUB_PAT", "tok")

    def boom(*a, **k):
        raise RuntimeError("locked")

    monkeypatch.setattr(repos, "store_repo_pats", boom)
    assert main(["repo", "add", "https://github.com/o/b.git"]) == 0
    assert "PAT not stored: locked" in capsys.readouterr().err
    assert read_repos(project)[-1]["name"] == "b"


def test_remove(project, capsys):
    assert main(["repo", "remove", "a"]) == 0
    assert read_repos(project) == []
    assert "keep their clone" in capsys.readouterr().out


def test_remove_unknown_lists_known(project, capsys):
    assert main(["repo", "remove", "nope"]) == 1
    err = capsys.readouterr().err
    assert "no Repo named 'nope'" in err and "a" in err


def test_list(project, monkeypatch, capsys):
    assert main(["repo", "list"]) == 0
    assert capsys.readouterr().out.strip() == "a\thttps://github.com/o/a.git\t-\t-"
    monkeypatch.setattr(repos, "_vault_names", lambda _d: {"github_pat"})
    main(["repo", "list"])
    assert capsys.readouterr().out.strip().endswith("github_pat")


def test_list_shows_password_var_and_empty(project, capsys):
    (project / "rig.yml").write_text(
        "name: t\nrepos:\n- {name: a, url: 'https://github.com/o/a.git', dest: /d, password_var: a_git_pat}\n"
    )
    main(["repo", "list"])
    assert capsys.readouterr().out.strip() == "a\thttps://github.com/o/a.git\t/d\ta_git_pat"
    (project / "rig.yml").write_text("name: t\n")
    main(["repo", "list"])
    assert "No Repos" in capsys.readouterr().out


def test_vault_names_swallows_missing_vars(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(repos, "list_all_vault_vars", lambda d: (_ for _ in ()).throw(OSError("x")))
    assert repos._vault_names(str(tmp_path)) == set()


def test_missing_subcommand_prints_help(project):
    assert main(["repo"]) == 1


@pytest.mark.parametrize("flag, expected", [(["--apply"], None), (["--apply", "dev1"], "dev1")])
def test_apply_flag_runs_apply(project, monkeypatch, flag, expected):
    seen = []
    monkeypatch.setattr(main_mod, "_cmd_apply", lambda a: seen.append(a.name) or 0)
    assert main(["repo", "remove", "a", *flag]) == 0
    assert seen == [expected]


def test_apply_flag_skipped_on_failure(project, monkeypatch):
    monkeypatch.setattr(main_mod, "_cmd_apply", lambda a: pytest.fail("must not apply"))
    assert main(["repo", "remove", "nope", "--apply"]) == 1


def test_no_apply_flag_does_not_apply(project, monkeypatch):
    monkeypatch.setattr(main_mod, "_cmd_apply", lambda a: pytest.fail("must not apply"))
    assert main(["repo", "remove", "a"]) == 0


def test_save_failure_reports(project, monkeypatch, capsys):
    from cli.remote_dev import ProjectConfig

    monkeypatch.setattr(ProjectConfig, "save", lambda self, write_vars=True: False)
    assert main(["repo", "add", "https://example.com/o/q.git", "--no-pat"]) == 1
    assert main(["repo", "remove", "a"]) == 1
    assert capsys.readouterr().err.count("could not write rig.yml") == 2


def test_added_repo_reaches_derived_clone_list(project):
    """The playbooks read git.clone_repos, derived from rig.yml: add needs no vars.yml write."""
    from cli.remote_dev import derive_config_vars

    main(["repo", "add", "https://github.com/o/b.git", "--no-pat"])
    cfg = yaml.safe_load((project / "rig.yml").read_text())
    derived = derive_config_vars(cfg)
    assert [r["repo"] for r in derived["git"]["clone_repos"]] == [
        "https://github.com/o/a.git",
        "https://github.com/o/b.git",
    ]
    assert derived["git_providers"] == ["github"]
    main(["repo", "remove", "a"])
    cfg = yaml.safe_load((project / "rig.yml").read_text())
    assert [r["repo"] for r in derive_config_vars(cfg)["git"]["clone_repos"]] == ["https://github.com/o/b.git"]
