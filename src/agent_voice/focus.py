"""Focus detection: is the terminal session that produced a reply in front of the user?"""

import json
import re
import subprocess
import time
from typing import Protocol

from agent_voice import herdr

GHOSTTY_BUNDLE = "com.mitchellh.ghostty"
_BUNDLE_RE = re.compile(r'bundleid"?\s*=\s*"([^"]*)"', re.IGNORECASE)


def _run(argv, timeout=2.0):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def bounded_run(budget, *, run=None, clock=time.monotonic):
    """A runner whose calls share one total time budget (seconds), starting now.

    Each call's timeout is capped by what is left; once the budget is spent a call raises
    subprocess.TimeoutExpired without starting a process (callers read that as "unknown").
    """
    run = _run if run is None else run
    deadline = clock() + budget

    def bounded(argv, timeout=2.0):
        remaining = deadline - clock()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, budget)
        return run(argv, timeout=min(timeout, remaining))

    return bounded


class PaneGoneError(Exception):
    """The session's pane no longer exists, so nothing can ever be pending for it."""


class FocusDetector(Protocol):
    """Is the session that produced a reply in front of the user?

    `is_focused()` returns True/False, or None when it cannot be determined (callers
    treat None as "speak": a broken detector must never silence the agent). It may raise
    PaneGoneError when the session itself is gone. Implementations must be cheap and
    read-only; terminal/multiplexer support (tmux, WezTerm, iTerm2, ...) plugs in here.
    """

    pane_id: str

    def check_pane(self) -> None:
        """Raise PaneGoneError if the session's pane no longer exists (optional; cheap)."""

    def is_focused(self) -> "bool | None": ...


class HerdrGhosttyDetector:
    """Focused = Ghostty is the frontmost app AND the herdr pane is the focused pane.

    herdr's `pane.focused` is global to the herdr session (workspace + tab + pane): in a
    live snapshot exactly one pane of all workspaces had it. It knows nothing about the
    OS-level focus, hence the separate Ghostty check.
    """

    def __init__(self, pane_id, *, run=None, herdr_bin="herdr"):
        self.pane_id = pane_id
        self._herdr = herdr_bin
        self._run = _run if run is None else run

    def ghostty_frontmost(self):
        """True/False from the frontmost app's bundle id; None when it cannot be read."""
        try:
            front = self._run(["lsappinfo", "front"])
            asn = front.stdout.strip()
            if front.returncode != 0 or not asn:
                return None
            info = self._run(["lsappinfo", "info", "-only", "bundleid", asn])
        except (OSError, subprocess.SubprocessError):
            return None
        match = _BUNDLE_RE.search(info.stdout) if info.returncode == 0 else None
        return None if match is None else match.group(1) == GHOSTTY_BUNDLE

    def herdr_pane_focused(self):
        """True/False from `herdr pane get`; None if unknown; PaneGoneError if the pane is gone."""
        try:
            result = self._run([self._herdr, "pane", "get", self.pane_id])
            data = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        if not isinstance(data, dict):
            return None
        error = data.get("error")
        if isinstance(error, dict) and error.get("code") == "pane_not_found":
            raise PaneGoneError(self.pane_id)
        try:
            focused = data["result"]["pane"]["focused"]
        except (KeyError, TypeError):
            return None
        return focused if isinstance(focused, bool) else None

    def check_pane(self):
        """Ask herdr only whether the pane exists, whatever the OS-level focus."""
        self.herdr_pane_focused()

    def is_focused(self):
        front = self.ghostty_frontmost()
        if front is False:
            return False  # cheap exit: one process instead of two
        pane = self.herdr_pane_focused()
        if pane is False:
            return False
        return True if front and pane else None


def detect_from_env(env, run=None):
    """The detector for this session, or None (no detector: speak immediately, as always).

    Currently: Ghostty + herdr, recognised by TERM_PROGRAM, HERDR_ENV and HERDR_PANE_ID.
    """
    if env.get("TERM_PROGRAM") != "ghostty" or not env.get("HERDR_ENV"):
        return None
    pane_id = env.get("HERDR_PANE_ID")
    if not pane_id or not herdr.valid_pane_id(pane_id):
        return None
    return HerdrGhosttyDetector(pane_id, run=run, herdr_bin=env.get("HERDR_BIN_PATH") or "herdr")
