import argparse
import io
import os
import sys
from pathlib import Path

import pytest

from agent_voice import cli, config
from agent_voice.cli import main


def test_version_prints_and_returns_zero(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "agent-voice 0.1.0\n"


def test_no_arguments_prints_nothing_and_returns_zero(capsys):
    assert main([]) == 0
    assert capsys.readouterr().out == ""


PY = [sys.executable, *(["-P"] if sys.version_info >= (3, 11) else [])]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VOICE_HOME", str(tmp_path))
    return tmp_path


def test_on_persists_enabled(home):
    assert main(["on"]) == 0
    assert config.load()["enabled"] is True


def test_off_persists_disabled_and_stops_player(home, monkeypatch):
    stops = []
    monkeypatch.setattr("agent_voice.player.stop", lambda *a, **k: stops.append(1))
    config.save({"enabled": True})
    assert main(["off"]) == 0
    assert config.load()["enabled"] is False
    assert stops == [1]


def test_stop_command_calls_player_stop(home, monkeypatch):
    stops = []
    monkeypatch.setattr("agent_voice.player.stop", lambda *a, **k: stops.append(1))
    assert main(["stop"]) == 0
    assert stops == [1]
    assert not (home / "config.json").exists()


def test_pause_command_calls_player_pause(home, monkeypatch):
    calls = []
    monkeypatch.setattr("agent_voice.player.pause", lambda *a, **k: calls.append(1))
    assert main(["pause"]) == 0
    assert calls == [1]
    assert not (home / "config.json").exists()


def test_status_shows_defaults_and_missing_models(home, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "disabled" in out
    assert "voice: em_alex" in out
    assert "model: fp32" in out
    assert "speed: 1.0" in out
    assert "lang: es-419" in out
    assert "model files: missing" in out


def test_status_reports_enabled_and_present_model_files(home, monkeypatch, capsys):
    config.save({"enabled": True, "model": "int8"})
    seen = []
    monkeypatch.setattr("agent_voice.models.resolve", lambda v, **k: seen.append(v) or ("m", "v"))
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "enabled"
    assert "model files: ok" in out
    assert seen == ["int8"]


def test_voice_without_name_prints_current_and_available(home, capsys):
    config.save({"voice": "em_santa"})
    assert main(["voice"]) == 0
    out = capsys.readouterr().out
    assert "current: em_santa" in out
    for name in ("ef_dora", "em_alex", "em_santa"):
        assert name in out


def test_voice_with_name_persists_it(home, capsys):
    assert main(["voice", "ef_dora"]) == 0
    assert config.load()["voice"] == "ef_dora"


def test_voice_with_unknown_name_fails_and_does_not_persist(home, capsys):
    assert main(["voice", "nope"]) != 0
    err = capsys.readouterr().err
    assert "nope" in err and "em_alex" in err
    assert config.load()["voice"] == "em_alex"


def test_model_without_arg_prints_current_and_variants(home, capsys):
    assert main(["model"]) == 0
    out = capsys.readouterr().out
    assert "current: fp32" in out
    for name in ("fp32", "fp16", "int8"):
        assert name in out


def test_model_set_persists_without_hint_when_files_present(home, monkeypatch, capsys):
    monkeypatch.setattr("agent_voice.models.resolve", lambda v, **k: ("m", "v"))
    assert main(["model", "int8"]) == 0
    assert config.load()["model"] == "int8"
    assert capsys.readouterr().err == ""


def test_model_set_hints_download_when_files_missing_but_still_persists(home, capsys):
    assert main(["model", "fp16"]) == 0
    assert config.load()["model"] == "fp16"
    assert "agent-voice download --model fp16" in capsys.readouterr().err


def test_model_unknown_variant_fails_and_does_not_persist(home, capsys):
    assert main(["model", "huge"]) != 0
    err = capsys.readouterr().err
    assert "huge" in err and "fp32" in err
    assert config.load()["model"] == "fp32"


def test_download_uses_configured_model_with_progress_on_stderr(home, monkeypatch, capsys):
    config.save({"model": "int8"})
    calls = []

    def fake_download(variant, *, progress_cb=None, **kw):
        calls.append(variant)
        progress_cb("kokoro.onnx", 50, 100)
        return ("m", "v")

    monkeypatch.setattr("agent_voice.models.download", fake_download)
    assert main(["download"]) == 0
    captured = capsys.readouterr()
    assert calls == ["int8"]
    assert "kokoro.onnx" in captured.err and "50%" in captured.err


def test_download_model_flag_overrides_config(home, monkeypatch):
    calls = []
    monkeypatch.setattr("agent_voice.models.download", lambda v, **k: calls.append(v))
    assert main(["download", "--model", "fp16"]) == 0
    assert calls == ["fp16"]


def test_download_failure_returns_nonzero(home, monkeypatch, capsys):
    def boom(v, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("agent_voice.models.download", boom)
    assert main(["download"]) == 1
    assert "offline" in capsys.readouterr().err


class Spy:
    """Fakes models.resolve, engine.Engine/to_wav_bytes and player.speak."""

    def __init__(self, monkeypatch):
        self.engines = []
        self.synth_calls = []
        self.speaks = []
        self.resolved = []
        spy = self

        class FakeEngine:
            def __init__(self, model_path, voices_path, **kw):
                spy.engines.append((model_path, voices_path))

            def synthesize(self, text, **kw):
                spy.synth_calls.append((text, kw))
                return "samples", 24000

        monkeypatch.setattr("agent_voice.engine.Engine", FakeEngine)
        monkeypatch.setattr(
            "agent_voice.engine.to_wav_bytes", lambda samples, sr: b"WAV:" + str(sr).encode()
        )
        monkeypatch.setattr(
            "agent_voice.models.resolve",
            lambda v, **k: spy.resolved.append(v) or ("/m.onnx", "/v.bin"),
        )

        def fake_speak(chunks, synth, **kw):
            spy.speaks.append((list(chunks), synth))
            return True

        monkeypatch.setattr("agent_voice.player.speak", fake_speak)


@pytest.fixture
def spy(monkeypatch):
    return Spy(monkeypatch)


def stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_speak_when_disabled_does_nothing_and_exits_zero(home, spy, monkeypatch, capsys):
    stdin(monkeypatch, "Hola mundo.")
    assert main(["speak"]) == 0
    assert spy.speaks == [] and spy.resolved == []
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_speak_with_nothing_speakable_does_not_synthesize(home, spy, monkeypatch):
    config.save({"enabled": True})
    stdin(monkeypatch, "```\ncode only\n```")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [])
    assert main(["speak"]) == 0
    assert spy.speaks == [] and spy.resolved == []


def test_speak_happy_path_wires_chunks_synth_and_player_with_config(home, spy, monkeypatch):
    config.save({"enabled": True, "voice": "em_santa", "speed": 1.2, "lang": "es-419", "model": "int8"})
    stdin(monkeypatch, "Hola mundo. Adios.")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: t.split(". "))
    assert main(["speak"]) == 0
    assert spy.resolved == ["int8"]
    assert spy.engines == [("/m.onnx", "/v.bin")]
    (chunks, synth), = spy.speaks
    assert chunks == ["Hola mundo", "Adios."]
    assert synth("Hola mundo") == b"WAV:24000"
    assert spy.synth_calls == [("Hola mundo", {"voice": "em_santa", "speed": 1.2, "lang": "es-419"})]


def test_speak_cli_flags_override_config(home, spy, monkeypatch):
    config.save({"enabled": True})
    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    argv = ["speak", "--voice", "ef_dora", "--speed", "0.8", "--lang", "es", "--model", "fp16"]
    assert main(argv) == 0
    assert spy.resolved == ["fp16"]
    (_, synth), = spy.speaks
    synth("Hola.")
    assert spy.synth_calls == [("Hola.", {"voice": "ef_dora", "speed": 0.8, "lang": "es"})]


def test_speak_missing_models_is_swallowed_with_generic_message(home, spy, monkeypatch, capsys):
    from agent_voice.models import ModelsMissingError

    config.save({"enabled": True})
    stdin(monkeypatch, "SECRETMARKER dice algo.")

    def missing(v, **k):
        raise ModelsMissingError(f"SECRETMARKER {v}")

    monkeypatch.setattr("agent_voice.models.resolve", missing)
    assert main(["speak"]) == 0
    err = capsys.readouterr().err
    assert "agent-voice: model files missing; run: agent-voice download --model fp32" in err
    assert "SECRETMARKER" not in err


@pytest.mark.parametrize("stage", ["synth", "play", "chunks"])
def test_speak_swallows_any_exception_without_leaking_text(home, spy, monkeypatch, capsys, stage):
    config.save({"enabled": True})
    stdin(monkeypatch, "SECRETMARKER dice algo.")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])

    def leak(*a, **k):
        raise RuntimeError("SECRETMARKER boom")

    if stage == "play":
        monkeypatch.setattr("agent_voice.player.speak", leak)
    elif stage == "chunks":
        monkeypatch.setattr("agent_voice.text.chunks", leak)
    else:
        monkeypatch.setattr("agent_voice.engine.Engine", leak)
    assert main(["speak"]) == 0
    err = capsys.readouterr().err
    assert err.startswith("agent-voice: could not speak")
    assert "RuntimeError" in err
    assert "SECRETMARKER" not in err


