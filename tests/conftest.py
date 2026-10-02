import os
from pathlib import Path

import pytest

from agent_voice import models

# Captured at import time, before any test or fixture can patch the environment.
_REAL_HOME = Path(os.path.expanduser("~"))
_REAL_MODELS_DIR = models.models_dir()
_GUARDED_SETTINGS = [
    _REAL_HOME / ".claude" / "settings.json",
    _REAL_HOME / ".claude-work" / "settings.json",
]
if os.environ.get("CLAUDE_CONFIG_DIR"):
    _GUARDED_SETTINGS.append(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json")


def _stamp(path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


@pytest.fixture(scope="session", autouse=True)
def _guard_real_settings():
    """Fail the session if any test touched a real Claude settings.json (read-only check)."""
    before = {p: _stamp(p) for p in _GUARDED_SETTINGS}
    yield
    changed = [str(p) for p, stamp in before.items() if _stamp(p) != stamp]
    assert not changed, f"tests modified real settings files: {changed}"


@pytest.fixture(autouse=True)
def _isolated_environment(request, tmp_path_factory, monkeypatch):
    """No test may ever read or write the real home, ~/.claude* or ~/.config/agent-voice.

    HOME (so Path.home()/expanduser), CLAUDE_CONFIG_DIR and the XDG dirs all point to tmp
    dirs. Integration tests only read the real downloaded model files: they get a tmp
    AGENT_VOICE_HOME whose `models` entry is a symlink to the real models dir.
    """
    root = tmp_path_factory.mktemp("sandbox")
    monkeypatch.setenv("HOME", str(root / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root / "claude"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "xdg-state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(root / "xdg-data"))
    avhome = root / "avhome"
    avhome.mkdir()
    monkeypatch.setenv("AGENT_VOICE_HOME", str(avhome))
    if request.node.get_closest_marker("integration"):
        if _REAL_MODELS_DIR.is_dir():
            (avhome / "models").symlink_to(_REAL_MODELS_DIR)
        return
    # Tests must behave the same inside and outside herdr/Ghostty: no ambient focus gating.
    for name in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_BIN_PATH", "TERM_PROGRAM"):
        monkeypatch.delenv(name, raising=False)

    # ...and never talk to a real herdr: it is "not installed" unless a test says otherwise.
    def _no_herdr(argv, timeout=2.0):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr("agent_voice.herdr._run", _no_herdr)
