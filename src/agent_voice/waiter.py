"""Pending replies: one in-memory waiter process per pane, tracked by pid + start time.

The run dir only ever holds a pid and a process start time per pane (the pane id is
hashed into the file name). The reply text lives solely in the waiting process.
"""

import hashlib
import time
from pathlib import Path

from agent_voice import focus, player

POLL_INTERVAL = 0.5


def _names(pane_id):
    key = hashlib.sha256(pane_id.encode()).hexdigest()[:16]
    return f"waiter-{key}.pid", f"waiter-{key}.lock"


def _resolve(run_dir, start_time):
    run_dir = player.runtime_dir() if run_dir is None else Path(run_dir)
    return run_dir, player.process_start_time if start_time is None else start_time


def register(pane_id, pid, *, run_dir=None, start_time=None):
    """Record `pid` as the pane's waiter; a previous waiter of that pane is terminated."""
    run_dir, start_time = _resolve(run_dir, start_time)
    pid_file, lock_file = _names(pane_id)
    player._claim(run_dir, start_time, pid=pid, pid_file=pid_file, lock_file=lock_file)


def cancel(pane_id, *, run_dir=None, start_time=None):
    """Terminate the pane's waiter, if any. Returns True if one was running."""
    run_dir, start_time = _resolve(run_dir, start_time)
    return player.stop(run_dir, start_time=start_time, pid_file=_names(pane_id)[0])


def cancel_all(*, run_dir=None, start_time=None):
    """Terminate every pane's waiter (used by `agent-voice off`); returns how many."""
    run_dir, start_time = _resolve(run_dir, start_time)
    if not run_dir.is_dir():
        return 0
    stopped = 0
    for record in sorted(run_dir.glob("waiter-*.pid")):
        stopped += bool(player.stop(run_dir, start_time=start_time, pid_file=record.name))
    return stopped


def release(pane_id, *, run_dir=None):
    """Drop this process's own record (called by the waiter when it stops waiting)."""
    run_dir, _ = _resolve(run_dir, None)
    player._release(run_dir, _names(pane_id)[0])


def wait_for_focus(detector, *, enabled, sleep=None, clock=None, interval=POLL_INTERVAL,
                   max_wait=0):
    """Block until the session has focus. True = speak now; False = drop the reply.

    Polls: herdr has a `pane.focused` event, but nothing announces Ghostty gaining the
    OS-level focus, so an event wait would still need a poll. Each tick costs one
    `lsappinfo` call while Ghostty is in the background (two when it is in front).
    Unknown focus keeps waiting. Gives up when speaking is disabled, the pane is gone,
    or `max_wait` seconds pass (0 = no limit).
    """
    sleep = time.sleep if sleep is None else sleep
    clock = time.monotonic if clock is None else clock
    deadline = clock() + max_wait if max_wait > 0 else None
    while enabled():
        try:
            state = detector.is_focused()
        except focus.PaneGoneError:
            return False
        except Exception:
            state = None
        if state is True:
            return True
        if deadline is not None and clock() >= deadline:
            return False
        sleep(interval)
    return False
