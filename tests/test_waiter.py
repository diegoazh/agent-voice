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
    ticks = iter(range(5000))  # a runaway wait ends as "disabled" instead of hanging the suite
    kw.setdefault("enabled", lambda: next(ticks, None) is not None)
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


class BackgroundGhostty(Script):
    """Ghostty never frontmost: is_focused() answers False without asking herdr; only
    check_pane() reaches herdr, and raises PaneGoneError once the pane closes at `closes_at`."""

    def __init__(self, clock, closes_at):
        super().__init__(False)
        self.clock, self.closes_at = clock, closes_at
        self.pane_checks = []

    def check_pane(self):
        self.pane_checks.append(self.clock.now)
        if self.clock.now >= self.closes_at:
            raise focus.PaneGoneError(self.pane_id)


def test_notices_a_closed_pane_within_one_check_even_with_ghostty_in_background():
    clock = Clock()
    det = BackgroundGhostty(clock, closes_at=25)
    assert wait(det, clock) is False  # returned False, i.e. never spoke
    assert 25 <= clock.now <= 25 + waiter.PANE_CHECK_INTERVAL
    assert det.pane_checks[0] == waiter.PANE_CHECK_INTERVAL
    assert all(b - a == waiter.PANE_CHECK_INTERVAL for a, b in zip(det.pane_checks, det.pane_checks[1:]))


def test_pane_check_interval_is_injectable_and_checks_stay_off_the_focus_ticks():
    clock = Clock()
    det = BackgroundGhostty(clock, closes_at=3)
    assert wait(det, clock, pane_check_interval=2.0) is False
    assert det.pane_checks == [2.0, 4.0]
    assert det.calls == 8  # focus ticks keep their own 0.5 s cadence


def test_an_open_pane_never_ends_the_wait_and_check_errors_count_as_unknown():
    clock = Clock()
    det = BackgroundGhostty(clock, closes_at=10**9)
    det.check_pane = lambda: (_ for _ in ()).throw(RuntimeError("herdr hiccup"))
    flags = iter([True] * 60 + [False])
    assert wait(det, clock, enabled=lambda: next(flags)) is False
    assert clock.now == pytest.approx(30.0)


def test_unknown_focus_keeps_waiting_until_disabled_and_never_speaks():
    clock, det = Clock(), Script(None)
    flags = iter([True] * 300 + [False])
    assert wait(det, clock, enabled=lambda: next(flags)) is False
    assert det.calls == 300 and clock.now == pytest.approx(150.0)


def test_detectors_without_a_pane_check_still_work():
    clock, det = Clock(), Script(False, False, True)
    assert not hasattr(det, "check_pane")
    assert wait(det, clock, pane_check_interval=0.5) is True


# --- T15 G5: a waiter does not outlive a herdr that keeps failing -----------


def test_gives_up_after_continuous_unknown_focus_without_speaking():
    clock, det = Clock(), Script(None)
    assert wait(det, clock, unknown_give_up=30) is False
    assert clock.now == pytest.approx(30.0)


def test_continuous_detector_errors_also_give_up():
    clock = Clock()
    assert wait(Script(RuntimeError("boom")), clock, unknown_give_up=30) is False
    assert clock.now == pytest.approx(30.0)


def test_a_known_state_resets_the_unknown_streak():
    clock = Clock()
    # 20s unknown, one known "not focused", then 20s unknown again, then focused:
    # never 30 s in a row, so it keeps waiting and finally speaks.
    det = Script(*([None] * 40 + [False] + [None] * 40 + [True]))
    assert wait(det, clock, unknown_give_up=30) is True


def test_default_unknown_give_up_is_ten_minutes_and_known_state_waits_forever():
    assert waiter.UNKNOWN_GIVE_UP == 600.0
    clock = Clock()
    det = Script(*([False] * 1500 + [True]))  # 750 s of known "not focused" is fine
    assert wait(det, clock) is True
