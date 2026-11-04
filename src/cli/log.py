"""Shared CLI logging for rig.

Default level is INFO. Use ``rig -v`` / ``--verbose`` for DEBUG detail, or
``rig -q`` / ``--quiet`` for warnings and errors only.

Format (stderr): INFO is message-only; DEBUG/WARNING/ERROR keep a level prefix.
Stdout stays free for command output (encrypt ciphertext, decrypt plaintext, etc.).
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

LOGGER_NAME = "rig"
_CONFIGURED = False


class _LevelFormatter(logging.Formatter):
    """INFO: bare message. Other levels: ``LEVEL  message``."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.levelno == logging.INFO:
            return message
        return f"{record.levelname.upper():<5} {message}"


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return the rig logger (or a child under ``rig.``)."""
    if name and name != LOGGER_NAME:
        if not name.startswith(f"{LOGGER_NAME}."):
            name = f"{LOGGER_NAME}.{name}"
        return logging.getLogger(name)
    return logging.getLogger(LOGGER_NAME)


def configure_logging(
    *,
    verbose: bool = False,
    quiet: bool = False,
    level: Optional[int] = None,
) -> logging.Logger:
    """
    Configure the root ``rig`` logger once (or reconfigure on subsequent calls).

    Precedence: explicit ``level`` > ``verbose`` > ``quiet`` > INFO.
    """
    global _CONFIGURED
    if level is None:
        if verbose:
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
