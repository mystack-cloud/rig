"""Tests for shared CLI logging."""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli import log as rig_log
from cli.main import main


def test_configure_logging_levels(capsys):
    rig_log.configure_logging()
    rig_log.info("hello-info")
    rig_log.debug("hello-debug")
    err = capsys.readouterr().err
    assert "hello-info" in err
    assert "INFO" not in err
    assert "hello-debug" not in err

    rig_log.configure_logging(verbose=True)
    rig_log.debug("hello-debug")
    err = capsys.readouterr().err
    assert "DEBUG hello-debug" in err

    rig_log.configure_logging(quiet=True)
    rig_log.info("hidden")
    rig_log.warning("shown-warn")
    err = capsys.readouterr().err
    assert "hidden" not in err
    assert "WARNING shown-warn" in err


def test_main_accepts_verbose_and_quiet_flags():
    from unittest.mock import patch

    with patch("cli.main._cmd_list", return_value=0) as listed:
        assert main(["-v", "list"]) == 0
        assert main(["-q", "list"]) == 0
        assert listed.call_count == 2
    assert rig_log.get_logger().level in (logging.DEBUG, logging.WARNING, logging.INFO)


def test_logger_name_is_rig():
    logger = rig_log.configure_logging()
    assert logger.name == "rig"
    assert logger.level == logging.INFO
