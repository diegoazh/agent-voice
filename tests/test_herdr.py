import json
import subprocess
from types import SimpleNamespace

from agent_voice import herdr
from agent_voice.herdr import _run as REAL_RUN  # conftest replaces herdr._run per test

SID = "86c4cf97-e5ba-4c56-b68f-24f657c78e54"


def pane(pane_id="w1:p1", focused=False, **extra):
    return {"pane_id": pane_id, "focused": focused, "cwd": "/work/app", **extra}


def get_json(p):
    return json.dumps({"id": "cli:pane:get", "result": {"pane": p, "type": "pane_info"}})


def list_json(*panes):
    return json.dumps({"id": "cli:pane:list", "result": {"panes": list(panes)}})


class FakeRun:
    """Records argv; `outputs` maps the herdr binary to stdout (or an exception to raise)."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, argv, timeout=2.0):
        self.calls.append(argv)
        out = self.outputs.get(argv[0], FileNotFoundError(argv[0]))
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(stdout=out, returncode=0)


def test_without_pane_env_the_focused_pane_of_the_list_is_returned():
    run = FakeRun({"herdr": list_json(pane("w1:p1"), pane("w2:p3", focused=True), pane("w2:p4"))})
    assert herdr.current_pane({}, run=run)["pane_id"] == "w2:p3"
    assert run.calls == [["herdr", "pane", "list"]]


def test_no_focused_pane_in_the_list_is_none():
    assert herdr.current_pane({}, run=FakeRun({"herdr": list_json(pane("w1:p1"))})) is None


def test_pane_id_in_env_gets_exactly_that_pane():
    run = FakeRun({"herdr": get_json(pane("w3:p2"))})
    assert herdr.current_pane({"HERDR_PANE_ID": "w3:p2"}, run=run)["pane_id"] == "w3:p2"
    assert run.calls == [["herdr", "pane", "get", "w3:p2"]]


def test_herdr_bin_path_is_preferred():
    run = FakeRun({"/x/herdr": list_json(pane(focused=True))})
    assert herdr.current_pane({"HERDR_BIN_PATH": "/x/herdr"}, run=run) is not None
    assert run.calls[0][0] == "/x/herdr"


def test_falls_back_to_homebrew_when_herdr_is_not_on_path():
    run = FakeRun({herdr.HOMEBREW_BIN: list_json(pane(focused=True))})
    assert herdr.current_pane({}, run=run) is not None
    assert [c[0] for c in run.calls] == ["herdr", herdr.HOMEBREW_BIN]


def test_herdr_missing_everywhere_is_none():
    assert herdr.current_pane({}, run=FakeRun({})) is None


def test_garbage_timeout_and_error_outputs_are_none():
    for out in (
        "not json",
        "[]",
        json.dumps({"error": {"code": "pane_not_found"}}),
        json.dumps({"result": {"pane": "x"}}),
        json.dumps({"result": {"panes": "x"}}),
        subprocess.TimeoutExpired("herdr", 2),
    ):
        assert herdr.current_pane({}, run=FakeRun({"herdr": out})) is None
        assert herdr.current_pane({"HERDR_PANE_ID": "w1:p1"}, run=FakeRun({"herdr": out})) is None


def test_default_runner_executes_a_real_process(tmp_path, monkeypatch):
    monkeypatch.setattr(herdr, "_run", REAL_RUN)
    script = tmp_path / "herdr"
    script.write_text("#!/bin/sh\necho '" + list_json(pane(focused=True)) + "'\n")
    script.chmod(0o755)
    assert herdr.current_pane({"HERDR_BIN_PATH": str(script)})["focused"] is True


def test_claude_session_id_only_for_a_claude_id_session():
    ok = {"agent": "claude", "agent_session": {"agent": "claude", "kind": "id", "value": SID}}
    assert herdr.claude_session_id(ok) == SID
    for bad in (
        {},
        {"agent": "codex", "agent_session": {"agent": "codex", "kind": "id", "value": SID}},
        {"agent": "claude", "agent_session": {"agent": "claude", "kind": "path", "value": SID}},
        {"agent": "claude", "agent_session": {"agent": "claude", "kind": "id", "value": 3}},
        {"agent": "claude", "agent_session": {"agent": "claude", "kind": "id", "value": "../x"}},
        {"agent": "claude", "agent_session": "x"},
    ):
        assert herdr.claude_session_id(bad) is None


# --- T15 G6: strict ids -----------------------------------------------------


def test_session_id_with_a_trailing_newline_is_rejected():
    p = {"agent": "claude", "agent_session": {"kind": "id", "value": SID + "\n"}}
    assert herdr.claude_session_id(p) is None


import pytest  # noqa: E402


@pytest.mark.parametrize("bad", ["--help", "-h", "w1 p1", "a;b", "w1:p1\n", "w1/p1", "../x"])
def test_invalid_pane_id_is_never_passed_to_herdr(bad):
    run = FakeRun({"herdr": get_json(pane())})
    assert herdr.current_pane({"HERDR_PANE_ID": bad}, run=run) is None
    assert run.calls == []


def test_real_pane_id_shape_is_passed_through():
    run = FakeRun({"herdr": get_json(pane("w1Q:p1"))})
    assert herdr.current_pane({"HERDR_PANE_ID": "w1Q:p1"}, run=run)["pane_id"] == "w1Q:p1"
    assert run.calls == [["herdr", "pane", "get", "w1Q:p1"]]
