import io
import json
import sys

import pytest

from agent_voice import config
from agent_voice.cli import main


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VOICE_HOME", str(tmp_path / "home"))
    config.save({"enabled": True})
    return tmp_path


@pytest.fixture
def detached(monkeypatch):
    calls = []

    def fake(args, raw):
        calls.append(raw)
        return 0

    monkeypatch.setattr("agent_voice.cli._detach", fake)
    return calls


def feed(monkeypatch, payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_hook_ignores_stop_hook_active(home, detached, monkeypatch, capsys):
    feed(monkeypatch, {"stop_hook_active": True, "last_assistant_message": "Hola."})
    assert main(["hook", "claude"]) == 0
    assert detached == []
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_hook_passes_last_assistant_message_to_detach(home, detached, monkeypatch, capsys):
    feed(monkeypatch, {"stop_hook_active": False, "last_assistant_message": "Hola mundo."})
    assert main(["hook", "claude"]) == 0
    assert detached == ["Hola mundo."]
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_hook_with_disabled_config_spawns_nothing(home, monkeypatch, capsys):
    config.save({"enabled": False})
    spawned = []
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", lambda *a, **k: spawned.append(a))
    feed(monkeypatch, {"last_assistant_message": "Hola."})
    assert main(["hook", "claude"]) == 0
    assert spawned == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        "",
        "[]",
        {},
        {"last_assistant_message": None},
        {"last_assistant_message": 42},
        {"last_assistant_message": ["x"]},
    ],
)
def test_hook_malformed_or_missing_message_is_silent_exit_zero(home, detached, monkeypatch, capsys, payload):
    feed(monkeypatch, payload)
    assert main(["hook", "claude"]) == 0
    assert detached == []
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_stdout_empty_and_stderr_has_no_reply_text_even_when_spawn_fails(home, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("SECRETMARKER")

    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", boom)
    feed(monkeypatch, {"last_assistant_message": "SECRETMARKER reply"})
    assert main(["hook", "claude"]) == 0
    out = capsys.readouterr()
    assert out.out == ""
    assert "SECRETMARKER" not in out.err


def test_real_subprocess_hook_exits_fast_with_empty_stdout(tmp_path):
    import os
    import subprocess as sp
    import time

    env = {**os.environ, "AGENT_VOICE_HOME": str(tmp_path)}
    (tmp_path / "config.json").write_text('{"enabled": false}')
    stop = json.dumps(
        {
            "session_id": "abc123",
            "transcript_path": "/x/transcript.jsonl",
            "cwd": "/x",
            "permission_mode": "default",
            "hook_event_name": "Stop",
            "last_assistant_message": "Terminé la tarea.",
            "stop_hook_active": False,
        }
    )
    start = time.monotonic()
    result = sp.run(
        [sys.executable, "-m", "agent_voice", "hook", "claude"],
        input=stop, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert time.monotonic() - start < 2


def test_hook_when_disabled_still_drains_stdin(home, monkeypatch):
    config.save({"enabled": False})
    reads = []

    class Tracked(io.StringIO):
        def read(self, *a):
            reads.append(1)
            return super().read(*a)

    monkeypatch.setattr(sys, "stdin", Tracked(json.dumps({"last_assistant_message": "Hola."})))
    assert main(["hook", "claude"]) == 0
    assert reads == [1]


class FakeDetector:
    pane_id = "w1:p1"

    def __init__(self, result):
        self.result = result
        self.queried = 0

    def is_focused(self):
        self.queried += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def gated(home, detached, monkeypatch):
    """A detector is present; records waiting spawns and waiter cancellations."""
    state = {"detector": None, "waiting": [], "cancelled": []}
    monkeypatch.setattr("agent_voice.focus.detect_from_env", lambda env, **k: state["detector"])
    monkeypatch.setattr(
        "agent_voice.cli._detach_waiting",
        lambda args, raw, pane: state["waiting"].append((raw, pane)) or 0,
    )
    monkeypatch.setattr(
        "agent_voice.waiter.cancel", lambda pane, **k: state["cancelled"].append(pane)
    )
    state["detached"] = detached
    return state


def run_gated(monkeypatch, gated, result):
    gated["detector"] = FakeDetector(result)
    feed(monkeypatch, {"last_assistant_message": "Hola."})
    assert main(["hook", "claude"]) == 0


def test_hook_speaks_now_when_the_session_has_focus_and_cancels_its_pending_reply(
    monkeypatch, gated
):
    run_gated(monkeypatch, gated, True)
    assert gated["detached"] == ["Hola."]
    assert gated["waiting"] == []
    assert gated["cancelled"] == ["w1:p1"]


def test_hook_keeps_the_reply_pending_when_the_session_has_no_focus(monkeypatch, gated):
    run_gated(monkeypatch, gated, False)
    assert gated["detached"] == []
    assert gated["waiting"] == [("Hola.", "w1:p1")]


@pytest.mark.parametrize("unknown", [None, RuntimeError("boom"), OSError("x")])
def test_hook_treats_unknown_focus_as_focused(monkeypatch, gated, unknown):
    run_gated(monkeypatch, gated, unknown)
    assert gated["detached"] == ["Hola."]
    assert gated["waiting"] == []


def test_hook_with_a_vanished_pane_speaks_now(monkeypatch, gated):
    from agent_voice import focus

    run_gated(monkeypatch, gated, focus.PaneGoneError("w1:p1"))
    assert gated["detached"] == ["Hola."]


def test_hook_when_disabled_never_queries_the_detector(monkeypatch, gated):
    config.save({"enabled": False})
    gated["detector"] = FakeDetector(False)
    feed(monkeypatch, {"last_assistant_message": "Hola."})
    assert main(["hook", "claude"]) == 0
    assert gated["detector"].queried == 0
    assert gated["waiting"] == [] and gated["detached"] == []


def test_hook_survives_a_failing_waiter_spawn(monkeypatch, gated, capsys):
    def boom(*a, **k):
        raise OSError("SECRETMARKER")

    monkeypatch.setattr("agent_voice.cli._detach_waiting", boom)
    run_gated(monkeypatch, gated, False)
    out = capsys.readouterr()
    assert out.out == "" and "SECRETMARKER" not in out.err


def test_hook_focus_check_runs_under_a_total_half_second_budget(home, detached, monkeypatch):
    captured = {}

    def detect(env, **kw):
        captured["run"] = kw.get("run")
        return None

    monkeypatch.setattr("agent_voice.focus.detect_from_env", detect)
    seen = []
    monkeypatch.setattr("agent_voice.focus._run", lambda argv, timeout=2.0: seen.append(timeout))
    feed(monkeypatch, {"last_assistant_message": "Hola."})
    assert main(["hook", "claude"]) == 0
    captured["run"](["lsappinfo", "front"])
    assert seen and seen[0] <= 0.5
