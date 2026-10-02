"""Pinned Kokoro model store.

Network access happens only inside :func:`download`. :func:`resolve`, used on
the speak path, never touches the network and never hashes large files: it
trusts a small ``<file>.verified`` sidecar written after a successful
verification, as long as the file's size and mtime still match.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

BASE_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
)
VOICES_FILE = "voices-v1.0.bin"
VARIANTS = {
    "fp32": "kokoro-v1.0.onnx",
    "fp16": "kokoro-v1.0.fp16.onnx",
    "int8": "kokoro-v1.0.int8.onnx",
}


@dataclass(frozen=True)
class PinnedFile:
    name: str
    sha256: str
    size: int


PINNED: dict[str, PinnedFile] = {
    f.name: f
    for f in (
        PinnedFile(
            "kokoro-v1.0.onnx",
            "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
            325505369,
        ),
        PinnedFile(
            "kokoro-v1.0.fp16.onnx",
            "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96",
            163527961,
        ),
        PinnedFile(
            "kokoro-v1.0.int8.onnx",
            "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c",
            114119327,
        ),
        PinnedFile(
            VOICES_FILE,
            "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
            28214398,
        ),
    )
}

# fetch(url, dest_path, progress_cb) ; progress_cb(name, done_bytes, total_bytes)
ProgressCb = Callable[[str, int, int], None]
Fetch = Callable[..., None]


class ModelsMissingError(RuntimeError):
    """Model files are absent or not verified; the user must run download."""


class ChecksumError(RuntimeError):
    """A downloaded file did not match its pinned SHA256."""


def models_dir(override: str | os.PathLike | None = None) -> Path:
    if override is not None:
        return Path(override)
    home = os.environ.get("AGENT_VOICE_HOME")
    if home:
        return Path(home) / "models"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "agent-voice" / "models"
    return Path.home() / ".local" / "share" / "agent-voice" / "models"


_models_dir = models_dir  # alias: resolve/download shadow the name with a param


def _files_for(variant: str) -> list[PinnedFile]:
    if variant not in VARIANTS:
        raise ValueError(
            f"unknown model variant {variant!r}; choose one of: {', '.join(VARIANTS)}"
        )
    return [PINNED[VARIANTS[variant]], PINNED[VOICES_FILE]]


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".verified")


def verify(path: str | os.PathLike, expected_sha: str) -> bool:
    """Full SHA256 of ``path`` compared to ``expected_sha`` (slow; download path)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha


def _write_sidecar(path: Path, pin: PinnedFile) -> None:
    st = path.stat()
    data = {"sha256": pin.sha256, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    tmp = _sidecar(path).with_suffix(".verified.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, _sidecar(path))


def _is_verified(path: Path, pin: PinnedFile) -> bool:
    try:
        st = path.stat()
        data = json.loads(_sidecar(path).read_text())
        return (
            data["sha256"] == pin.sha256
            and data["size"] == st.st_size == pin.size
            and data["mtime_ns"] == st.st_mtime_ns
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def resolve(
    variant: str, *, models_dir: str | os.PathLike | None = None
) -> tuple[Path, Path]:
    """Return ``(model_path, voices_path)`` or raise :class:`ModelsMissingError`."""
    pins = _files_for(variant)
    base = _models_dir(models_dir)
    paths = [base / p.name for p in pins]
    if not all(_is_verified(path, pin) for path, pin in zip(paths, pins)):
        raise ModelsMissingError(
            f"model files for variant {variant!r} are missing or unverified; "
            f"run: agent-voice download --model {variant}"
        )
    return paths[0], paths[1]


def default_fetch(
    url: str,
    dest: str | os.PathLike,
    progress_cb: ProgressCb | None = None,
    *,
    chunk_size: int = 1 << 20,
) -> None:
    """Stream ``url`` to ``dest`` using only the standard library (HTTPS only)."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    name = url.rsplit("/", 1)[-1]
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(chunk_size):
            out.write(chunk)
            done += len(chunk)
            if progress_cb:
                progress_cb(name, done, total)


def download(
    variant: str,
    *,
    fetch: Fetch = default_fetch,
    models_dir: str | os.PathLike | None = None,
    progress_cb: ProgressCb | None = None,
) -> tuple[Path, Path]:
    """Ensure model + voices are present and verified; the only networked path."""
    pins = _files_for(variant)
    base = _models_dir(models_dir)
    base.mkdir(parents=True, exist_ok=True)
    for pin in pins:
        final = base / pin.name
        if final.is_file() and verify(final, pin.sha256):
            _write_sidecar(final, pin)
            continue
        part = base / (pin.name + ".part")
        try:
            fetch(f"{BASE_URL}/{pin.name}", part, progress_cb)
            if not verify(part, pin.sha256):
                raise ChecksumError(
                    f"SHA256 mismatch for {pin.name}; downloaded file discarded"
                )
            os.replace(part, final)
        finally:
            part.unlink(missing_ok=True)
        _write_sidecar(final, pin)
    return base / pins[0].name, base / pins[1].name
