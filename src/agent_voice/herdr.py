"""Read-only herdr lookups: which pane (and so which agent session) is the user looking at.

Only `herdr pane get` and `herdr pane list` are ever run. Any failure means "unknown"
(None): callers fall back to their previous behavior.
"""

import json
import re
import subprocess

# skhd runs commands with a minimal PATH that usually lacks Homebrew's bin directory.
HOMEBREW_BIN = "/opt/homebrew/bin/herdr"
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _run(argv, timeout=2.0):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _candidates(env):
    return list(dict.fromkeys([env.get("HERDR_BIN_PATH") or "herdr", HOMEBREW_BIN]))


def current_pane(env, run=None):
    """The pane dict herdr reports, or None.

    HERDR_PANE_ID in `env` (typed inside a pane) -> that pane; otherwise the pane that
    has `focused: true` (herdr's focus is global to its session).
    """
    run = _run if run is None else run
    pane_id = env.get("HERDR_PANE_ID")
    args = ["pane", "get", pane_id] if pane_id else ["pane", "list"]
    for binary in _candidates(env):
        try:
            data = json.loads(run([binary, *args]).stdout)
        except FileNotFoundError:
            continue  # not at this path: try the next candidate
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        return _pick(data, by_id=bool(pane_id))
    return None


def _pick(data, by_id):
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return None
    if by_id:
        pane = result.get("pane")
        return pane if isinstance(pane, dict) else None
    panes = result.get("panes")
    if not isinstance(panes, list):
        return None
    return next((p for p in panes if isinstance(p, dict) and p.get("focused") is True), None)


def claude_session_id(pane):
    """The Claude session id of a pane running Claude Code, else None."""
    session = pane.get("agent_session")
    if pane.get("agent") != "claude" or not isinstance(session, dict):
        return None
    value = session.get("value")
    if session.get("kind") != "id" or not isinstance(value, str) or not _SESSION_ID.match(value):
        return None
    return value
