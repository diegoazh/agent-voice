"""Brief on-screen display (macOS) for speed/volume changes.

`show(text)` is the whole interface: it compiles the shipped Swift helper on first use,
caches the binary per user, and starts it detached. It never raises and never waits.
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


def _compile(binary: Path) -> bool:
    """Build into a temp name, then rename atomically so concurrent runs never see a partial file."""
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return False
    fd, tmp = tempfile.mkstemp(dir=binary.parent, prefix=".osd-", suffix=".tmp")
    os.close(fd)
    try:
        result = subprocess.run(
            [swiftc, "-O", str(SOURCE), "-o", tmp],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_COMPILE_TIMEOUT_S,
        )
        if result.returncode != 0 or not os.path.getsize(tmp):
            return False
        os.chmod(tmp, 0o755)
        os.replace(tmp, binary)
        return True
    finally:
        Path(tmp).unlink(missing_ok=True)


def _ensure_binary() -> Path | None:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:12]
    cache = _cache_dir()
    binary = cache / f"osd-{digest}"
    if binary.exists():
        return binary
    cache.mkdir(parents=True, exist_ok=True)
    return binary if _compile(binary) else None


def show(text: str) -> None:
    """Flash `text` on screen for about a second. Silent no-op on any failure."""
    try:
        if os.environ.get("AGENT_VOICE_OSD", "").strip().lower() in _OFF_VALUES:
            return
        if sys.platform != "darwin":
            return
        binary = _ensure_binary()
        if binary is None:
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
