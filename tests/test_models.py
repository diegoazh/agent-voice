import hashlib
import json
import os
from pathlib import Path

import pytest

from agent_voice import models


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


REAL_PINNED = dict(models.PINNED)
MODEL = b"fake-model-bytes"
VOICES = b"fake-voices"


@pytest.fixture(autouse=True)
def tiny_table(monkeypatch):
    monkeypatch.setattr(
        models,
        "PINNED",
        {
            "kokoro-v1.0.onnx": models.PinnedFile(
                "kokoro-v1.0.onnx", sha(MODEL), len(MODEL)
            ),
            "kokoro-v1.0.fp16.onnx": models.PinnedFile(
                "kokoro-v1.0.fp16.onnx", sha(b"half"), 4
            ),
            "voices-v1.0.bin": models.PinnedFile(
                "voices-v1.0.bin", sha(VOICES), len(VOICES)
            ),
        },
    )
    monkeypatch.delenv("AGENT_VOICE_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


class FakeFetch:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def __call__(self, url, dest, progress_cb=None):
        self.calls.append(url)
        name = url.rsplit("/", 1)[1]
        Path(dest).write_bytes(self.payloads[name])
        if progress_cb:
            progress_cb(name, len(self.payloads[name]), len(self.payloads[name]))


def good_payloads():
    return {"kokoro-v1.0.onnx": MODEL, "voices-v1.0.bin": VOICES}


# --- directory resolution -------------------------------------------------


def test_dir_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VOICE_HOME", str(tmp_path / "home"))
    assert models.models_dir(tmp_path / "x") == tmp_path / "x"


def test_dir_agent_voice_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VOICE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert models.models_dir() == tmp_path / "home" / "models"


def test_dir_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert models.models_dir() == tmp_path / "xdg" / "agent-voice" / "models"


def test_dir_default_home(monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: Path("/h"))
    assert models.models_dir() == Path("/h/.local/share/agent-voice/models")


# --- variants -------------------------------------------------------------


def test_unknown_variant_value_error(tmp_path):
    with pytest.raises(ValueError, match="int4"):
        models.resolve("int4", models_dir=tmp_path)
    with pytest.raises(ValueError):
        models.download("int4", models_dir=tmp_path, fetch=FakeFetch({}))


def test_variant_file_names():
    assert models.VARIANTS == {
        "fp32": "kokoro-v1.0.onnx",
        "fp16": "kokoro-v1.0.fp16.onnx",
        "int8": "kokoro-v1.0.int8.onnx",
    }


def test_real_pins_complete():
    for fname in [*models.VARIANTS.values(), models.VOICES_FILE]:
        pin = REAL_PINNED[fname]
        assert len(pin.sha256) == 64 and pin.size > 0
    assert REAL_PINNED["kokoro-v1.0.onnx"].size == 325505369
    assert models.BASE_URL.startswith("https://") and "model-files-v1.1" in models.BASE_URL


# --- verify / resolve -----------------------------------------------------


def test_verify(tmp_path):
    f = tmp_path / "a"
    f.write_bytes(b"abc")
    assert models.verify(f, sha(b"abc"))
    assert not models.verify(f, sha(b"abd"))


def test_resolve_missing_raises_with_hint(tmp_path):
    with pytest.raises(models.ModelsMissingError) as exc:
        models.resolve("fp32", models_dir=tmp_path)
    assert "agent-voice download --model fp32" in str(exc.value)


def test_resolve_ok_after_download(tmp_path):
    models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(good_payloads()))
    assert models.resolve("fp32", models_dir=tmp_path) == (
        tmp_path / "kokoro-v1.0.onnx",
        tmp_path / "voices-v1.0.bin",
    )


def test_resolve_never_hashes_or_fetches(tmp_path, monkeypatch):
    models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(good_payloads()))

    def boom(*a, **k):
        raise AssertionError("hashed")

    monkeypatch.setattr(models, "verify", boom)
    models.resolve("fp32", models_dir=tmp_path)


def test_resolve_file_without_sidecar_is_unverified(tmp_path):
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(MODEL)
    (tmp_path / "voices-v1.0.bin").write_bytes(VOICES)
    with pytest.raises(models.ModelsMissingError):
        models.resolve("fp32", models_dir=tmp_path)


def test_resolve_stale_size(tmp_path):
    models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(good_payloads()))
    f = tmp_path / "kokoro-v1.0.onnx"
    st = f.stat()
    f.write_bytes(MODEL + b"x")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    with pytest.raises(models.ModelsMissingError):
        models.resolve("fp32", models_dir=tmp_path)


def test_resolve_stale_mtime(tmp_path):
    models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(good_payloads()))
    f = tmp_path / "kokoro-v1.0.onnx"
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    with pytest.raises(models.ModelsMissingError):
        models.resolve("fp32", models_dir=tmp_path)


