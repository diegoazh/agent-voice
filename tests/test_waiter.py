import os
import signal
import subprocess
import sys
import threading

import pytest

from agent_voice import player, waiter

WAIT = 5
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture
def run_dir(tmp_path):
    return tmp_path / "run"


@pytest.fixture
def spawn():
    procs = []

    def make():
        proc = subprocess.Popen(SLEEPER)
        threading.Thread(target=proc.wait, daemon=True).start()
        procs.append(proc)
        return proc

    yield make
    for proc in procs:
        if proc.poll() is None:
            proc.kill()


def test_register_records_only_pid_and_start_time_under_the_run_dir(run_dir, spawn):
    proc = spawn()
    waiter.register("w1Q:p1", proc.pid, run_dir=run_dir)
    files = list(run_dir.iterdir())
    pid_files = [f for f in files if f.suffix == ".pid"]
    assert len(pid_files) == 1 and pid_files[0].name.startswith("waiter-")
    pid, start = pid_files[0].read_text().splitlines()
    assert pid == str(proc.pid) and start == player.process_start_time(proc.pid)
    assert "w1Q" not in "".join(f.name for f in files)  # opaque file names


def test_a_newer_waiter_for_the_same_pane_terminates_the_previous_one(run_dir, spawn):
    old, new = spawn(), spawn()
    waiter.register("w1:p1", old.pid, run_dir=run_dir)
    waiter.register("w1:p1", new.pid, run_dir=run_dir)
    assert old.wait(WAIT) == -signal.SIGTERM
    assert new.poll() is None


def test_waiters_of_other_panes_are_left_alone(run_dir, spawn):
    a, b = spawn(), spawn()
    waiter.register("w1:p1", a.pid, run_dir=run_dir)
    waiter.register("w1:p2", b.pid, run_dir=run_dir)
    assert a.poll() is None and b.poll() is None


def test_cancel_terminates_the_waiter_of_that_pane_only(run_dir, spawn):
    a, b = spawn(), spawn()
    waiter.register("w1:p1", a.pid, run_dir=run_dir)
    waiter.register("w1:p2", b.pid, run_dir=run_dir)
    assert waiter.cancel("w1:p1", run_dir=run_dir) is True
    assert a.wait(WAIT) == -signal.SIGTERM
    assert b.poll() is None
    assert waiter.cancel("w1:p1", run_dir=run_dir) is False


def test_cancel_all_terminates_every_waiter_but_not_the_speaker(run_dir, spawn):
    a, b, speaker = spawn(), spawn(), spawn()
    waiter.register("w1:p1", a.pid, run_dir=run_dir)
    waiter.register("w1:p2", b.pid, run_dir=run_dir)
    run_dir.mkdir(exist_ok=True)
    (run_dir / player.PID_FILE).write_text(f"{speaker.pid}\n{player.process_start_time(speaker.pid)}")
    assert waiter.cancel_all(run_dir=run_dir) == 2
    assert a.wait(WAIT) == -signal.SIGTERM and b.wait(WAIT) == -signal.SIGTERM
    assert speaker.poll() is None


def test_cancel_all_without_a_run_dir_is_a_no_op(run_dir):
    assert waiter.cancel_all(run_dir=run_dir) == 0


def test_a_reused_pid_of_an_unrelated_process_is_never_signalled(run_dir, spawn):
    other = spawn()
    waiter.register("w1:p1", other.pid, run_dir=run_dir, start_time=lambda pid: "then")
    waiter.cancel("w1:p1", run_dir=run_dir, start_time=lambda pid: "now")
    waiter.cancel_all(run_dir=run_dir, start_time=lambda pid: "now")
    assert other.poll() is None


def test_release_removes_only_the_own_record(run_dir, spawn):
    proc = spawn()
    waiter.register("w1:p1", proc.pid, run_dir=run_dir)
    waiter.release("w1:p1", run_dir=run_dir)  # not our pid: kept
    assert any(f.suffix == ".pid" for f in run_dir.iterdir())
    waiter.register("w1:p1", os.getpid(), run_dir=run_dir)
    waiter.release("w1:p1", run_dir=run_dir)
    assert not any(f.suffix == ".pid" for f in run_dir.iterdir())


from agent_voice import focus  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Script:
    """A detector replaying `states`; an exception instance is raised."""

    pane_id = "w1:p1"

    def __init__(self, *states):
        self.states = list(states)
        self.calls = 0

    def is_focused(self):
        self.calls += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return state


def wait(detector, clock, **kw):
    kw.setdefault("enabled", lambda: True)
    return waiter.wait_for_focus(detector, sleep=clock.sleep, clock=clock.monotonic, **kw)


def test_returns_immediately_when_already_focused():
    clock = Clock()
    assert wait(Script(True), clock) is True
    assert clock.sleeps == []


def test_polls_every_half_second_until_focused():
    clock, det = Clock(), Script(False, False, True)
    assert wait(det, clock) is True
    assert clock.sleeps == [0.5, 0.5] and det.calls == 3


def test_unknown_focus_keeps_waiting_instead_of_speaking():
    clock, det = Clock(), Script(None, None, True)
    assert wait(det, clock) is True
    assert det.calls == 3


def test_gives_up_when_the_pane_is_gone():
    clock = Clock()
    assert wait(Script(False, focus.PaneGoneError("w1:p1")), clock) is False
    assert clock.sleeps == [0.5]


def test_gives_up_when_speaking_gets_disabled():
    clock, det = Clock(), Script(False)
    flags = iter([True, True, False])
    assert wait(det, clock, enabled=lambda: next(flags)) is False
    assert det.calls == 2


def test_max_wait_caps_the_wait_and_zero_means_no_limit():
    clock = Clock()
    assert wait(Script(False), clock, max_wait=2) is False
    assert clock.now == pytest.approx(2.0)
    clock = Clock()
    det = Script(*([False] * 400 + [True]))
    assert wait(det, clock, max_wait=0) is True
    assert clock.now == pytest.approx(200.0)


def test_unexpected_detector_errors_count_as_unknown():
    clock, det = Clock(), Script(RuntimeError("boom"), True)
    assert wait(det, clock) is True
