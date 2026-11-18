"""The advertised Python floor must match the syntax the code actually uses."""

import re
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # stdlib from Python 3.11; CI runs 3.11

ROOT = Path(__file__).resolve().parent.parent


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _floor(spec):
    match = re.search(r">=\s*(\d+)\.(\d+)", spec)
    assert match, f"unexpected requires-python: {spec!r}"
    return int(match.group(1)), int(match.group(2))


def test_requires_python_matches_the_ruff_target():
    """ruff's pyupgrade rules rewrite to the target version (e.g. `X | None`), so the
    package metadata must not promise older interpreters than that."""
    data = _pyproject()
    target = re.fullmatch(r"py(\d)(\d+)", data["tool"]["ruff"]["target-version"])
    assert target, "tool.ruff.target-version should look like 'py310'"
    assert _floor(data["project"]["requires-python"]) == (int(target.group(1)), int(target.group(2)))


def test_no_dependency_is_gated_on_a_python_older_than_the_floor():
    data = _pyproject()
    floor = _floor(data["project"]["requires-python"])
    for dep in data["project"]["dependencies"]:
        marker = re.search(r"python_version\s*<\s*['\"](\d+)\.(\d+)['\"]", dep)
        if marker:
            assert (int(marker.group(1)), int(marker.group(2))) > floor, f"dead marker: {dep}"
