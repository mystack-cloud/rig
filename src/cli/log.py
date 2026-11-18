"""Shared CLI logging for rig.

Default level is INFO: lifecycle lines only. Playbook task output is shown
when a task fails, or when verbosity is raised.

``rig -v`` streams each playbook task and turns on DEBUG. ``-vv``, ``-vvv``,
and ``-vvvv`` pass that same verbosity through to ansible-playbook.
``rig -q`` / ``--quiet`` shows warnings and errors only.

Format (stderr): INFO is message-only; DEBUG/WARNING/ERROR keep a level prefix.
Stdout stays free for command output (encrypt ciphertext, decrypt plaintext, etc.).
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

LOGGER_NAME = "rig"
_CONFIGURED = False
_VERBOSITY = 0


class _LevelFormatter(logging.Formatter):
    """INFO: bare message. Other levels: ``LEVEL  message``."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.levelno == logging.INFO:
            return message
        return f"{record.levelname.upper():<5} {message}"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the rig logger (or a child under ``rig.``)."""
    if name and name != LOGGER_NAME:
        if not name.startswith(f"{LOGGER_NAME}."):
            name = f"{LOGGER_NAME}.{name}"
        return logging.getLogger(name)
    return logging.getLogger(LOGGER_NAME)


def verbosity() -> int:
    """How much detail the current command asked for. ``0`` is the default."""
    return _VERBOSITY


def configure_logging(
    *,
    verbose: bool = False,
    quiet: bool = False,
    level: int | None = None,
    verbosity: int | None = None,
) -> logging.Logger:
    """
    Configure the root ``rig`` logger once (or reconfigure on subsequent calls).

    ``verbosity`` counts ``-v`` flags. Precedence for the level: explicit
    ``level`` > any verbosity or ``verbose`` > ``quiet`` > INFO.
    """
    global _CONFIGURED, _VERBOSITY
    if verbosity is None:
        verbosity = 1 if verbose else 0
    _VERBOSITY = max(0, int(verbosity))
    if level is None:
        if _VERBOSITY > 0 or verbose:
            level = logging.DEBUG
        elif quiet:
            level = logging.WARNING
        else:
            level = logging.INFO

    logger = get_logger()
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_LevelFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    _CONFIGURED = True
    return logger


def ensure_logging() -> logging.Logger:
    """Configure with defaults if nothing has set logging up yet."""
    if not _CONFIGURED:
        return configure_logging()
    return get_logger()


def debug(msg: str, *args, **kwargs) -> None:
    ensure_logging().debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs) -> None:
    ensure_logging().info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs) -> None:
    ensure_logging().warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs) -> None:
    ensure_logging().error(msg, *args, **kwargs)
