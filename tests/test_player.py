import os
import signal
import stat
import subprocess
import sys
import threading

import pytest

from agent_voice import player

WAIT = 5


def fake_synth(chunk):
    return b"WAV:" + chunk.encode()


class Recorder:
    """Fake `play`: records wav contents and whether the file existed."""

    def __init__(self):
        self.played = []
        self.paths = []

    def __call__(self, path):
        assert os.path.isabs(path)
        with open(path, "rb") as f:
            self.played.append(f.read())
        self.paths.append(path)


@pytest.fixture
def run_dir(tmp_path):
    return tmp_path / "run"


def test_speaks_all_chunks_in_order(run_dir, tmp_path):
    rec = Recorder()
    player.speak(
        ["one", "two", "three"],
        fake_synth,
        play=rec,
        workdir=tmp_path,
        run_dir=run_dir,
        handle_signals=False,
    )
    assert rec.played == [b"WAV:one", b"WAV:two", b"WAV:three"]


def test_synthesis_of_next_chunk_starts_before_current_playback_ends(run_dir, tmp_path):
    second_synth_started = threading.Event()
    seen = {}

    def synth(chunk):
        if chunk == "two":
            second_synth_started.set()
        return fake_synth(chunk)

    def play(path):
        if path.endswith("0.wav"):
            seen["overlap"] = second_synth_started.wait(WAIT)

    player.speak(
        ["one", "two"],
        synth,
        play=play,
        workdir=tmp_path,
        run_dir=run_dir,
        handle_signals=False,
    )
    assert seen["overlap"] is True


def test_failed_chunk_is_skipped_and_its_text_is_not_reported(run_dir, tmp_path, capsys):
    def synth(chunk):
        if chunk == "SECRET sentence":
            raise RuntimeError("boom while reading SECRET sentence")
        return fake_synth(chunk)

    rec = Recorder()
    player.speak(
        ["one", "SECRET sentence", "three"],
        synth,
        play=rec,
        workdir=tmp_path,
        run_dir=run_dir,
        handle_signals=False,
    )
    assert rec.played == [b"WAV:one", b"WAV:three"]
    captured = capsys.readouterr()
    assert "synthesis failed" in captured.err
    assert "SECRET" not in captured.err + captured.out


def test_files_are_deleted_after_each_play_and_the_dir_is_removed(run_dir, tmp_path):
    seen = {"paths": []}

    def play(path):
        seen["dir"] = os.path.dirname(path)
        seen["paths"].append(path)
        # every earlier file was deleted right after its own playback
        assert not any(os.path.exists(p) for p in seen["paths"][:-1])

    player.speak(
        ["one", "two", "three"],
        fake_synth,
        play=play,
        workdir=tmp_path,
        run_dir=run_dir,
        handle_signals=False,
    )
    assert len(seen["paths"]) == 3
    assert not os.path.exists(seen["dir"])


def test_dir_is_removed_and_error_propagates_when_play_raises(run_dir, tmp_path):
    seen = {}

    def play(path):
        seen["dir"] = os.path.dirname(path)
        raise OSError("afplay missing")

    with pytest.raises(OSError):
        player.speak(
            ["one", "two", "three", "four", "five"],
            fake_synth,
            play=play,
            workdir=tmp_path,
            run_dir=run_dir,
            handle_signals=False,
        )
    assert not os.path.exists(seen["dir"])


def test_temp_dir_is_private(run_dir, tmp_path):
    modes = []

    def play(path):
        modes.append(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode))

    player.speak(
        ["one"], fake_synth, play=play, workdir=tmp_path, run_dir=run_dir, handle_signals=False
    )
    assert modes == [0o700]


def test_producer_thread_stops_when_playback_aborts(run_dir, tmp_path):
    producer = []

    def synth(chunk):
        producer.append(threading.current_thread())
        return fake_synth(chunk)

    def play(path):
        raise OSError("afplay missing")

    with pytest.raises(OSError):
        player.speak(
            [str(i) for i in range(10)],
            synth,
            play=play,
            workdir=tmp_path,
            run_dir=run_dir,
            handle_signals=False,
        )
    producer[0].join(WAIT)
    assert not producer[0].is_alive()


