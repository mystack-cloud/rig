"""Derive the PyPI version from a git release tag and check built archives."""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path

# Public PEP 440 version. At least major.minor.patch, no local segment.
_VERSION = re.compile(
    r"^(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2,}"
    r"(?:(?:a|b|rc)(?:0|[1-9]\d*))?"
    r"(?:\.post(?:0|[1-9]\d*))?"
    r"(?:\.dev(?:0|[1-9]\d*))?$"
)


def version_from_tag(tag: str) -> str:
    """Return the package version encoded by a release tag.

    ``v1.2.3`` and ``1.2.3`` both become ``1.2.3``. Anything that is not a
    public PEP 440 version is rejected so a bad tag cannot be uploaded.
    """
    raw = tag.strip()
    if raw.startswith("refs/tags/"):
        raw = raw[len("refs/tags/") :]
    if raw[:1] in ("v", "V"):
        raw = raw[1:]
    if not _VERSION.fullmatch(raw):
        raise ValueError(
            f"release tag {tag!r} is not a public PEP 440 version (expected vX.Y.Z, optional a/b/rc/post/dev suffix)"
        )
    return raw


def _metadata_version(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    raise ValueError("distribution metadata has no Version field")


def _metadata_name(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Name:"):
            return line.split(":", 1)[1].strip()
    raise ValueError("distribution metadata has no Name field")


def archive_metadata(path: Path) -> tuple[str, str]:
    """Return ``(name, version)`` from a wheel or sdist."""
    if path.name.endswith(".whl"):
        with zipfile.ZipFile(path) as archive:
            name = next(item for item in archive.namelist() if item.endswith(".dist-info/METADATA"))
            text = archive.read(name).decode()
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            member = next(item for item in archive.getmembers() if item.name.endswith("/PKG-INFO"))
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"{path} is missing PKG-INFO")
            text = extracted.read().decode()
    else:
        raise ValueError(f"not a wheel or sdist: {path}")
    return _metadata_name(text), _metadata_version(text)


def check_dist(dist_dir: Path, expected_version: str, expected_name: str = "mystack-rig") -> None:
    """Fail unless dist contains one sdist and one wheel at the tag version."""
    archives = [
        path for path in sorted(dist_dir.iterdir()) if path.name.endswith(".whl") or path.name.endswith(".tar.gz")
    ]
    wheels = [path for path in archives if path.name.endswith(".whl")]
    sdists = [path for path in archives if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        found = ", ".join(path.name for path in archives) or "(empty)"
        raise ValueError(f"expected one wheel and one sdist, found: {found}")
    for path in archives:
        name, version = archive_metadata(path)
        if name != expected_name or version != expected_version:
            raise ValueError(f"{path.name} is {name} {version}, expected {expected_name} {expected_version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Git tag or refs/tags/<tag> for this release")
    parser.add_argument(
        "--check-dist",
        type=Path,
        help="Directory of built archives that must match the tag version",
    )
    args = parser.parse_args(argv)
    try:
        version = version_from_tag(args.tag)
        if args.check_dist is not None:
            check_dist(args.check_dist, version)
    except (ValueError, StopIteration) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
