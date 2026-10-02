import os
import json

import pytest

from agent_voice.cli import main


@pytest.fixture(autouse=True)
def fixed_which(monkeypatch):
    monkeypatch.setattr("agent_voice.adapters.claude.shutil.which", lambda name: "/opt/bin/agent-voice")


def read(d):
    return json.loads((d / "settings.json").read_text())


def entry(cmd="/opt/bin/agent-voice hook claude"):
    return {"hooks": [{"type": "command", "command": cmd}]}


def test_install_into_empty_dir_creates_settings(tmp_path, capsys):
    d = tmp_path / "cfg"
    assert main(["install", "claude", "--config-dir", str(d)]) == 0
    assert read(d) == {"hooks": {"Stop": [entry()]}}
    assert (d / "settings.json").read_text().startswith('{\n  "hooks"')
    assert str(d / "settings.json") in capsys.readouterr().out


def test_install_preserves_other_keys_and_hooks(tmp_path):
    other = {"matcher": "", "hooks": [{"type": "command", "command": "gentle-ai hook stop"}]}
    original = {
        "env": {"A": "1"},
        "hooks": {"Stop": [other], "SubagentStop": [other], "PreToolUse": [{"matcher": "Agent", "hooks": []}]},
        "outputStyle": "x",
    }
    (tmp_path / "settings.json").write_text(json.dumps(original))
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 0
    got = read(tmp_path)
    assert got["env"] == {"A": "1"} and got["outputStyle"] == "x"
    assert got["hooks"]["Stop"] == [other, entry()]
    assert got["hooks"]["SubagentStop"] == [other]
    assert got["hooks"]["PreToolUse"] == original["hooks"]["PreToolUse"]


def test_install_is_idempotent(tmp_path, capsys):
    main(["install", "claude", "--config-dir", str(tmp_path)])
    first = (tmp_path / "settings.json").read_text()
    capsys.readouterr()
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 0
    assert (tmp_path / "settings.json").read_text() == first
    assert read(tmp_path)["hooks"]["Stop"] == [entry()]
    assert "unchanged" in capsys.readouterr().out


def test_install_updates_our_command_path_in_place(tmp_path, capsys):
    stale = {"hooks": {"Stop": [entry("/old/place/agent-voice hook claude")]}}
    (tmp_path / "settings.json").write_text(json.dumps(stale))
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path)["hooks"]["Stop"] == [entry()]
    assert "updated" in capsys.readouterr().out


@pytest.mark.parametrize("content", ["{not json", "[]", ""])
def test_install_refuses_invalid_settings_and_changes_nothing(tmp_path, capsys, content):
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    bad.mkdir()
    (bad / "settings.json").write_text(content)
    code = main(["install", "claude", "--config-dir", str(good), "--config-dir", str(bad)])
    assert code == 1
    assert (bad / "settings.json").read_text() == content
    assert not (good / "settings.json").exists()
    assert not (bad / "settings.json.agent-voice.bak").exists()
    assert str(bad / "settings.json") in capsys.readouterr().err


def test_backup_is_created_once_with_original_content(tmp_path):
    original = '{"env": {"A": "1"}}'
    (tmp_path / "settings.json").write_text(original)
    bak = tmp_path / "settings.json.agent-voice.bak"
    main(["install", "claude", "--config-dir", str(tmp_path)])
    assert bak.read_text() == original
    main(["uninstall", "claude", "--config-dir", str(tmp_path)])
    main(["install", "claude", "--config-dir", str(tmp_path)])
    assert bak.read_text() == original


def test_no_backup_when_settings_did_not_exist_and_no_tmp_left(tmp_path):
    main(["install", "claude", "--config-dir", str(tmp_path)])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


def test_uninstall_removes_only_our_entry_keeping_others(tmp_path, capsys):
    other = {"matcher": "", "hooks": [{"type": "command", "command": "gentle-ai hook stop"}]}
    mixed = {"hooks": {"type": "x"}}
    mixed = {"hooks": [{"type": "command", "command": "/a/agent-voice hook claude"},
                       {"type": "command", "command": "keep-me"}]}
    (tmp_path / "settings.json").write_text(
        json.dumps({"env": {"A": "1"}, "hooks": {"Stop": [other, entry(), mixed], "SubagentStop": [other]}})
    )
    assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 0
    got = read(tmp_path)
    assert got["env"] == {"A": "1"}
    assert got["hooks"]["Stop"] == [
        other,
        {"hooks": [{"type": "command", "command": "keep-me"}]},
    ]
    assert got["hooks"]["SubagentStop"] == [other]
    assert "updated" in capsys.readouterr().out


