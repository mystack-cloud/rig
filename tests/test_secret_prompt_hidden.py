"""`rig vars encrypt` must never echo the secret, typed or pasted."""

import io
import os
import select
import sys
import textwrap
import time
from unittest.mock import patch

import pytest

from cli import encrypt as enc

SECRET = "s3cr3t-TOKEN-abcdef0123456789"
pty = pytest.importorskip("pty")
pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="needs a POSIX pty")

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


def _run_in_pty(code, payload, *, wait_for, per_char=False):
    """Run ``code`` with a controlling terminal; return everything the terminal showed."""
    pid, master = pty.fork()
    if pid == 0:  # child
        os.environ["PYTHONPATH"] = SRC
        os.execv(sys.executable, [sys.executable, "-c", textwrap.dedent(code)])
    shown = b""
    deadline = time.time() + 15
    sent = False
    while time.time() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            shown += chunk
        if not sent and wait_for.encode() in shown:
            time.sleep(0.2)  # let the child switch echo off, as a user would
            if per_char:  # typed
                for ch in payload:
                    os.write(master, ch.encode())
                    time.sleep(0.01)
            else:  # pasted: one burst
                os.write(master, payload.encode())
            sent = True
    os.close(master)
    os.waitpid(pid, 0)
    return shown.decode(errors="replace")


def test_harness_detects_an_echoing_prompt():
    """Control: plain input() (the old behaviour) shows the secret, so the tests below can fail."""
    shown = _run_in_pty(
        """
        value = input("Enter value to encrypt (input hidden): ")
        print("LEN=%d" % len(value))
        """,
        SECRET + "\n",
        wait_for="input hidden",
    )
    assert SECRET in shown


@pytest.mark.parametrize("per_char", [False, True], ids=["pasted", "typed"])
def test_single_line_prompt_does_not_echo_the_value(per_char):
    shown = _run_in_pty(
        """
        from cli.encrypt import read_single_line_value
        value = read_single_line_value()
        print("LEN=%d" % len(value))
        """,
        SECRET + "\n",
        wait_for="input hidden",
        per_char=per_char,
    )
    assert SECRET not in shown
    assert not any(SECRET[:8] in line for line in shown.splitlines())
    assert f"LEN={len(SECRET)}" in shown  # the value was read intact


@pytest.mark.parametrize("per_char", [False, True], ids=["pasted", "typed"])
def test_multiline_stdin_mode_does_not_echo_the_value(per_char):
    payload = SECRET + "\nsecond-line-" + SECRET + "\n\x04"  # Ctrl+D ends input
    shown = _run_in_pty(
        """
        from unittest.mock import patch
        import cli.encrypt as enc

        def fake(plaintext, name, path, vault_id=None):
            print("LEN=%d" % len(plaintext))
            return "x"

        with patch("cli.encrypt.encrypt_named_value", side_effect=fake):
            enc.encrypt_stdin(vault_password_file="/dev/null", stdin_name="db_password")
        """,
        payload,
        wait_for="finish with Ctrl+D",
        per_char=per_char,
    )
    assert SECRET not in shown and "second-line" not in shown
    assert "input hidden" in shown
    assert f"LEN={len(payload.replace(chr(4), ''))}" in shown


def test_terminal_echo_is_restored_afterwards():
    shown = _run_in_pty(
        """
        import sys, termios
        from cli.encrypt import _echo_disabled
        fd = sys.stdin.fileno()
        before = termios.tcgetattr(fd)[3] & termios.ECHO
        with _echo_disabled(sys.stdin) as hidden:
            inside = termios.tcgetattr(fd)[3] & termios.ECHO
        after = termios.tcgetattr(fd)[3] & termios.ECHO
        print("RESULT", bool(before), hidden, bool(inside), bool(after))
        """,
        "",
        wait_for="RESULT",
    )
    assert "RESULT True True False True" in shown


# ---- injection points and the non-terminal paths -----------------------------

def test_single_line_value_defaults_to_getpass_not_input():
    with patch("cli.encrypt.getpass.getpass", return_value="v") as hidden, patch(
        "builtins.input", side_effect=AssertionError("input() would echo")
    ):
        assert enc.read_single_line_value() == "v"
    assert "hidden" in hidden.call_args.args[0]


def test_single_line_value_prefers_the_secret_fn():
    assert enc.read_single_line_value(input_fn=lambda _: "a", secret_fn=lambda _: "b") == "b"
    assert enc.read_single_line_value(input_fn=lambda _: "a") == "a"


def test_single_line_value_rejects_empty():
    with pytest.raises(ValueError, match="required"):
        enc.read_single_line_value(secret_fn=lambda _: "")


def test_echo_disabled_is_a_noop_off_a_terminal():
    with enc._echo_disabled(io.StringIO("x")) as hidden:
        assert hidden is False


def test_piped_stdin_still_reads_everything_without_a_terminal(tmp_path):
    key = tmp_path / "k"
    key.write_text("pw\n")
    with patch("cli.encrypt.encrypt_named_value", return_value="v") as call:
        enc.encrypt_stdin(str(key), "name", io.StringIO("a\nb\n"))
    assert call.call_args.args[0] == "a\nb\n"
