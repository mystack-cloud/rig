"""Tests for the remembered Dev target per environment under /tmp."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.devcontainers import DevTarget
from cli.last_target import read_target, remember_target


def test_remember_and_read_target_round_trip(tmp_path):
    directory = str(tmp_path / "targets")
    target = DevTarget(repo="api", config=".devcontainer/backend/devcontainer.json")
    assert read_target("dev-abc", directory=directory) is None
    remember_target("dev-abc", target, directory=directory)
    assert read_target("dev-abc", directory=directory) == target


def test_targets_are_kept_per_environment(tmp_path):
    directory = str(tmp_path / "targets")
    first = DevTarget(repo="api", config=".devcontainer.json")
    second = DevTarget(repo="web", config=".devcontainer/devcontainer.json")
    remember_target("env-one", first, directory=directory)
    remember_target("env-two", second, directory=directory)
    assert read_target("env-one", directory=directory) == first
    assert read_target("env-two", directory=directory) == second


def test_env_name_cannot_escape_the_directory(tmp_path):
    directory = str(tmp_path / "targets")
    target = DevTarget(repo="api", config=".devcontainer.json")
    remember_target("../../evil", target, directory=directory)
    assert os.listdir(directory) and not (tmp_path / "evil").exists()
    assert read_target("../../evil", directory=directory) == target
