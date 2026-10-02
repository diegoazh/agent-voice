import json
import subprocess

import pytest

from agent_voice import focus

GHOSTTY = 'bundleID="com.mitchellh.ghostty"'


class Fake:
    """Scripted command runner: maps the program name to a (rc, stdout) or a callable."""

    def __init__(self, **replies):
        self.replies = replies
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        reply = self.replies[argv[0] if argv[0] in self.replies else "default"]
        if isinstance(reply, Exception):
            raise reply
        rc, out = reply(argv) if callable(reply) else reply
        return subprocess.CompletedProcess(argv, rc, stdout=out)


def front(bundle_line=GHOSTTY):
    def reply(argv):
        return (0, "ASN:0x0-0x42042:\n") if argv[1] == "front" else (0, f"[ NULL ] ASN\n    {bundle_line}\n")

    return reply


def test_ghostty_frontmost_true_when_bundle_matches():
    runner = Fake(lsappinfo=front())
    det = focus.HerdrGhosttyDetector("w1:p1", run=runner)
    assert det.ghostty_frontmost() is True
    assert runner.calls[0] == ["lsappinfo", "front"]
    assert runner.calls[1][:4] == ["lsappinfo", "info", "-only", "bundleid"]
    assert runner.calls[1][-1] == "ASN:0x0-0x42042:"


def test_ghostty_frontmost_false_for_another_app():
    det = focus.HerdrGhosttyDetector("w1:p1", run=Fake(lsappinfo=front('bundleID="com.apple.Safari"')))
    assert det.ghostty_frontmost() is False


@pytest.mark.parametrize(
    "runner",
    [
        Fake(lsappinfo=(1, "")),
        Fake(lsappinfo=(0, "")),
        Fake(lsappinfo=front("no bundle here")),
        Fake(lsappinfo=OSError("missing")),
        Fake(lsappinfo=subprocess.TimeoutExpired("lsappinfo", 2)),
        Fake(lsappinfo=lambda argv: (0, "ASN:1:\n") if argv[1] == "front" else (1, "")),
    ],
)
def test_ghostty_frontmost_unknown_when_the_command_fails(runner):
    assert focus.HerdrGhosttyDetector("w1:p1", run=runner).ghostty_frontmost() is None


def pane(focused):
    return (0, json.dumps({"id": "x", "result": {"pane": {"pane_id": "w1:p1", "focused": focused}}}))


@pytest.mark.parametrize("focused", [True, False])
def test_herdr_pane_focused_reads_the_pane_flag(focused):
    runner = Fake(herdr=pane(focused))
    det = focus.HerdrGhosttyDetector("w1:p1", run=runner)
    assert det.herdr_pane_focused() is focused
    assert runner.calls == [["herdr", "pane", "get", "w1:p1"]]


def test_herdr_binary_is_configurable():
    runner = Fake(default=pane(True))
    focus.HerdrGhosttyDetector("w1:p1", run=runner, herdr_bin="/opt/h/herdr").herdr_pane_focused()
    assert runner.calls[0][0] == "/opt/h/herdr"


def test_herdr_pane_gone_raises():
    err = json.dumps({"error": {"code": "pane_not_found", "message": "gone"}})
    det = focus.HerdrGhosttyDetector("w1:p1", run=Fake(herdr=(1, err)))
    with pytest.raises(focus.PaneGoneError):
        det.herdr_pane_focused()


@pytest.mark.parametrize(
    "reply",
    [
        (1, json.dumps({"error": {"code": "server_not_running"}})),
        (1, ""),
        (0, "not json"),
        (0, "[]"),
        (0, json.dumps({"result": {"pane": {"focused": "yes"}}})),
        (0, json.dumps({"result": {}})),
        OSError("no herdr"),
        subprocess.TimeoutExpired("herdr", 2),
    ],
)
def test_herdr_pane_focused_unknown_on_any_other_failure(reply):
    assert focus.HerdrGhosttyDetector("w1:p1", run=Fake(herdr=reply)).herdr_pane_focused() is None


def detector(ghostty, herdr):
    runner = Fake(lsappinfo=front(GHOSTTY if ghostty else 'bundleID="x.y"'), herdr=herdr)
    return focus.HerdrGhosttyDetector("w1:p1", run=runner), runner


def test_focused_needs_ghostty_in_front_and_the_pane_focused():
    det, _ = detector(True, pane(True))
    assert det.is_focused() is True


