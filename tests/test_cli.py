from agent_voice.cli import main


def test_version_prints_and_returns_zero(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "agent-voice 0.1.0\n"


def test_no_arguments_prints_nothing_and_returns_zero(capsys):
    assert main([]) == 0
    assert capsys.readouterr().out == ""
