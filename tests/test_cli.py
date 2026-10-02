import io
import os
import sys
from pathlib import Path

import pytest

from agent_voice import config
from agent_voice.cli import main


def test_version_prints_and_returns_zero(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "agent-voice 0.1.0\n"


def test_no_arguments_prints_nothing_and_returns_zero(capsys):
    assert main([]) == 0
    assert capsys.readouterr().out == ""


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
        sys.executable, "-m", "agent_voice", "speak",
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
    assert FakePopen.instances[0].argv == [sys.executable, "-m", "agent_voice", "speak"]

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

    def fake(config_dirs, cwd=None):
        calls.append((list(config_dirs), cwd))
        return fake.reply

    fake.reply = "Hola SECRETMARKER."
    fake.calls = calls
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
        sys.executable, "-m", "agent_voice", "speak", "--voice", "ef_dora", "--always",
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
    assert child.argv == [sys.executable, "-m", "agent_voice", "speak", "--always"]


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
    assert child.argv == [sys.executable, "-m", "agent_voice", "speak", "--wait-focus"]
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
    )


def test_keys_skhd_shell_quotes_a_path_with_a_space(monkeypatch, capsys):
    monkeypatch.setattr("agent_voice.cli._executable", lambda: "/Users/a b/bin/agent-voice")
    assert main(["keys", "skhd"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "ctrl + alt - q : '/Users/a b/bin/agent-voice' stop"
    assert lines[2] == "ctrl + alt - r : '/Users/a b/bin/agent-voice' repeat --detach"


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
