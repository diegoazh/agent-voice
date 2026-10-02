import json
from pathlib import Path

from agent_voice import config


def test_path_prefers_agent_voice_home():
    env = {"AGENT_VOICE_HOME": "/h", "XDG_CONFIG_HOME": "/x"}
    assert config.config_path(env) == Path("/h/config.json")


def test_path_falls_back_to_xdg_config_home():
    assert config.config_path({"XDG_CONFIG_HOME": "/x"}) == Path("/x/agent-voice/config.json")


def test_path_defaults_to_dot_config_in_home(monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: Path("/home/u"))
    assert config.config_path({}) == Path("/home/u/.config/agent-voice/config.json")


def test_load_without_file_returns_defaults(tmp_path):
    assert config.load({"AGENT_VOICE_HOME": str(tmp_path)}) == {
        "enabled": False,
        "voice": "em_alex",
        "lang": "es-419",
        "model": "fp32",
        "speed": 1.0,
        "pending_max_wait_s": 0,
    }


def test_save_then_load_roundtrip(tmp_path):
    env = {"AGENT_VOICE_HOME": str(tmp_path)}
    config.save({"voice": "ef_dora", "enabled": True}, env)
    loaded = config.load(env)
    assert loaded["voice"] == "ef_dora"
    assert loaded["enabled"] is True
    assert loaded["model"] == "fp32"


def test_save_preserves_unknown_keys(tmp_path):
    env = {"AGENT_VOICE_HOME": str(tmp_path)}
    (tmp_path / "config.json").write_text(json.dumps({"future": [1, 2], "voice": "em_santa"}))
    config.save({"enabled": True}, env)
    data = json.loads((tmp_path / "config.json").read_text())
    assert data == {"future": [1, 2], "voice": "em_santa", "enabled": True}
    assert config.load(env)["future"] == [1, 2]


def test_failed_write_keeps_old_file_and_leaves_no_temp(tmp_path, monkeypatch):
    env = {"AGENT_VOICE_HOME": str(tmp_path)}
    config.save({"voice": "em_santa"}, env)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", boom)
    try:
        config.save({"voice": "ef_dora"}, env)
    except OSError:
        pass
    monkeypatch.undo()
    assert config.load(env)["voice"] == "em_santa"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_corrupt_file_gives_defaults_and_one_warning(tmp_path, capsys):
    env = {"AGENT_VOICE_HOME": str(tmp_path)}
    (tmp_path / "config.json").write_text("{not json")
    assert config.load(env) == config.DEFAULTS
    err = capsys.readouterr().err
    assert err.count("agent-voice:") == 1
    assert "config" in err


def test_non_object_json_is_treated_as_corrupt(tmp_path, capsys):
    env = {"AGENT_VOICE_HOME": str(tmp_path)}
    (tmp_path / "config.json").write_text("[1, 2]")
    assert config.load(env) == config.DEFAULTS
    assert "agent-voice:" in capsys.readouterr().err


def test_save_over_corrupt_file_recovers(tmp_path):
    env = {"AGENT_VOICE_HOME": str(tmp_path)}
    (tmp_path / "config.json").write_text("{not json")
    config.save({"enabled": True}, env)
    assert config.load(env)["enabled"] is True


import pytest


def _write(tmp_path, data):
    (tmp_path / "config.json").write_text(json.dumps(data))
    return {"AGENT_VOICE_HOME": str(tmp_path)}


@pytest.mark.parametrize(
    "bad",
    [
        {"voice": 3},
        {"voice": "nope"},
        {"model": ["fp32"]},
        {"model": "fp64"},
        {"speed": "fast"},
        {"speed": True},
        {"speed": 0},
        {"speed": -1.5},
        {"speed": float("nan")},
        {"speed": 100},
        {"lang": 5},
        {"lang": ""},
        {"pending_max_wait_s": -1},
        {"pending_max_wait_s": "10"},
        {"pending_max_wait_s": True},
        {"pending_max_wait_s": float("inf")},
    ],
)
def test_invalid_value_falls_back_to_default_with_one_warning(tmp_path, capsys, bad):
    env = _write(tmp_path, {"enabled": True, **bad})
    loaded = config.load(env)
    (key,) = bad
    assert loaded[key] == config.DEFAULTS[key]
    assert loaded["enabled"] is True
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and key in err


def test_valid_values_load_unchanged_without_warning(tmp_path, capsys):
    env = _write(tmp_path, {"voice": "ef_dora", "model": "int8", "speed": 1, "lang": "es", "x": [1]})
    assert config.load(env) == {
        **config.DEFAULTS, "voice": "ef_dora", "model": "int8", "speed": 1, "lang": "es", "x": [1],
    }
    assert capsys.readouterr().err == ""


def test_several_invalid_values_give_a_single_warning_naming_all(tmp_path, capsys):
    env = _write(tmp_path, {"voice": 1, "speed": "x"})
    loaded = config.load(env)
    assert loaded["voice"] == config.DEFAULTS["voice"] and loaded["speed"] == 1.0
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "voice" in err and "speed" in err


@pytest.mark.parametrize("bad", ["/a", [1], ["a", 2], [""], {"a": 1}])
def test_invalid_claude_config_dirs_are_dropped_with_one_warning(tmp_path, capsys, bad):
    env = _write(tmp_path, {"claude_config_dirs": bad})
    assert "claude_config_dirs" not in config.load(env)
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "claude_config_dirs" in err


def test_valid_claude_config_dirs_are_kept(tmp_path, capsys):
    env = _write(tmp_path, {"claude_config_dirs": ["/a", "/b"]})
    assert config.load(env)["claude_config_dirs"] == ["/a", "/b"]
    assert capsys.readouterr().err == ""


def test_pending_max_wait_accepts_non_negative_numbers(tmp_path):
    env = _write(tmp_path, {"pending_max_wait_s": 90.5})
    assert config.load(env)["pending_max_wait_s"] == 90.5