def test_uninstall_removes_empty_containers_we_created(tmp_path):
    main(["install", "claude", "--config-dir", str(tmp_path)])
    assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path) == {}


def test_uninstall_without_settings_file_is_a_noop(tmp_path, capsys):
    assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 0
    assert not (tmp_path / "settings.json").exists()
    assert "unchanged" in capsys.readouterr().out


def test_multiple_config_dirs_are_all_updated_and_listed(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["install", "claude", "--config-dir", str(a), "--config-dir", str(b)]) == 0
    assert read(a) == read(b) == {"hooks": {"Stop": [entry()]}}
    out = capsys.readouterr().out
    assert str(a / "settings.json") in out and str(b / "settings.json") in out
    assert main(["uninstall", "claude", "--config-dir", str(a), "--config-dir", str(b)]) == 0
    assert read(a) == read(b) == {}


def test_default_dir_is_claude_config_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "envdir"))
    assert main(["install", "claude"]) == 0
    assert read(tmp_path / "envdir") == {"hooks": {"Stop": [entry()]}}
    assert main(["uninstall", "claude"]) == 0
    assert read(tmp_path / "envdir") == {}


def test_default_dir_falls_back_to_home_dot_claude(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("agent_voice.adapters.claude.Path.home", lambda: tmp_path)
    assert main(["install", "claude"]) == 0
    assert read(tmp_path / ".claude") == {"hooks": {"Stop": [entry()]}}


def test_command_falls_back_to_bare_name_when_not_on_path(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_voice.adapters.claude.shutil.which", lambda name: None)
    main(["install", "claude", "--config-dir", str(tmp_path)])
    assert read(tmp_path)["hooks"]["Stop"] == [entry("agent-voice hook claude")]


def test_uninstall_refuses_invalid_json(tmp_path, capsys):
    (tmp_path / "settings.json").write_text("{oops")
    assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 1
    assert (tmp_path / "settings.json").read_text() == "{oops"
    assert "nothing changed" in capsys.readouterr().err


def test_uninstall_ignores_foreign_shapes(tmp_path):
    odd = {"hooks": {"Stop": ["junk", {"hooks": "nope"}, {"hooks": [{"type": "command"}]}], "Other": 1}}
    (tmp_path / "settings.json").write_text(json.dumps(odd))
    assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path) == odd


def test_install_tolerates_foreign_shapes_and_refuses_non_list_stop(tmp_path, capsys):
    (tmp_path / "settings.json").write_text(json.dumps({"hooks": {"Stop": ["junk", {"hooks": "nope"}]}}))
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path)["hooks"]["Stop"] == ["junk", {"hooks": "nope"}, entry()]
    other = tmp_path / "o"
    other.mkdir()
    (other / "settings.json").write_text(json.dumps({"hooks": {"Stop": "x"}}))
    assert main(["install", "claude", "--config-dir", str(other)]) == 1
    assert "nothing changed" in capsys.readouterr().err
    assert read(other) == {"hooks": {"Stop": "x"}}


def test_uninstall_keeps_hooks_container_when_other_events_exist(tmp_path):
    other = {"hooks": [{"type": "command", "command": "x"}]}
    (tmp_path / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [entry()], "SubagentStop": [other]}})
    )
    main(["uninstall", "claude", "--config-dir", str(tmp_path)])
    assert read(tmp_path) == {"hooks": {"SubagentStop": [other]}}


# --- T11 hardening ---------------------------------------------------------


def test_hook_command_path_is_shell_quoted(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_voice.adapters.claude.shutil.which", lambda n: "/opt/my bin/agent-voice")
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path)["hooks"]["Stop"] == [entry("'/opt/my bin/agent-voice' hook claude")]
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 0
    assert len(read(tmp_path)["hooks"]["Stop"]) == 1
    assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path) == {}


def test_old_unquoted_entry_is_upgraded_in_place_not_duplicated(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_voice.adapters.claude.shutil.which", lambda n: "/opt/my bin/agent-voice")
    (tmp_path / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [entry("/opt/my bin/agent-voice hook claude")]}})
    )
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path)["hooks"]["Stop"] == [entry("'/opt/my bin/agent-voice' hook claude")]


def test_uninstall_recognises_old_unquoted_entry(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [entry("/opt/my bin/agent-voice hook claude")]}})
    )
    assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path) == {}