def test_runtime_dir_prefers_agent_voice_home():
    env = {"AGENT_VOICE_HOME": "/h", "XDG_STATE_HOME": "/x"}
    assert str(player.runtime_dir(env)) == "/h/run"


def test_runtime_dir_falls_back_to_xdg_state_home():
    assert str(player.runtime_dir({"XDG_STATE_HOME": "/x"})) == "/x/agent-voice"


def test_runtime_dir_defaults_to_local_state_in_home(monkeypatch):
    monkeypatch.setattr(player.Path, "home", classmethod(lambda cls: player.Path("/home/u")))
    assert str(player.runtime_dir({})) == "/home/u/.local/state/agent-voice"


def _pid_file(run_dir):
    return run_dir / "speaker.pid"


def test_pid_file_holds_only_our_identity_while_speaking_and_is_removed_after(run_dir, tmp_path):
    seen = {}

    def play(path):
        seen["content"] = _pid_file(run_dir).read_text()
        seen["mode"] = stat.S_IMODE(run_dir.stat().st_mode)

    player.speak(
        ["one"], fake_synth, play=play, workdir=tmp_path, run_dir=run_dir, handle_signals=False
    )
    # PID plus the OS-reported start time, never any text.
    assert seen["content"].splitlines() == [
        str(os.getpid()),
        player.process_start_time(os.getpid()),
    ]
    assert seen["mode"] == 0o700
    assert not _pid_file(run_dir).exists()


def test_pid_file_of_a_newer_speaker_is_left_alone(run_dir, tmp_path):
    def play(path):
        # a newer speaker took over while we were playing
        _pid_file(run_dir).write_text("999999")

    player.speak(
        ["one"], fake_synth, play=play, workdir=tmp_path, run_dir=run_dir, handle_signals=False
    )
    assert _pid_file(run_dir).read_text() == "999999"


SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.fixture
def sleeper():
    proc = subprocess.Popen(SLEEPER)
    # reap in the background so a terminated child is not left as a zombie
    threading.Thread(target=proc.wait, daemon=True).start()
    yield proc
    if proc.poll() is None:
        proc.kill()


def _write_identity(run_dir, proc, start=None):
    start = player.process_start_time(proc.pid) if start is None else start
    run_dir.mkdir(mode=0o700, exist_ok=True)
    _pid_file(run_dir).write_text(f"{proc.pid}\n{start}")


def test_previous_live_speaker_is_terminated(run_dir, tmp_path, sleeper):
    _write_identity(run_dir, sleeper)
    seen = {}

    def play(path):
        seen["pid_file"] = _pid_file(run_dir).read_text()

    player.speak(
        ["one"], fake_synth, play=play, workdir=tmp_path, run_dir=run_dir, handle_signals=False
    )
    assert sleeper.wait(WAIT) == -signal.SIGTERM
    assert seen["pid_file"].splitlines()[0] == str(os.getpid())


def test_stale_pid_file_is_ignored_and_nothing_is_signalled(run_dir, tmp_path, monkeypatch):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    run_dir.mkdir(mode=0o700)
    _pid_file(run_dir).write_text(str(dead.pid))
    sent = []
    real_kill = os.kill
    monkeypatch.setattr(
        player.os, "kill", lambda pid, sig: (sent.append(sig), real_kill(pid, sig))[1]
    )
    rec = Recorder()
    player.speak(
        ["one"], fake_synth, play=rec, workdir=tmp_path, run_dir=run_dir, handle_signals=False
    )
    assert rec.played == [b"WAV:one"]
    assert signal.SIGTERM not in sent


def test_stop_terminates_the_current_speaker_and_clears_the_pid_file(run_dir, sleeper):
    _write_identity(run_dir, sleeper)
    assert player.stop(run_dir) is True
    assert sleeper.wait(WAIT) == -signal.SIGTERM
    assert not _pid_file(run_dir).exists()


def test_pid_reused_by_an_unrelated_live_process_is_never_signalled_by_speak(
    run_dir, tmp_path, sleeper
):
    _write_identity(run_dir, sleeper, start="Thu Jan  1 00:00:00 1970")
    rec = Recorder()
    player.speak(
        ["one"], fake_synth, play=rec, workdir=tmp_path, run_dir=run_dir, handle_signals=False
    )
    assert rec.played == [b"WAV:one"]
    assert sleeper.poll() is None


