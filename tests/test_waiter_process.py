"""Real waiter child process with fake `lsappinfo`/`herdr` programs on PATH."""

import json
import os
import signal
import stat
import subprocess
import sys

import pytest

from agent_voice import waiter

MARKER = "SECRETMARKER-4711"


def _script(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def world(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _script(
        bin_dir / "lsappinfo",
        'if [ "$1" = front ]; then echo ASN:1:; else echo \'bundleID="com.mitchellh.ghostty"\'; fi\n',
    )
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text('{"enabled": true}')
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "AGENT_VOICE_HOME": str(home),
        "TERM_PROGRAM": "ghostty",
        "HERDR_ENV": "1",
        "HERDR_PANE_ID": "w9:p9",
        "HERDR_BIN_PATH": str(bin_dir / "herdr"),
    }
    return bin_dir, home, env


def start(env):
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_voice", "speak", "--wait-focus"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    proc.stdin.write(f"Hola {MARKER}.".encode())
    proc.stdin.close()
    return proc


def test_waiter_exits_quietly_when_the_pane_is_gone(world):
    bin_dir, _, env = world
    err = json.dumps({"error": {"code": "pane_not_found"}})
    _script(bin_dir / "herdr", f"echo '{err}'; exit 1\n")
    proc = start(env)
    assert proc.wait(timeout=20) == 0
    assert proc.stdout.read() == b"" and proc.stderr.read() == b""


def test_waiter_keeps_the_text_in_memory_and_dies_on_cancel(world):
    bin_dir, home, env = world
    pane = json.dumps({"result": {"pane": {"focused": False}}})
    _script(bin_dir / "herdr", f"echo '{pane}'\n")
    proc = start(env)
    try:
        run_dir = home / "run"
        waiter.register("w9:p9", proc.pid, run_dir=run_dir)
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=1.5)  # still pending: not focused
        assert waiter.cancel("w9:p9", run_dir=run_dir) is True
        assert proc.wait(timeout=10) == -signal.SIGTERM
        for path in home.rglob("*"):
            if path.is_file():
                assert MARKER not in path.read_text(errors="ignore")
    finally:
        if proc.poll() is None:
            proc.kill()
