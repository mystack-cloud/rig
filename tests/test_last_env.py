"""Tests for last-handled environment memory under /tmp."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.last_env import (
    clear_last_env,
    read_last_env,
    remember_last_env,
    resolve_env_name,
)


def test_remember_and_read_last_env(tmp_path):
    path = str(tmp_path / "last-env")
    assert read_last_env(path=path) is None
    remember_last_env("dev-abc", path=path)
    assert read_last_env(path=path) == "dev-abc"
    remember_last_env("dev-xyz", path=path)
    assert read_last_env(path=path) == "dev-xyz"


def test_clear_last_env_only_when_name_matches(tmp_path):
    path = str(tmp_path / "last-env")
    remember_last_env("dev-keep", path=path)
    clear_last_env("dev-other", path=path)
    assert read_last_env(path=path) == "dev-keep"
    clear_last_env("dev-keep", path=path)
    assert read_last_env(path=path) is None


def test_clear_last_env_unconditional(tmp_path):
    path = str(tmp_path / "last-env")
    remember_last_env("dev-gone", path=path)
    clear_last_env(path=path)
    assert read_last_env(path=path) is None


def test_resolve_env_name_prefers_explicit_then_last(tmp_path):
    path = str(tmp_path / "last-env")
    remember_last_env("dev-last", path=path)
    assert resolve_env_name(None, None, path=path) == "dev-last"
    assert resolve_env_name("dev-pos", None, path=path) == "dev-pos"
    assert resolve_env_name(None, "dev-flag", path=path) == "dev-flag"
    assert resolve_env_name("", "  ", path=path) == "dev-last"