class FakeStdin:
    def __init__(self):
        self.data = None
        self.closed = False

    def write(self, data):
        if not isinstance(data, bytes):
            raise TypeError("a bytes-like object is required")
        self.data = data

    def close(self):
        self.closed = True


class FakePopen:
    instances = []

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.pid = 4242
        self.stdin = FakeStdin()
        self.waited = False
        FakePopen.instances.append(self)

    def wait(self, *a, **k):
        self.waited = True


def test_detach_spawns_session_child_with_text_on_stdin_and_returns(home, spy, monkeypatch):
    import subprocess

    FakePopen.instances = []
    config.save({"enabled": True})
    stdin(monkeypatch, "Hola SECRETMARKER.")
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    before = sorted(p.name for p in home.iterdir())
    argv = ["speak", "--detach", "--voice", "ef_dora", "--speed", "0.9", "--lang", "es", "--model", "int8"]
    assert main(argv) == 0
    (child,) = FakePopen.instances
    assert child.argv == [
        *PY, "-m", "agent_voice", "speak",
        "--voice", "ef_dora", "--speed", "0.9", "--lang", "es", "--model", "int8",
    ]
    assert child.kwargs["start_new_session"] is True
    assert child.kwargs["stdin"] == subprocess.PIPE
    assert child.kwargs["stdout"] == subprocess.DEVNULL
    assert child.kwargs["stderr"] == subprocess.DEVNULL
    assert child.stdin.data == b"Hola SECRETMARKER." and child.stdin.closed
    assert not child.waited
    assert spy.speaks == []
    assert sorted(p.name for p in home.iterdir()) == before


