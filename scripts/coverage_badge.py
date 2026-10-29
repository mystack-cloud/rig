#!/usr/bin/env python3
"""Read coverage.xml and print or refresh the README coverage badge."""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
COVERAGE_XML = ROOT / "coverage.xml"
BADGE_RE = re.compile(
    r"https://img\.shields\.io/badge/coverage-\d+%25-[a-z]+"
)


def line_percent(xml_path: Path) -> int:
    root = ET.parse(xml_path).getroot()
    rate = float(root.attrib["line-rate"])
    return int(round(rate * 100))


def badge_color(percent: int) -> str:
    if percent >= 90:
        return "brightgreen"
    if percent >= 80:
        return "green"
    if percent >= 70:
        return "yellowgreen"
    if percent >= 60:
        return "yellow"
    if percent >= 50:
        return "orange"
    return "red"


def badge_url(percent: int) -> str:
    return f"https://img.shields.io/badge/coverage-{percent}%25-{badge_color(percent)}"


def update_readme(url: str) -> bool:
    text = README.read_text()
    updated, count = BADGE_RE.subn(url, text, count=1)
    if count != 1:
        raise SystemExit("README coverage badge not found")
    if updated != text:
        README.write_text(updated)
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Update the coverage badge URL in README.md",
    )
    parser.add_argument(
        "--xml",
        type=Path,
        default=COVERAGE_XML,
        help="Coverage XML report (default: coverage.xml)",
    )
    args = parser.parse_args()

    if not args.xml.is_file():
        print(f"Coverage report not found: {args.xml}", file=sys.stderr)
        return 1

    percent = line_percent(args.xml)
    url = badge_url(percent)
    print(f"## Coverage\n\nLine coverage: **{percent}%**\n")
    if args.write:
        changed = update_readme(url)
        print("Updated README badge." if changed else "README badge already matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
