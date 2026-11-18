"""Tests for .gitignore updates performed by rig init."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.remote_dev import (
    GITIGNORE_ENTRIES,
    GITIGNORE_MARKER_BEGIN,
    GITIGNORE_MARKER_END,
    ProjectConfig,
    ensure_project_gitignore,
)


def test_ensure_gitignore_creates_file(tmp_path):
    added = ensure_project_gitignore(str(tmp_path))
    assert added == list(GITIGNORE_ENTRIES)
    text = (tmp_path / ".gitignore").read_text()
    assert GITIGNORE_MARKER_BEGIN in text
    assert GITIGNORE_MARKER_END in text
    for entry in GITIGNORE_ENTRIES:
        assert entry in text


def test_ensure_gitignore_is_idempotent(tmp_path):
    assert ensure_project_gitignore(str(tmp_path))
    assert ensure_project_gitignore(str(tmp_path)) == []


def test_ensure_gitignore_appends_to_existing(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n.env\n")
    added = ensure_project_gitignore(str(tmp_path))
    assert ".rig/collections/" in added
    text = gitignore.read_text()
    assert text.startswith("node_modules/\n.env\n")
    assert GITIGNORE_MARKER_BEGIN in text
    assert ".rig/roles/" in text
    assert "galaxy_roles" not in text
    assert "local_roles" not in text


def test_ensure_gitignore_skips_entries_already_present(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".rig/collections/\n.rig/roles/\n")
    added = ensure_project_gitignore(str(tmp_path))
    assert ".rig/collections/" not in added
    assert ".rig/roles/" not in added
    assert ".rig/inventory.yml" in added


def test_ensure_gitignore_fills_missing_inside_managed_block(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(f"{GITIGNORE_MARKER_BEGIN}\n.rig/collections/\n{GITIGNORE_MARKER_END}\n")
    added = ensure_project_gitignore(str(tmp_path))
    assert ".rig/roles/" in added
    assert ".rig/inventory.yml" in added
    text = gitignore.read_text()
    assert text.count(GITIGNORE_MARKER_BEGIN) == 1
    for entry in GITIGNORE_ENTRIES:
        assert entry in text


def test_project_init_writes_gitignore(tmp_path):
    cfg = ProjectConfig(str(tmp_path))
    assert cfg.init(name="demo", agent_type="opencode")
    text = (tmp_path / ".gitignore").read_text()
    for entry in GITIGNORE_ENTRIES:
        assert entry in text
    assert (tmp_path / ".rig" / "local_roles" / ".gitkeep").is_file()
    assert "local_roles" not in text
