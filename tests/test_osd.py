import subprocess
import sys
import types

import pytest

from agent_voice import osd


class Fakes:
    """Records compile/spawn calls; nothing real is ever compiled or launched."""

    def __init__(self):
        self.compiles = []
        self.spawns = []
        self.compile_rc = 0
        self.compile_writes = True
        self.compile_error = None
        self.spawn_error = None

    def run(self, argv, **kwargs):
        self.compiles.append((argv, kwargs))
        if self.compile_error:
            raise self.compile_error
        if self.compile_writes:
            with open(argv[argv.index("-o") + 1], "wb") as fh:
                fh.write(b"binary")
        return types.SimpleNamespace(returncode=self.compile_rc)

    def popen(self, argv, **kwargs):
        if self.spawn_error:
            raise self.spawn_error
        self.spawns.append((argv, kwargs))
        return object()


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    f = Fakes()
    src = tmp_path / "src" / "osd.swift"
    src.parent.mkdir()
    src.write_text("// v1")
    monkeypatch.setenv("AGENT_VOICE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AGENT_VOICE_OSD", raising=False)
    monkeypatch.setattr(osd, "SOURCE", src)
    monkeypatch.setattr(osd.sys, "platform", "darwin")
    monkeypatch.setattr(osd.shutil, "which", lambda name: "/usr/bin/swiftc")
    monkeypatch.setattr(osd.subprocess, "run", f.run)
    monkeypatch.setattr(osd.subprocess, "Popen", f.popen)
    f.src = src
    f.cache = tmp_path / "home" / "osd"
    return f


def test_first_show_compiles_into_cache_and_spawns_detached(fakes):
    osd.show("⏩ 1.5x")
    (argv, _), = fakes.compiles
    assert argv[:2] == ["/usr/bin/swiftc", "-O"]
    assert str(fakes.src) in argv
    (spawn_argv, kwargs), = fakes.spawns
    assert spawn_argv[1:] == ["⏩ 1.5x"]
    assert spawn_argv[0].startswith(str(fakes.cache / "osd-"))
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert [p.name for p in fakes.cache.iterdir()] == [spawn_argv[0].rsplit("/", 1)[1]]


def test_cached_binary_is_reused_without_recompiling(fakes):
    osd.show("a")
    osd.show("b")
    assert len(fakes.compiles) == 1
    assert [s[0][1] for s in fakes.spawns] == ["a", "b"]


def test_changed_source_rebuilds_under_a_new_name(fakes):
    osd.show("a")
    fakes.src.write_text("// v2")
    osd.show("b")
    assert len(fakes.compiles) == 2
    assert fakes.spawns[0][0][0] != fakes.spawns[1][0][0]


@pytest.mark.parametrize("value", ["0", "off", "false", "no"])
def test_env_var_disables_everything(fakes, monkeypatch, value):
    monkeypatch.setenv("AGENT_VOICE_OSD", value)
    osd.show("x")
    assert fakes.compiles == [] and fakes.spawns == []


def test_not_macos_does_nothing(fakes, monkeypatch):
    monkeypatch.setattr(osd.sys, "platform", "linux")
    osd.show("x")
    assert fakes.compiles == [] and fakes.spawns == []


def test_missing_swiftc_does_nothing_when_not_cached(fakes, monkeypatch):
    monkeypatch.setattr(osd.shutil, "which", lambda name: None)
    osd.show("x")
    assert fakes.compiles == [] and fakes.spawns == []


def test_cached_binary_still_spawns_without_swiftc(fakes, monkeypatch):
    osd.show("a")
    monkeypatch.setattr(osd.shutil, "which", lambda name: None)
    osd.show("b")
    assert len(fakes.spawns) == 2


def test_compile_failure_is_silent_and_leaves_no_files(fakes):
    fakes.compile_rc = 1
    osd.show("x")
    assert fakes.spawns == []
    assert list(fakes.cache.iterdir()) == []


@pytest.mark.parametrize(
    "error", [OSError("boom"), subprocess.TimeoutExpired("swiftc", 1)]
)
def test_compile_exception_is_silent(fakes, error):
    fakes.compile_error = error
    osd.show("x")
    assert fakes.spawns == []


def test_compile_that_produces_nothing_is_silent(fakes):
    fakes.compile_writes = False
    osd.show("x")
    assert fakes.spawns == []


def test_unwritable_cache_is_silent(fakes, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("file")
    monkeypatch.setenv("AGENT_VOICE_HOME", str(blocker))
    osd.show("x")
    assert fakes.spawns == []


def test_spawn_failure_is_silent(fakes):
    fakes.spawn_error = OSError("nope")
    osd.show("x")


def test_missing_source_is_silent(fakes):
    fakes.src.unlink()
    osd.show("x")
    assert fakes.spawns == []


def test_shipped_swift_source_exists_and_takes_text_from_argv():
    text = osd.SOURCE.read_text()
    assert "CommandLine.arguments" in text
    assert sys.platform  # the source itself is only compiled by opt-in checks