def test_detach_real_pipe_delivers_utf8_text(home, tmp_path, monkeypatch):
    import subprocess

    config.save({"enabled": True})
    out = tmp_path / "out.txt"
    stdin(monkeypatch, "¿Querés? ñ")
    real, kids = subprocess.Popen, []

    def popen(argv, **kw):
        code = "import sys;open(sys.argv[1],'w',encoding='utf-8').write(sys.stdin.read())"
        kids.append(real([sys.executable, "-c", code, str(out)], **kw))
        return kids[0]

    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", popen)
    assert main(["speak", "--detach"]) == 0
    assert kids[0].wait(timeout=30) == 0
    assert out.read_text(encoding="utf-8") == "¿Querés? ñ"


def test_detach_when_disabled_spawns_nothing(home, monkeypatch):
    FakePopen.instances = []
    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    assert main(["speak", "--detach"]) == 0
    assert FakePopen.instances == []


def test_detach_without_options_passes_no_flags_and_swallows_spawn_errors(home, monkeypatch, capsys):
    FakePopen.instances = []
    config.save({"enabled": True})
    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    assert main(["speak", "--detach"]) == 0
    assert FakePopen.instances[0].argv == [*PY, "-m", "agent_voice", "speak"]

    def boom(*a, **k):
        raise OSError("SECRETMARKER")

    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", boom)
    assert main(["speak", "--detach"]) == 0
    err = capsys.readouterr().err
    assert "OSError" in err and "SECRETMARKER" not in err