def test_resolve_sidecar_wrong_sha_or_corrupt(tmp_path):
    models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(good_payloads()))
    side = tmp_path / "kokoro-v1.0.onnx.verified"
    data = json.loads(side.read_text())
    data["sha256"] = "0" * 64
    side.write_text(json.dumps(data))
    with pytest.raises(models.ModelsMissingError):
        models.resolve("fp32", models_dir=tmp_path)
    side.write_text("not json")
    with pytest.raises(models.ModelsMissingError):
        models.resolve("fp32", models_dir=tmp_path)


def test_sidecar_content(tmp_path):
    models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(good_payloads()))
    f = tmp_path / "kokoro-v1.0.onnx"
    data = json.loads((tmp_path / "kokoro-v1.0.onnx.verified").read_text())
    assert data == {
        "sha256": sha(MODEL),
        "size": len(MODEL),
        "mtime_ns": f.stat().st_mtime_ns,
    }


# --- download -------------------------------------------------------------


def test_download_adopts_existing_good_file_without_fetch(tmp_path):
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(MODEL)
    (tmp_path / "voices-v1.0.bin").write_bytes(VOICES)
    fetch = FakeFetch({})
    models.download("fp32", models_dir=tmp_path, fetch=fetch)
    assert fetch.calls == []
    assert models.resolve("fp32", models_dir=tmp_path)


def test_download_fetches_missing_only_with_pinned_url(tmp_path):
    (tmp_path / "voices-v1.0.bin").write_bytes(VOICES)
    fetch = FakeFetch(good_payloads())
    models.download("fp32", models_dir=tmp_path, fetch=fetch)
    assert fetch.calls == [models.BASE_URL + "/kokoro-v1.0.onnx"]
    assert not list(tmp_path.glob("*.part"))


def test_download_creates_dir_and_reports_progress(tmp_path):
    seen = []
    models.download(
        "fp32",
        models_dir=tmp_path / "new" / "models",
        fetch=FakeFetch(good_payloads()),
        progress_cb=lambda *a: seen.append(a),
    )
    assert seen


def test_download_replaces_corrupt_existing_file(tmp_path):
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(b"corrupt")
    (tmp_path / "voices-v1.0.bin").write_bytes(VOICES)
    models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(good_payloads()))
    assert (tmp_path / "kokoro-v1.0.onnx").read_bytes() == MODEL


def test_checksum_mismatch_deletes_part_and_raises(tmp_path):
    bad = {"kokoro-v1.0.onnx": b"tampered-bytes!", "voices-v1.0.bin": VOICES}
    with pytest.raises(models.ChecksumError):
        models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(bad))
    assert not (tmp_path / "kokoro-v1.0.onnx").exists()
    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob("*.verified"))


def test_failed_fetch_leaves_no_partial_final_file(tmp_path):
    def failing(url, dest, progress_cb=None):
        Path(dest).write_bytes(b"half")
        raise OSError("boom")

    with pytest.raises(OSError):
        models.download("fp32", models_dir=tmp_path, fetch=failing)
    assert not (tmp_path / "kokoro-v1.0.onnx").exists()
    assert not list(tmp_path.glob("*.part"))


def test_failed_download_keeps_previous_good_file_untouched(tmp_path):
    # a stale/old file is only replaced atomically after a verified fetch
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(b"corrupt")
    bad = {"kokoro-v1.0.onnx": b"tampered-bytes!", "voices-v1.0.bin": VOICES}
    with pytest.raises(models.ChecksumError):
        models.download("fp32", models_dir=tmp_path, fetch=FakeFetch(bad))
    assert (tmp_path / "kokoro-v1.0.onnx").read_bytes() == b"corrupt"


# --- default fetch --------------------------------------------------------


def test_default_fetch_rejects_non_https(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        models.default_fetch("http://example.com/x", tmp_path / "x.part")


def test_default_fetch_streams(tmp_path, monkeypatch):
    import io

    class Resp(io.BytesIO):
        headers = {"Content-Length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    monkeypatch.setattr(models.urllib.request, "urlopen", lambda url: Resp(b"abcdef"))
    seen = []
    dest = tmp_path / "x.part"
    models.default_fetch(
        "https://example.com/f.bin", dest, lambda *a: seen.append(a), chunk_size=4
    )
    assert dest.read_bytes() == b"abcdef"
    assert seen[-1] == ("f.bin", 6, 6)


def test_default_fetch_without_content_length(tmp_path, monkeypatch):
    import io

    class Resp(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    monkeypatch.setattr(models.urllib.request, "urlopen", lambda url: Resp(b"ab"))
    dest = tmp_path / "y.part"
    models.default_fetch("https://example.com/y", dest)
    assert dest.read_bytes() == b"ab"
