"""Environment Outputs: parsing, merging and masking.

Playbooks publish Outputs by writing a YAML file (path given as the
``rig_outputs_file`` extra var) shaped like::

    rig_outputs:            # required: dict of name -> value
      url: https://...
      password: ...
    rig_outputs_sensitive:  # optional: extra keys to mask in `rig describe`
      - custom_secret
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from typing import Any, Optional

import yaml

MASK = "********"
OUTPUTS_KEY = "rig_outputs"
SENSITIVE_KEY = "rig_outputs_sensitive"

_SENSITIVE_RE = re.compile(r"password|secret|token|key", re.IGNORECASE)


def is_sensitive_key(key: Any) -> bool:
    return bool(_SENSITIVE_RE.search(str(key)))


def mask_outputs(
    outputs: dict[str, Any],
    sensitive: Iterable[str] = (),
    show_secrets: bool = False,
) -> dict[str, Any]:
    """Return a copy of *outputs* with sensitive values replaced by MASK."""
    if show_secrets:
        return copy.deepcopy(outputs)
    explicit = {str(s) for s in sensitive or ()}

    def _walk(value: Any) -> Any:
        if not isinstance(value, dict):
            return copy.deepcopy(value)
        result = {}
        for k, v in value.items():
            if str(k) in explicit or is_sensitive_key(k):
                result[k] = MASK
            else:
                result[k] = _walk(v)
        return result

    return _walk(outputs)


def merge_outputs(
    stored: dict[str, Any] | None,
    new: dict[str, Any],
) -> dict[str, Any]:
    """Shallow-merge *new* over *stored* (later keys win); union sensitive keys."""
    stored = stored or {}
    outputs: dict[str, Any] = dict(stored.get(OUTPUTS_KEY) or {})
    outputs.update(new.get(OUTPUTS_KEY) or {})
    sensitive: list[str] = list(stored.get(SENSITIVE_KEY) or [])
    for key in new.get(SENSITIVE_KEY) or []:
        if key not in sensitive:
            sensitive.append(key)
    return {OUTPUTS_KEY: outputs, SENSITIVE_KEY: sensitive}


def read_outputs_file(path: str) -> dict[str, Any] | None:
    """Parse a playbook-written outputs file; None if missing or malformed."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get(OUTPUTS_KEY), dict):
        return None
    sensitive = data.get(SENSITIVE_KEY)
    result: dict[str, Any] = {OUTPUTS_KEY: data[OUTPUTS_KEY]}
    if isinstance(sensitive, list):
        result[SENSITIVE_KEY] = [str(s) for s in sensitive]
    return result


def format_kv(data: dict[str, Any], indent: int = 0) -> str:
    """Render a dict as plain ``key: value`` lines (nested dicts indented)."""
    pad = "  " * indent
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            if value:
                lines.append(format_kv(value, indent + 1))
        elif isinstance(value, (list, tuple)):
            lines.append(f"{pad}{key}: {', '.join(str(v) for v in value) or '-'}")
        elif value is None or value == "":
            lines.append(f"{pad}{key}: -")
        else:
            lines.append(f"{pad}{key}: {value}")
    return "\n".join(lines)