@pytest.mark.integration
def test_python_dash_m_status_with_temp_home(tmp_path):
    import os
    import subprocess as sp

    env = {**os.environ, "AGENT_VOICE_HOME": str(tmp_path)}
    (tmp_path / "config.json").write_text('{"enabled": true, "voice": "ef_dora"}')
    result = sp.run(
        [sys.executable, "-m", "agent_voice", "status"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "enabled"
    assert "voice: ef_dora" in lines
    assert "model files: missing" in lines


# --- repeat ---------------------------------------------------------------


@pytest.fixture
def last(monkeypatch):
    calls = []

    def fake(config_dirs, cwd=None, nth=1):
        calls.append((list(config_dirs), cwd))
        fake.nths.append(nth)
        return fake.reply

    fake.reply = "Hola SECRETMARKER."
    fake.calls = calls
    fake.nths = []
    monkeypatch.setattr("agent_voice.adapters.claude.last_reply", fake)
    return fake


def test_repeat_speaks_last_reply_even_when_disabled(home, spy, last, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/cfg")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    assert config.load()["enabled"] is False
    assert main(["repeat"]) == 0
    assert last.calls == [([Path("/cfg")], os.getcwd())]
    (chunks, _), = spy.speaks
    assert chunks == ["Hola SECRETMARKER."]


def test_repeat_config_dirs_and_overrides(home, spy, last, monkeypatch):
    config.save({"voice": "em_santa", "speed": 1.2})
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    argv = ["repeat", "--config-dir", "/a", "--config-dir", "/b", "--voice", "ef_dora",
            "--speed", "0.8", "--lang", "es", "--model", "fp16"]
    assert main(argv) == 0
    assert last.calls == [(["/a", "/b"], os.getcwd())]
    assert spy.resolved == ["fp16"]
    (_, synth), = spy.speaks
    synth("x")
    assert spy.synth_calls == [("x", {"voice": "ef_dora", "speed": 0.8, "lang": "es"})]


def test_repeat_uses_config_defaults_without_overrides(home, spy, last, monkeypatch):
    config.save({"voice": "em_santa", "speed": 1.2, "model": "int8"})
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    assert main(["repeat", "--config-dir", "/a"]) == 0
    assert spy.resolved == ["int8"]
    (_, synth), = spy.speaks
    synth("x")
    assert spy.synth_calls == [("x", {"voice": "em_santa", "speed": 1.2, "lang": "es-419"})]


def test_repeat_nothing_found_exits_one_without_speaking(home, spy, last, capsys):
    last.reply = None
    assert main(["repeat", "--config-dir", "/a"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "agent-voice: no previous reply found\n"
    assert captured.out == "" and spy.speaks == []


def test_repeat_failure_is_text_free(home, spy, last, monkeypatch, capsys):
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])

    def boom(*a, **k):
        raise RuntimeError("SECRETMARKER boom")

    monkeypatch.setattr("agent_voice.player.speak", boom)
    assert main(["repeat", "--config-dir", "/a"]) == 1
    err = capsys.readouterr().err
    assert err == "agent-voice: could not repeat (RuntimeError)\n"


def test_repeat_reader_failure_is_text_free(home, spy, last, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("SECRETMARKER /path")

    monkeypatch.setattr("agent_voice.adapters.claude.last_reply", boom)
    assert main(["repeat", "--config-dir", "/a"]) == 1
    assert capsys.readouterr().err == "agent-voice: could not repeat (OSError)\n"


def test_repeat_nothing_speakable_is_quiet_success(home, spy, last, monkeypatch):
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [])
    assert main(["repeat", "--config-dir", "/a"]) == 0
    assert spy.speaks == []


def test_repeat_detach_hands_text_to_child_that_ignores_disabled_flag(home, spy, last, monkeypatch):
    FakePopen.instances = []
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    assert main(["repeat", "--config-dir", "/a", "--detach", "--voice", "ef_dora"]) == 0
    (child,) = FakePopen.instances
    assert child.argv == [
        *PY, "-m", "agent_voice", "speak", "--voice", "ef_dora", "--always",
    ]
    assert child.stdin.data == b"Hola SECRETMARKER." and child.stdin.closed
    assert spy.speaks == []


def test_speak_always_flag_speaks_even_when_disabled(home, spy, monkeypatch):
    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    assert main(["speak", "--always"]) == 0
    assert len(spy.speaks) == 1


# --- T11 hardening ---------------------------------------------------------


class TrackedStdin(io.StringIO):
    reads = 0

    def read(self, *a):
        TrackedStdin.reads += 1
        return super().read(*a)


@pytest.mark.parametrize("error", [None, OSError("disk"), ValueError("bad variant")])
def test_status_reports_missing_for_expected_errors(home, monkeypatch, capsys, error):
    def resolve(variant, **k):
        raise error or __import__("agent_voice.models", fromlist=["x"]).ModelsMissingError("gone")

    monkeypatch.setattr("agent_voice.models.resolve", resolve)
    assert main(["status"]) == 0
    assert "model files: missing" in capsys.readouterr().out


def test_status_does_not_mask_unexpected_errors_as_missing(home, monkeypatch):
    def resolve(variant, **k):
        raise RuntimeError("real bug")

    monkeypatch.setattr("agent_voice.models.resolve", resolve)
    with pytest.raises(RuntimeError, match="real bug"):
        main(["status"])


def test_detach_when_disabled_still_drains_stdin(home, monkeypatch):
    TrackedStdin.reads = 0
    monkeypatch.setattr(sys, "stdin", TrackedStdin("Hola."))
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    FakePopen.instances = []
    assert main(["speak", "--detach"]) == 0
    assert TrackedStdin.reads == 1 and FakePopen.instances == []


def test_speak_always_detach_forwards_always_to_the_child(home, monkeypatch):
    FakePopen.instances = []
    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    assert config.load()["enabled"] is False
    assert main(["speak", "--always", "--detach"]) == 0
    (child,) = FakePopen.instances
    assert child.argv == [*PY, "-m", "agent_voice", "speak", "--always"]


def test_repeat_without_flag_uses_recorded_claude_config_dirs(home, spy, last, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/env")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    config.save({"claude_config_dirs": ["/work", "/personal"]})
    assert main(["repeat"]) == 0
    assert last.calls == [(["/work", "/personal"], os.getcwd())]


def test_repeat_flag_overrides_recorded_dirs_and_empty_list_uses_env_default(home, spy, last, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/env")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    config.save({"claude_config_dirs": ["/work"]})
    assert main(["repeat", "--config-dir", "/x"]) == 0
    config.save({"claude_config_dirs": []})
    assert main(["repeat"]) == 0
    assert [c[0] for c in last.calls] == [["/x"], [Path("/env")]]


class FocusScript:
    pane_id = "w1:p1"

    def __init__(self, *states, on_call=None):
        self.states = list(states)
        self.on_call = on_call

    def is_focused(self):
        if self.on_call:
            self.on_call()
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


@pytest.fixture
def focus_env(monkeypatch):
    """Install a scripted detector and make the wait loop instant."""
    state = {"detector": None, "released": [], "sleeps": []}
    monkeypatch.setattr("agent_voice.focus.detect_from_env", lambda env, **k: state["detector"])
    monkeypatch.setattr("agent_voice.waiter.time.sleep", lambda s: state["sleeps"].append(s))
    monkeypatch.setattr(
        "agent_voice.waiter.release", lambda pane, **k: state["released"].append(pane)
    )
    return state


def test_speak_wait_focus_without_a_detector_speaks_immediately(home, spy, focus_env, monkeypatch):
    config.save({"enabled": True})
    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    assert main(["speak", "--wait-focus"]) == 0
    assert [c for c, _ in spy.speaks] == [["Hola."]]
    assert focus_env["sleeps"] == []


def test_speak_wait_focus_reads_stdin_first_then_speaks_once_focused(
    home, spy, focus_env, monkeypatch
):
    config.save({"enabled": True})
    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    consumed = []

    def probe():
        consumed.append(sys.stdin.tell() > 0)  # the whole text was read before polling

    focus_env["detector"] = FocusScript(False, False, True, on_call=probe)
    assert main(["speak", "--wait-focus"]) == 0
    assert consumed == [True, True, True]
    assert focus_env["sleeps"] == [0.5, 0.5]
    assert [c for c, _ in spy.speaks] == [["Hola."]]
    assert focus_env["released"] == ["w1:p1"]


def test_speak_wait_focus_drops_the_reply_when_disabled_while_waiting(
    home, spy, focus_env, monkeypatch
):
    config.save({"enabled": True})
    stdin(monkeypatch, "Hola.")
    focus_env["detector"] = FocusScript(False, on_call=lambda: config.save({"enabled": False}))
    assert main(["speak", "--wait-focus"]) == 0
    assert spy.speaks == []
    assert focus_env["released"] == ["w1:p1"]


def test_speak_wait_focus_honours_pending_max_wait(home, spy, focus_env, monkeypatch):
    config.save({"enabled": True, "pending_max_wait_s": 0.05})
    stdin(monkeypatch, "Hola.")
    focus_env["detector"] = FocusScript(False)
    assert main(["speak", "--wait-focus"]) == 0
    assert spy.speaks == []
    assert focus_env["sleeps"]  # it did wait (sleeps are instant here), then gave up
    assert focus_env["released"] == ["w1:p1"]


def test_speak_wait_focus_when_disabled_drains_stdin_and_never_polls(home, focus_env, monkeypatch):
    stdin(monkeypatch, "Hola.")
    focus_env["detector"] = FocusScript(True, on_call=lambda: pytest.fail("polled"))
    assert main(["speak", "--wait-focus"]) == 0
    assert sys.stdin.tell() > 0


def test_off_cancels_every_pending_waiter(home, monkeypatch):
    cancelled = []
    monkeypatch.setattr("agent_voice.player.stop", lambda *a, **k: None)
    monkeypatch.setattr("agent_voice.waiter.cancel_all", lambda **k: cancelled.append(1))
    assert main(["off"]) == 0
    assert cancelled == [1]


def test_detach_waiting_spawns_a_waiter_child_and_registers_its_pid(home, monkeypatch):
    from agent_voice import cli

    FakePopen.instances = []
    registered = []
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    monkeypatch.setattr(
        "agent_voice.waiter.register", lambda pane, pid, **k: registered.append((pane, pid))
    )
    import argparse

    opts = argparse.Namespace(voice=None, speed=None, lang=None, model=None)
    assert cli._detach_waiting(opts, "Hola SECRETMARKER.", "w1:p1") == 0
    (child,) = FakePopen.instances
    assert child.argv == [*PY, "-m", "agent_voice", "speak", "--wait-focus"]
    assert child.stdin.data == b"Hola SECRETMARKER." and child.stdin.closed
    assert registered == [("w1:p1", child.pid)]


# --- toggle ---------------------------------------------------------------


def test_toggle_flips_the_flag_and_prints_the_new_state(home, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("agent_voice.player.stop", lambda *a, **k: calls.append("stop"))
    monkeypatch.setattr("agent_voice.waiter.cancel_all", lambda **k: calls.append("cancel"))
    config.save({"enabled": False})
    assert main(["toggle"]) == 0
    assert config.load()["enabled"] is True
    assert capsys.readouterr().out == "enabled\n"
    assert calls == []  # turning on never stops anything
    assert main(["toggle"]) == 0
    assert config.load()["enabled"] is False
    assert capsys.readouterr().out == "disabled\n"
    assert calls == ["stop", "cancel"]  # turning off behaves exactly like `off`
    assert main(["toggle"]) == 0
    assert config.load()["enabled"] is True
    assert capsys.readouterr().out == "enabled\n"


# --- keys skhd ------------------------------------------------------------


def test_keys_skhd_prints_the_exact_block(monkeypatch, capsys):
    monkeypatch.setattr("agent_voice.cli._executable", lambda: "/opt/av/bin/agent-voice")
    assert main(["keys", "skhd"]) == 0
    assert capsys.readouterr().out == (
        "# agent-voice\n"
        "ctrl + alt - q : /opt/av/bin/agent-voice stop\n"
        "ctrl + alt - r : /opt/av/bin/agent-voice repeat --detach\n"
        "ctrl + alt - v : /opt/av/bin/agent-voice toggle\n"
        "ctrl + alt - p : /opt/av/bin/agent-voice pause\n"
    )


def test_keys_skhd_shell_quotes_a_path_with_a_space(monkeypatch, capsys):
    monkeypatch.setattr("agent_voice.cli._executable", lambda: "/Users/a b/bin/agent-voice")
    assert main(["keys", "skhd"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "ctrl + alt - q : '/Users/a b/bin/agent-voice' stop"
    assert lines[2] == "ctrl + alt - r : '/Users/a b/bin/agent-voice' repeat --detach"
    assert lines[4] == "ctrl + alt - p : '/Users/a b/bin/agent-voice' pause"


def test_keys_skhd_writes_no_files(home, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    assert main(["keys", "skhd"]) == 0
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_keys_unknown_target_exits_two_listing_supported(capsys):
    assert main(["keys", "karabiner"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "agent-voice: unknown target 'karabiner'; supported: skhd\n"


def test_executable_is_the_running_agent_voice_absolute_path(monkeypatch):
    from agent_voice import cli

    monkeypatch.setattr(cli.sys, "argv", ["bin/agent-voice"])
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert os.path.isabs(cli._executable())
    monkeypatch.setattr(cli.sys, "argv", ["/x/y/agent-voice"])
    assert cli._executable() == "/x/y/agent-voice"
    monkeypatch.setattr(cli.sys, "argv", ["/x/y/__main__.py"])
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/found/agent-voice")
    assert cli._executable() == "/found/agent-voice"


@pytest.mark.parametrize("argv, nth", [
    (["repeat"], 1), (["repeat", "1"], 1), (["repeat", "3"], 3),
    (["repeat", "2", "--voice", "ef_dora"], 2),
])
def test_repeat_forwards_the_index_to_the_reader(home, spy, last, monkeypatch, argv, nth):
    monkeypatch.setattr("agent_voice.text.chunks", lambda t: [t])
    assert main(argv + ["--config-dir", "/a"]) == 0
    assert last.nths == [nth]


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_repeat_rejects_an_index_below_one_text_free(home, spy, last, capsys, bad):
    assert main(["repeat", bad]) != 0
    assert last.nths == [] and spy.speaks == []
    err = capsys.readouterr().err
    assert "SECRETMARKER" not in err and "Hola" not in err


def test_repeat_out_of_range_index_is_the_text_free_no_reply_error(home, spy, last, capsys):
    last.reply = None
    assert main(["repeat", "9", "--config-dir", "/a"]) == 1
    assert capsys.readouterr().err == "agent-voice: no previous reply found\n"
    assert spy.speaks == []


def test_repeat_index_is_forwarded_to_the_pane_session_and_project_readers(
    home, spy, monkeypatch, tmp_path
):
    from agent_voice.adapters import claude

    _session(tmp_path, "-p", SID, "only one reply", 1000)
    _herdr_pane(monkeypatch, _claude_pane())
    FakePopen.instances = []
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    assert main(["repeat", "2", "--detach", "--config-dir", str(tmp_path)]) == 1
    assert FakePopen.instances == []
    seen = []
    real = claude.session_reply
    monkeypatch.setattr(
        "agent_voice.adapters.claude.session_reply",
        lambda d, sid, nth=1: seen.append(nth) or real(d, sid, nth=nth),
    )
    assert main(["repeat", "1", "--detach", "--config-dir", str(tmp_path)]) == 0
    assert seen == [1]


# --- repeat follows the focused herdr session --------------------------------

SID = "86c4cf97-e5ba-4c56-b68f-24f657c78e54"


def _session(cfg_dir, project, name, reply, mtime):
    import json

    folder = Path(cfg_dir) / "projects" / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    line = {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "text", "text": reply}]}}
    path.write_text(json.dumps(line) + "\n")
    os.utime(path, (mtime, mtime))


def _herdr_pane(monkeypatch, pane):
    monkeypatch.setattr("agent_voice.herdr.current_pane", lambda env: pane)


def _claude_pane(**extra):
    return {"pane_id": "w1:p1", "focused": True, "agent": "claude", "cwd": "/w/app",
            "agent_session": {"agent": "claude", "kind": "id", "value": SID}, **extra}


def _detached_text(monkeypatch):
    FakePopen.instances = []
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)

    def got():
        (child,) = FakePopen.instances
        return child.stdin.data.decode()

    return got


def test_repeat_follows_the_focused_panes_session_across_config_dirs(home, spy, monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _session(a, "-w-app", "newest-here", "WRONG newest in cwd project", 3000)
    _session(b, "-other", SID, "right: the focused session", 1000)
    _herdr_pane(monkeypatch, _claude_pane())
    got = _detached_text(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert main(["repeat", "--detach", "--config-dir", str(a), "--config-dir", str(b)]) == 0
    assert got() == "right: the focused session"


def test_repeat_falls_back_to_the_panes_cwd_project_without_a_session(home, spy, monkeypatch, tmp_path):
    from agent_voice.adapters import claude

    _session(tmp_path, claude.encode_project_dir("/w/app"), "s1", "pane cwd project", 1000)
    _session(tmp_path, claude.encode_project_dir(os.getcwd()), "s2", "WRONG process cwd", 2000)
    _session(tmp_path, "-zzz", "s3", "WRONG newest overall", 3000)
    pane = {"pane_id": "w1:p1", "focused": True, "cwd": "/w/app"}
    _herdr_pane(monkeypatch, pane)
    got = _detached_text(monkeypatch)
    assert main(["repeat", "--detach", "--config-dir", str(tmp_path)]) == 0
    assert got() == "pane cwd project"


def test_repeat_session_file_missing_then_uses_the_panes_cwd_project(home, spy, monkeypatch, tmp_path):
    from agent_voice.adapters import claude

    _session(tmp_path, claude.encode_project_dir("/w/app"), "s1", "pane cwd project", 1000)
    _herdr_pane(monkeypatch, _claude_pane())
    got = _detached_text(monkeypatch)
    assert main(["repeat", "--detach", "--config-dir", str(tmp_path)]) == 0
    assert got() == "pane cwd project"


@pytest.mark.parametrize("pane", [_claude_pane(), {"pane_id": "w1:p1", "focused": True, "cwd": "/w/app"}])
@pytest.mark.parametrize("detach", [True, False])
def test_repeat_pane_without_any_transcript_stays_silent_and_exits_one(
    home, spy, monkeypatch, tmp_path, capsys, pane, detach
):
    _session(tmp_path, "-zzz", "s3", "WRONG newest overall", 3000)
    _herdr_pane(monkeypatch, pane)
    FakePopen.instances = []
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    argv = ["repeat", "--config-dir", str(tmp_path)] + (["--detach"] if detach else [])
    assert main(argv) == 1
    assert capsys.readouterr().err == "agent-voice: no previous reply for this pane\n"
    assert FakePopen.instances == [] and spy.speaks == [] and spy.resolved == []


def test_repeat_strictness_also_applies_without_an_explicit_config_dir(home, spy, monkeypatch, capsys):
    monkeypatch.setattr("agent_voice.adapters.claude.default_config_dir", lambda: home / "nothing")
    _herdr_pane(monkeypatch, {"pane_id": "w1:p1", "focused": True, "cwd": "/w/app"})
    assert main(["repeat"]) == 1
    assert capsys.readouterr().err == "agent-voice: no previous reply for this pane\n"
    assert spy.speaks == []


@pytest.mark.parametrize("failure", ["missing", "raises", "no-pane"])
def test_repeat_without_a_usable_herdr_keeps_the_current_behavior(home, spy, monkeypatch, tmp_path, failure):
    from agent_voice.adapters import claude

    _session(tmp_path, claude.encode_project_dir(os.getcwd()), "s1", "process cwd project", 1000)
    _session(tmp_path, "-zzz", "s2", "newest overall", 3000)
    if failure == "raises":
        def boom(env):
            raise RuntimeError("herdr exploded")

        monkeypatch.setattr("agent_voice.herdr.current_pane", boom)
    elif failure == "no-pane":
        _herdr_pane(monkeypatch, None)
    got = _detached_text(monkeypatch)
    assert main(["repeat", "--detach", "--config-dir", str(tmp_path)]) == 0
    assert got() == "process cwd project"


def test_repeat_inside_a_pane_uses_that_panes_session(home, spy, monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    _session(tmp_path, "-p", SID, "pane session reply", 1000)
    _session(tmp_path, "-q", "other", "WRONG newest", 3000)
    calls = []

    def fake_run(argv, timeout=2.0):
        calls.append(argv)
        out = {"result": {"pane": _claude_pane(focused=False)}}
        return SimpleNamespace(stdout=json.dumps(out), returncode=0)

    monkeypatch.setattr("agent_voice.herdr._run", fake_run)
    monkeypatch.setenv("HERDR_PANE_ID", "w9:p2")
    got = _detached_text(monkeypatch)
    assert main(["repeat", "--detach", "--config-dir", str(tmp_path)]) == 0
    assert calls == [["herdr", "pane", "get", "w9:p2"]]
    assert got() == "pane session reply"


def test_repeat_detach_returns_without_loading_the_model(home, spy, monkeypatch, tmp_path):
    import agent_voice

    _session(tmp_path, "-p", SID, "Hola.", 1000)
    _herdr_pane(monkeypatch, _claude_pane())
    got = _detached_text(monkeypatch)
    monkeypatch.delattr(agent_voice, "engine", raising=False)
    monkeypatch.setitem(sys.modules, "agent_voice.engine", None)  # importing it would raise
    assert main(["repeat", "--detach", "--config-dir", str(tmp_path)]) == 0
    assert got() == "Hola."
    assert spy.resolved == [] and spy.speaks == []


# --- pending-wait --------------------------------------------------------------


def test_pending_wait_without_argument_prints_off_by_default(home, capsys):
    assert main(["pending-wait"]) == 0
    assert capsys.readouterr().out == "off\n"


@pytest.mark.parametrize(
    "arg, seconds, shown",
    [("45s", 45, "45s"), ("30m", 1800, "30m"), ("2h", 7200, "2h"), ("90", 90, "90s"),
     ("90m", 5400, "90m"), ("120s", 120, "2m"), ("3600", 3600, "1h")],
)
def test_pending_wait_sets_and_shows_the_limit(home, capsys, arg, seconds, shown):
    assert main(["pending-wait", arg]) == 0
    assert config.load()["pending_max_wait_s"] == seconds
    capsys.readouterr()
    assert main(["pending-wait"]) == 0
    assert capsys.readouterr().out == f"{shown}\n"


def test_pending_wait_off_clears_the_limit(home, capsys):
    config.save({"pending_max_wait_s": 1800})
    assert main(["pending-wait", "off"]) == 0
    assert config.load()["pending_max_wait_s"] == 0
    capsys.readouterr()
    main(["pending-wait"])
    assert capsys.readouterr().out == "off\n"


@pytest.mark.parametrize("arg", ["abc", "1.5h", "10x", "", "m", "0m ", "1d"])
def test_pending_wait_rejects_invalid_values_with_a_usage_hint(home, capsys, arg):
    config.save({"pending_max_wait_s": 60})
    assert main(["pending-wait", arg]) == 2
    err = capsys.readouterr().err
    assert "agent-voice: invalid duration" in err and "30m" in err and "off" in err
    assert config.load()["pending_max_wait_s"] == 60


def test_pending_wait_zero_means_off(home, capsys):
    assert main(["pending-wait", "0"]) == 0
    main(["pending-wait"])
    assert capsys.readouterr().out == "off\n"


def test_status_shows_the_pending_wait(home, capsys):
    main(["status"])
    assert "pending wait: off\n" in capsys.readouterr().out
    config.save({"pending_max_wait_s": 1800})
    main(["status"])
    assert "pending wait: 30m\n" in capsys.readouterr().out


def test_pending_wait_negative_value_is_rejected_by_the_parser(home):
    config.save({"pending_max_wait_s": 60})
    with pytest.raises(SystemExit) as exc:
        main(["pending-wait", "-5m"])
    assert exc.value.code == 2
    assert config.load()["pending_max_wait_s"] == 60


def test_pending_wait_shows_a_fractional_stored_limit_in_seconds(home, capsys):
    config.save({"pending_max_wait_s": 0.5})
    main(["pending-wait"])
    assert capsys.readouterr().out == "0.5s\n"


def test_spawned_child_never_imports_modules_from_the_hook_cwd(home, tmp_path, monkeypatch):
    """A project dir with json.py or agent_voice/ must not get code execution (cwd hijack)."""
    evil = tmp_path / "evil"
    (evil / "agent_voice").mkdir(parents=True)
    marker_pkg, marker_json = tmp_path / "pwned-pkg", tmp_path / "pwned-json"
    (evil / "agent_voice" / "__init__.py").write_text("")
    (evil / "agent_voice" / "__main__.py").write_text(f"open({str(marker_pkg)!r}, 'w')\n")
    (evil / "json.py").write_text(f"open({str(marker_json)!r}, 'w')\n")
    monkeypatch.chdir(evil)
    stdin(monkeypatch, "Hola.")
    config.save({"enabled": True})
    pid = cli._spawn(argparse.Namespace(voice=None, speed=None, lang=None, model=None), "Hola.")
    os.waitpid(pid, 0)
    assert not marker_pkg.exists()
    assert not marker_json.exists()


def test_spawn_runs_child_from_a_safe_cwd_with_safe_path_env(home, monkeypatch):
    FakePopen.instances = []
    monkeypatch.setattr("agent_voice.cli.subprocess.Popen", FakePopen)
    cli._spawn(argparse.Namespace(voice=None, speed=None, lang=None, model=None), "x")
    (child,) = FakePopen.instances
    assert child.kwargs["cwd"] == "/"
    assert child.kwargs["env"]["PYTHONSAFEPATH"] == "1"
    assert ("-P" in child.argv) == (sys.version_info >= (3, 11))


def test_speaker_pid_is_registered_before_text_processing_and_released_after(home, spy, monkeypatch):
    """`stop`/`off` must be able to cut a process that is still cleaning a huge reply."""
    from agent_voice import player

    seen = []

    def chunks(raw):
        seen.append(player._read_pid(player.runtime_dir()))
        return []

    config.save({"enabled": True})
    stdin(monkeypatch, "Hola.")
    monkeypatch.setattr("agent_voice.text.chunks", chunks)
    assert main(["speak"]) == 0
    assert seen == [os.getpid()]
    assert player._read_pid(player.runtime_dir()) is None
