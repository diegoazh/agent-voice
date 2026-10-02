import pytest


@pytest.fixture(autouse=True)
def _isolated_agent_voice_home(request, tmp_path_factory, monkeypatch):
    """No test may ever read or write the real ~/.config/agent-voice.

    Integration tests only read the real downloaded model files (which live
    under the XDG data dir unless AGENT_VOICE_HOME is set), so they keep the
    ambient environment.
    """
    if request.node.get_closest_marker("integration"):
        return
    # Tests must behave the same inside and outside herdr/Ghostty: no ambient focus gating.
    for name in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_BIN_PATH", "TERM_PROGRAM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_VOICE_HOME", str(tmp_path_factory.mktemp("avhome")))
    # ...and never talk to a real herdr: it is "not installed" unless a test says otherwise.
    def _no_herdr(argv, timeout=2.0):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr("agent_voice.herdr._run", _no_herdr)