def test_symlinked_settings_is_written_through_and_link_preserved(tmp_path):
    real_dir = tmp_path / "dotfiles"
    real_dir.mkdir()
    target = real_dir / "settings.json"
    target.write_text(json.dumps({"env": {"A": "1"}}))
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.json").symlink_to(target)
    assert main(["install", "claude", "--config-dir", str(cfg)]) == 0
    assert (cfg / "settings.json").is_symlink()
    assert json.loads(target.read_text()) == {"env": {"A": "1"}, "hooks": {"Stop": [entry()]}}
    assert sorted(p.name for p in real_dir.iterdir()) == ["settings.json"]
    assert main(["uninstall", "claude", "--config-dir", str(cfg)]) == 0
    assert (cfg / "settings.json").is_symlink()
    assert json.loads(target.read_text()) == {"env": {"A": "1"}}


def test_partial_write_reports_changed_and_failed_dirs_and_leaves_no_temp(tmp_path, monkeypatch, capsys):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    real_replace = os.replace

    def flaky(src, dst):
        if str(dst).startswith(str(b)):
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr("agent_voice.adapters.claude.os.replace", flaky)
    code = main(["install", "claude", "--config-dir", str(a), "--config-dir", str(b), "--config-dir", str(c)])
    assert code == 1
    err = capsys.readouterr().err
    assert str(a / "settings.json") in err and str(c / "settings.json") in err
    assert "changed" in err and "failed" in err
    assert f"{b / 'settings.json'}" in err and "No space left" in err
    assert (a / "settings.json").exists() and (c / "settings.json").exists()
    assert not (b / "settings.json").exists()
    for d in (a, b, c):
        if d.exists():
            assert [p.name for p in d.iterdir() if p.name != "settings.json"] == []


def test_uninstall_keeps_foreign_empty_stop_list_and_hooks_object(tmp_path):
    for body in ({"hooks": {"Stop": []}}, {"hooks": {}}, {"hooks": {"Stop": [{"hooks": []}]}}):
        (tmp_path / "settings.json").write_text(json.dumps(body))
        assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 0
        assert read(tmp_path) == body


def test_uninstall_keeps_foreign_empty_stop_when_ours_is_in_another_event(tmp_path):
    body = {"hooks": {"Stop": [], "SubagentStop": [entry()]}}
    (tmp_path / "settings.json").write_text(json.dumps(body))
    assert main(["uninstall", "claude", "--config-dir", str(tmp_path)]) == 0
    assert read(tmp_path) == body


def test_shape_error_names_the_settings_file(tmp_path, capsys):
    (tmp_path / "settings.json").write_text(json.dumps({"hooks": {"Stop": "x"}}))
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 1
    assert str(tmp_path / "settings.json") in capsys.readouterr().err


def test_unreadable_settings_names_the_file(tmp_path, capsys):
    (tmp_path / "settings.json").mkdir()
    assert main(["install", "claude", "--config-dir", str(tmp_path)]) == 1
    assert str(tmp_path / "settings.json") in capsys.readouterr().err


# --- T11: remembered config dirs ------------------------------------------


def recorded():
    from agent_voice import config

    return config.load().get("claude_config_dirs", [])


def test_install_records_absolute_deduplicated_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["install", "claude", "--config-dir", "rel", "--config-dir", str(tmp_path / "rel"),
                 "--config-dir", str(tmp_path / "b")]) == 0
    assert recorded() == [str(tmp_path / "rel"), str(tmp_path / "b")]
    assert main(["install", "claude", "--config-dir", str(tmp_path / "b"),
                 "--config-dir", str(tmp_path / "c")]) == 0
    assert recorded() == [str(tmp_path / "rel"), str(tmp_path / "b"), str(tmp_path / "c")]


def test_install_records_the_default_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "envdir"))
    assert main(["install", "claude"]) == 0
    assert recorded() == [str(tmp_path / "envdir")]


def test_uninstall_forgets_only_the_given_dirs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    main(["install", "claude", "--config-dir", str(a), "--config-dir", str(b)])
    assert main(["uninstall", "claude", "--config-dir", str(a)]) == 0
    assert recorded() == [str(b)]
    assert main(["uninstall", "claude", "--config-dir", str(b)]) == 0
    assert recorded() == []


def test_failed_install_records_nothing(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "settings.json").write_text("{oops")
    assert main(["install", "claude", "--config-dir", str(tmp_path / "good"), "--config-dir", str(bad)]) == 1
    assert recorded() == []


def test_partial_install_records_only_dirs_that_were_written(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    real_replace = os.replace

    def flaky(src, dst):
        if str(dst).startswith(str(b)):
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr("agent_voice.adapters.claude.os.replace", flaky)
    assert main(["install", "claude", "--config-dir", str(a), "--config-dir", str(b)]) == 1
    assert recorded() == [str(a)]


def test_uninstall_of_an_unknown_dir_does_not_create_a_config_file(tmp_path):
    from agent_voice import config

    assert main(["uninstall", "claude", "--config-dir", str(tmp_path / "x")]) == 0
    assert not config.config_path().exists()
