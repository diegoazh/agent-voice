import os
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


def test_show_never_compiles_and_is_silent_without_a_binary(fakes):
    osd.show("x")
    assert fakes.compiles == [] and fakes.spawns == []


def test_show_spawns_the_built_binary_detached(fakes):
    path = osd.build()
    osd.show("⏩ 1.5x")
    (spawn_argv, kwargs), = fakes.spawns
    assert spawn_argv == [str(path), "⏩ 1.5x"]
    assert path.parent == fakes.cache and path.name.startswith("osd-")
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_build_compiles_once_and_reuses_the_cached_binary(fakes):
    first = osd.build()
    second = osd.build()
    assert first == second and len(fakes.compiles) == 1
    (argv, _), = fakes.compiles
    assert argv[:2] == ["/usr/bin/swiftc", "-O"] and str(fakes.src) in argv
    assert [p.name for p in fakes.cache.iterdir()] == [first.name]
    assert os.access(first, os.X_OK)


def test_changed_source_needs_a_new_build(fakes):
    old = osd.build()
    fakes.src.write_text("// v2")
    osd.show("stale")
    assert fakes.spawns == []
    assert osd.build() != old


def test_build_without_swiftc_explains_how_to_get_it(fakes, monkeypatch):
    monkeypatch.setattr(osd.shutil, "which", lambda name: None)
    with pytest.raises(osd.BuildError, match="xcode-select --install"):
        osd.build()
    assert fakes.compiles == []


def test_build_failure_reports_compile_failed_and_leaves_no_files(fakes):
    fakes.compile_rc = 1
    with pytest.raises(osd.BuildError, match="compile failed"):
        osd.build()
    assert list(fakes.cache.iterdir()) == []


def test_build_timeout_is_reported(fakes):
    fakes.compile_error = subprocess.TimeoutExpired("swiftc", 1)
    with pytest.raises(osd.BuildError, match="timed out"):
        osd.build()


def test_build_oserror_is_reported(fakes):
    fakes.compile_error = OSError("boom")
    with pytest.raises(osd.BuildError, match="boom"):
        osd.build()


def test_build_that_produces_nothing_fails(fakes):
    fakes.compile_writes = False
    with pytest.raises(osd.BuildError):
        osd.build()
    assert list(fakes.cache.iterdir()) == []


def test_build_with_unwritable_cache_fails(fakes, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("file")
    monkeypatch.setenv("AGENT_VOICE_HOME", str(blocker))
    with pytest.raises(osd.BuildError):
        osd.build()


def test_build_off_macos_fails(fakes, monkeypatch):
    monkeypatch.setattr(osd.sys, "platform", "linux")
    with pytest.raises(osd.BuildError, match="macOS"):
        osd.build()


@pytest.mark.parametrize("value", ["0", "off", "false", "no"])
def test_env_var_disables_everything(fakes, monkeypatch, value):
    monkeypatch.setenv("AGENT_VOICE_OSD", value)
    osd.show("x")
    assert fakes.compiles == [] and fakes.spawns == []


def test_not_macos_does_nothing(fakes, monkeypatch):
    monkeypatch.setattr(osd.sys, "platform", "linux")
    osd.show("x")
    assert fakes.compiles == [] and fakes.spawns == []


def test_built_binary_still_spawns_without_swiftc(fakes, monkeypatch):
    osd.build()
    monkeypatch.setattr(osd.shutil, "which", lambda name: None)
    osd.show("b")
    assert len(fakes.spawns) == 1


def test_spawn_failure_is_silent(fakes):
    osd.build()
    fakes.spawn_error = OSError("nope")
    osd.show("x")


def test_missing_source_is_silent(fakes):
    fakes.src.unlink()
    osd.show("x")
    assert fakes.spawns == []
    with pytest.raises(osd.BuildError):
        osd.build()


def test_shipped_swift_source_exists_and_takes_text_from_argv():
    text = osd.SOURCE.read_text()
    assert "CommandLine.arguments" in text
    # Single-instance + hard deadline live in Swift (not unit-testable from Python).
    assert "DistributedNotificationCenter" in text
    assert "exit(0)" in text and "hardDeadlineSeconds" in text
    assert sys.platform  # the source itself is only compiled by opt-in checks
