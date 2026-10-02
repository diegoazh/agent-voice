import pytest


@pytest.fixture(autouse=True)
def _isolated_agent_voice_home(tmp_path_factory, monkeypatch):
    """No test may ever read or write the real ~/.config/agent-voice."""
    monkeypatch.setenv("AGENT_VOICE_HOME", str(tmp_path_factory.mktemp("avhome")))
