"""Brief on-screen display (macOS) for speed/volume changes.

`show(text)` starts the cached helper binary detached; it never raises, never waits and
never compiles. `build()` is the explicit, user-initiated step that compiles the shipped
Swift helper into the per-user cache (`agent-voice osd build`, also tried by `install`).
Compiling is kept off the hotkey path because `/usr/bin/swiftc` is an xcrun shim that can
pop the Command Line Tools install dialog on machines without them.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_voice import config

SOURCE = Path(__file__).parent / "osd_helper" / "osd.swift"
_COMPILE_TIMEOUT_S = 120
_OFF_VALUES = {"0", "off", "false", "no"}


def _cache_dir() -> Path:
    return config.config_path().parent / "osd"


class BuildError(Exception):
    """The helper could not be built; the message is a one-line reason."""


def enabled() -> bool:
    return sys.platform == "darwin" and os.environ.get("AGENT_VOICE_OSD", "").strip().lower() not in _OFF_VALUES


def _binary_path() -> Path:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:12]
    return _cache_dir() / f"osd-{digest}"


def _compile(swiftc: str, binary: Path) -> None:
    """Build into a temp name, then rename atomically so concurrent runs never see a partial file."""
    fd, tmp = tempfile.mkstemp(dir=binary.parent, prefix=".osd-", suffix=".tmp")
    os.close(fd)
    try:
        try:
            result = subprocess.run(
                [swiftc, "-O", str(SOURCE), "-o", tmp],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_COMPILE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise BuildError(f"swiftc timed out after {_COMPILE_TIMEOUT_S}s") from None
        if result.returncode != 0 or not os.path.getsize(tmp):
            raise BuildError("compile failed (swiftc returned an error)")
        os.chmod(tmp, 0o755)
        os.replace(tmp, binary)
    finally:
        Path(tmp).unlink(missing_ok=True)


def build() -> Path:
    """Compile the helper if the cache lacks it; return the binary path or raise BuildError."""
    if sys.platform != "darwin":
        raise BuildError("the on-screen display is macOS only")
    try:
        binary = _binary_path()
        if binary.exists():
            return binary
        swiftc = shutil.which("swiftc")
        if not swiftc:
            raise BuildError("swiftc not found; install the Command Line Tools with `xcode-select --install`")
        binary.parent.mkdir(parents=True, exist_ok=True)
        _compile(swiftc, binary)
    except BuildError:
        raise
    except OSError as exc:
        raise BuildError(str(exc)) from exc
    return binary


def show(text: str) -> None:
    """Flash `text` on screen for about a second. Silent no-op unless the helper is already built."""
    try:
        if not enabled():
            return
        binary = _binary_path()
        if not binary.exists():
            return
        subprocess.Popen(
            [str(binary), text],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return