def test_background_ghostty_is_not_focused_and_skips_herdr():
    det, runner = detector(False, pane(True))
    assert det.is_focused() is False
    assert all(c[0] == "lsappinfo" for c in runner.calls)


def test_ghostty_in_front_but_another_pane_focused():
    det, _ = detector(True, pane(False))
    assert det.is_focused() is False


def test_unknown_when_a_part_is_unknown_unless_the_other_says_no():
    assert detector(True, (1, ""))[0].is_focused() is None
    assert focus.HerdrGhosttyDetector("p", run=Fake(lsappinfo=(1, ""), herdr=pane(True))).is_focused() is None
    assert focus.HerdrGhosttyDetector("p", run=Fake(lsappinfo=(1, ""), herdr=pane(False))).is_focused() is False


def test_pane_gone_propagates_from_is_focused():
    err = json.dumps({"error": {"code": "pane_not_found"}})
    det, _ = detector(True, (1, err))
    with pytest.raises(focus.PaneGoneError):
        det.is_focused()


ENV = {
    "TERM_PROGRAM": "ghostty",
    "HERDR_ENV": "1",
    "HERDR_PANE_ID": "w1Q:p1",
    "HERDR_BIN_PATH": "/opt/homebrew/bin/herdr",
}


def test_detect_from_env_returns_the_herdr_detector():
    det = focus.detect_from_env(ENV)
    assert isinstance(det, focus.HerdrGhosttyDetector)
    assert det.pane_id == "w1Q:p1"
    assert det._herdr == "/opt/homebrew/bin/herdr"


def test_detect_from_env_falls_back_to_herdr_on_path():
    env = {k: v for k, v in ENV.items() if k != "HERDR_BIN_PATH"}
    assert focus.detect_from_env(env)._herdr == "herdr"


@pytest.mark.parametrize(
    "drop",
    ["TERM_PROGRAM", "HERDR_ENV", "HERDR_PANE_ID"],
)
def test_detect_from_env_none_when_a_piece_is_missing(drop):
    assert focus.detect_from_env({k: v for k, v in ENV.items() if k != drop}) is None


def test_detect_from_env_none_for_another_terminal():
    assert focus.detect_from_env({**ENV, "TERM_PROGRAM": "iTerm.app"}) is None


def test_default_runner_uses_a_timeout_and_captures_text(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="x")

    monkeypatch.setattr(focus.subprocess, "run", fake_run)
    focus._run(["echo"])
    assert seen["timeout"] > 0 and seen["capture_output"] and seen["text"]


def test_check_pane_asks_only_herdr_even_when_ghostty_is_in_the_background():
    runner = Fake(default=pane(False))
    focus.HerdrGhosttyDetector("w1:p1", run=runner).check_pane()
    assert runner.calls == [["herdr", "pane", "get", "w1:p1"]]  # no lsappinfo


def test_check_pane_raises_when_the_pane_is_gone_and_is_quiet_when_unknown():
    err = json.dumps({"error": {"code": "pane_not_found"}})
    with pytest.raises(focus.PaneGoneError):
        focus.HerdrGhosttyDetector("w1:p1", run=Fake(herdr=(1, err))).check_pane()
    focus.HerdrGhosttyDetector("w1:p1", run=Fake(herdr=(1, ""))).check_pane()


# --- T15 G6: strict pane id, bounded hook runner ----------------------------


@pytest.mark.parametrize("bad", ["--help", "w1 p1", "a;b", "w1:p1\n"])
def test_detect_from_env_none_for_an_invalid_pane_id(bad):
    assert focus.detect_from_env({**ENV, "HERDR_PANE_ID": bad}) is None


def test_bounded_run_caps_every_call_by_the_remaining_budget():
    now = [0.0]
    seen = []

    def inner(argv, timeout=2.0):
        seen.append(timeout)
        now[0] += 0.2
        return "ok"

    run = focus.bounded_run(0.5, run=inner, clock=lambda: now[0])
    assert run(["a"]) == "ok" and run(["b"], timeout=0.1) == "ok" and run(["c"]) == "ok"
    assert seen == [pytest.approx(0.5), pytest.approx(0.1), pytest.approx(0.1)]
    with pytest.raises(subprocess.TimeoutExpired):
        run(["d"])  # budget spent: no further process is started
    assert len(seen) == 3