def test_pid_reused_by_an_unrelated_live_process_is_never_signalled_by_stop(run_dir, sleeper):
    _write_identity(run_dir, sleeper, start="Thu Jan  1 00:00:00 1970")
    assert player.stop(run_dir) is False
    assert sleeper.poll() is None
    assert not _pid_file(run_dir).exists()


def test_old_format_pid_only_file_is_never_signalled(run_dir, tmp_path, sleeper):
    run_dir.mkdir(mode=0o700)
    _pid_file(run_dir).write_text(str(sleeper.pid))
    assert player.stop(run_dir) is False
    assert sleeper.poll() is None
    assert not _pid_file(run_dir).exists()
    _pid_file(run_dir).write_text(str(sleeper.pid))
    player.speak(
        ["one"], fake_synth, play=Recorder(), workdir=tmp_path, run_dir=run_dir,
        handle_signals=False,
    )
    assert sleeper.poll() is None


def test_unreadable_start_time_is_treated_as_stale_and_nothing_is_signalled(
    run_dir, tmp_path, sleeper
):
    _write_identity(run_dir, sleeper)
    assert player.stop(run_dir, start_time=lambda pid: None) is False
    assert sleeper.poll() is None
    _write_identity(run_dir, sleeper)
    player.speak(
        ["one"], fake_synth, play=Recorder(), workdir=tmp_path, run_dir=run_dir,
        handle_signals=False, start_time=lambda pid: None,
    )
    assert sleeper.poll() is None


def test_process_start_time_is_none_for_a_dead_pid():
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    assert player.process_start_time(dead.pid) is None


def test_process_start_time_is_none_when_ps_is_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("ps")

    monkeypatch.setattr(player.subprocess, "run", boom)
    assert player.process_start_time(os.getpid()) is None


def test_stop_without_a_speaker_returns_false(run_dir):
    assert player.stop(run_dir) is False


def test_stop_with_a_stale_pid_file_returns_false_and_removes_it(run_dir):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    run_dir.mkdir(mode=0o700)
    _pid_file(run_dir).write_text(str(dead.pid))
    assert player.stop(run_dir) is False
    assert not _pid_file(run_dir).exists()


class BlockingPlayer:
    """Fake play that blocks until terminated, like afplay would."""

    def __init__(self):
        self.started = threading.Event()
        self.released = threading.Event()
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        self.started.set()
        self.released.wait(WAIT)

    def terminate(self):
        self.released.set()


def test_sigterm_terminates_playback_and_cleans_up(run_dir, tmp_path):
    blocking = BlockingPlayer()
    previous = signal.getsignal(signal.SIGTERM)
    dirs = []

    def synth(chunk):
        dirs.append(chunk)
        return fake_synth(chunk)

    def send_sigterm():
        assert blocking.started.wait(WAIT)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=send_sigterm, daemon=True).start()
    finished = player.speak(
        ["one", "two", "three"], synth, play=blocking, workdir=tmp_path, run_dir=run_dir
    )
    assert finished is False
    assert blocking.calls == 1
    assert signal.getsignal(signal.SIGTERM) == previous
    assert not _pid_file(run_dir).exists()
    assert [p for p in tmp_path.iterdir() if p.name.startswith("agent-voice-")] == []


def test_speak_reports_completion(run_dir, tmp_path):
    assert (
        player.speak(
            ["one"], fake_synth, play=Recorder(), workdir=tmp_path, run_dir=run_dir,
            handle_signals=False,
        )
        is True
    )


def test_afplay_player_runs_the_command_with_the_file_path_and_waits(tmp_path):
    marker = tmp_path / "ran"
    script = "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])"
    # argv layout: [python, -c, script, <marker>, <path>] -> command carries the marker
    afplay = player.AfplayPlayer([sys.executable, "-c", script, str(marker)])
    afplay("/abs/file.wav")
    assert marker.read_text() == "/abs/file.wav"


def test_afplay_player_terminate_interrupts_a_running_playback():
    afplay = player.AfplayPlayer(SLEEPER)
    done = threading.Event()

    def run():
        afplay("ignored.wav")
        done.set()

    threading.Thread(target=run, daemon=True).start()
    for _ in range(200):
        if afplay._proc is not None:
            break
        threading.Event().wait(0.01)
    afplay.terminate()
    assert done.wait(WAIT)


def test_afplay_player_does_not_start_after_terminate(tmp_path):
    marker = tmp_path / "ran"
    afplay = player.AfplayPlayer(
        [sys.executable, "-c", "import sys, pathlib; pathlib.Path(sys.argv[1]).touch()", str(marker)]
    )
    afplay.terminate()
    afplay("x.wav")
    assert not marker.exists()


def test_default_play_is_afplay(run_dir, tmp_path, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(player, "AfplayPlayer", lambda: rec)
    player.speak(["one"], fake_synth, workdir=tmp_path, run_dir=run_dir, handle_signals=False)
    assert rec.played == [b"WAV:one"]


def test_paths_are_absolute_even_with_a_relative_workdir(run_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    rec = Recorder()  # asserts os.path.isabs(path)
    player.speak(["one"], fake_synth, play=rec, workdir="rel", run_dir=run_dir, handle_signals=False)
    assert rec.played == [b"WAV:one"]


# --- T15 G6/G7 ---------------------------------------------------------------


@pytest.mark.parametrize("pid_text", ["0", "1", "-1"])
def test_pid_file_with_a_reserved_pid_never_signals_anything(run_dir, monkeypatch, pid_text):
    run_dir.mkdir(mode=0o700)
    _pid_file(run_dir).write_text(f"{pid_text}\nsome start")
    sent = []
    monkeypatch.setattr(player.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert player.stop(run_dir, start_time=lambda pid: "some start") is False
    assert sent == []


def test_stop_survives_a_previous_speaker_we_may_not_signal(run_dir, monkeypatch):
    run_dir.mkdir(mode=0o700)
    _pid_file(run_dir).write_text("4242\nsome start")

    def kill(pid, sig):
        if sig != 0:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(player.os, "kill", kill)
    assert player.stop(run_dir, start_time=lambda pid: "some start") is False


def test_wav_files_are_private(run_dir, tmp_path):
    modes = []
    player.speak(
        ["one"], fake_synth, play=lambda p: modes.append(stat.S_IMODE(os.stat(p).st_mode)),
        workdir=tmp_path, run_dir=run_dir, handle_signals=False,
    )
    assert modes == [0o600]


def _stale_dir(root, name, age_s, pid=None):
    d = root / name
    d.mkdir()
    (d / "0.wav").write_bytes(b"WAV")
    t = os.stat(d).st_mtime - age_s
    os.utime(d, (t, t))
    return d


def _dead_pid():
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    return dead.pid


def test_start_sweeps_only_stale_leftovers_of_dead_agent_voice_processes(run_dir, tmp_path, sleeper):
    dead = _dead_pid()
    stale = _stale_dir(tmp_path, f"agent-voice-{dead}-abc", 3600)
    young = _stale_dir(tmp_path, f"agent-voice-{dead}-new", 5)
    alive = _stale_dir(tmp_path, f"agent-voice-{sleeper.pid}-abc", 3600)
    foreign = _stale_dir(tmp_path, f"other-{dead}-abc", 3600)
    no_pid = _stale_dir(tmp_path, "agent-voice-old", 3600)
    target = tmp_path / "precious"
    target.mkdir()
    (tmp_path / f"agent-voice-{dead}-link").symlink_to(target)
    (tmp_path / f"agent-voice-{dead}-file").write_text("x")
    player.speak(["one"], fake_synth, play=Recorder(), workdir=tmp_path, run_dir=run_dir,
                 handle_signals=False)
    assert not stale.exists()
    assert young.exists() and alive.exists() and foreign.exists() and no_pid.exists()
    assert target.exists() and (tmp_path / f"agent-voice-{dead}-link").is_symlink()
    assert (tmp_path / f"agent-voice-{dead}-file").exists()


def test_a_failing_sweep_never_breaks_speaking(run_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(player.os, "scandir", lambda p: (_ for _ in ()).throw(OSError("denied")))
    rec = Recorder()
    player.speak(["one"], fake_synth, play=rec, workdir=tmp_path, run_dir=run_dir, handle_signals=False)
    assert rec.played == [b"WAV:one"]
