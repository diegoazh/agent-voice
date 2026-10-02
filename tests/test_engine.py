import numpy as np
import pytest

from agent_voice import engine

REAL_CONFIGURE_ESPEAK = engine.configure_espeak


class FakeKokoro:
    instances = []

    def __init__(self, model_path, voices_path, espeak_config=None):
        self.model_path = model_path
        self.voices_path = voices_path
        self.espeak_config = espeak_config
        self.calls = []
        FakeKokoro.instances.append(self)

    def get_voices(self):
        return ["ef_dora", "em_alex", "em_santa"]

    def create(self, text, voice, speed=1.0, lang="en-us"):
        self.calls.append(dict(text=text, voice=voice, speed=speed, lang=lang))
        return np.zeros(2400, dtype=np.float32), 24000


@pytest.fixture(autouse=True)
def reset_fake():
    FakeKokoro.instances = []


@pytest.fixture(autouse=True)
def no_espeak_workaround(monkeypatch):
    # Never touch the real library/cwd in unit tests unless a test opts in.
    monkeypatch.setattr(engine, "configure_espeak", lambda: None)


@pytest.fixture
def eng():
    return engine.Engine("m.onnx", "v.bin", kokoro_factory=FakeKokoro)


def test_synthesize_uses_owner_defaults(eng):
    samples, sr = eng.synthesize("hola")
    call = FakeKokoro.instances[0].calls[0]
    assert (call["voice"], call["lang"], call["speed"]) == ("em_alex", "es-419", 1.0)
    assert call["text"] == "hola"
    assert sr == 24000
    assert samples.dtype == np.float32 and samples.ndim == 1


def test_unknown_voice_raises_with_available_list(eng):
    with pytest.raises(ValueError) as exc:
        eng.synthesize("hola", voice="nope")
    msg = str(exc.value)
    assert "nope" in msg and "em_alex" in msg and "ef_dora" in msg
    assert FakeKokoro.instances[0].calls == []


@pytest.mark.parametrize("speed", [0.49, 2.01, 0.0, -1.0])
def test_speed_out_of_bounds_raises(eng, speed):
    with pytest.raises(ValueError, match="speed"):
        eng.synthesize("hola", speed=speed)
    assert FakeKokoro.instances == []


@pytest.mark.parametrize("speed", [0.5, 2.0])
def test_speed_bounds_inclusive(eng, speed):
    eng.synthesize("hola", speed=speed)
    assert FakeKokoro.instances[0].calls[0]["speed"] == speed


def test_kokoro_constructed_lazily_and_once(eng):
    assert FakeKokoro.instances == []
    eng.synthesize("uno")
    eng.synthesize("dos")
    eng.available_voices()
    assert len(FakeKokoro.instances) == 1
    assert FakeKokoro.instances[0].model_path == "m.onnx"
    assert FakeKokoro.instances[0].voices_path == "v.bin"


def test_to_wav_bytes_header_and_params():
    import io
    import wave

    samples = np.array([0.0, 0.5, -0.5], dtype=np.float32)
    data = engine.to_wav_bytes(samples, 24000)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.getnframes() == 3
        pcm = np.frombuffer(w.readframes(3), dtype="<i2")
    assert list(pcm) == [0, 16383, -16383]


def test_to_wav_bytes_clips_out_of_range():
    import io
    import wave

    data = engine.to_wav_bytes(np.array([2.0, -3.0], dtype=np.float32), 24000)
    with wave.open(io.BytesIO(data)) as w:
        pcm = np.frombuffer(w.readframes(2), dtype="<i2")
    assert list(pcm) == [32767, -32767]


LONG_DATA = "/" + "/".join(["very-long-directory-name"] * 8) + "/espeak-ng-data"


@pytest.fixture
def phonemizer_wrapper(monkeypatch):
    import phonemizer.backend.espeak.wrapper as w

    calls = []

    def fake_api(lib, data_path):
        calls.append(data_path)

    monkeypatch.setattr(w, "EspeakAPI", fake_api)
    return w, calls


def _set_data_path(monkeypatch, path):
    import espeakng_loader

    monkeypatch.setattr(espeakng_loader, "get_data_path", lambda: path)


def test_configure_espeak_noop_for_short_path(monkeypatch, phonemizer_wrapper):
    w, calls = phonemizer_wrapper
    original = w.EspeakAPI
    _set_data_path(monkeypatch, "/short/espeak-ng-data")
    assert REAL_CONFIGURE_ESPEAK() is None
    assert w.EspeakAPI is original


def test_configure_espeak_long_path_returns_workdir_and_forces_relative(
    monkeypatch, phonemizer_wrapper
):
    w, calls = phonemizer_wrapper
    _set_data_path(monkeypatch, LONG_DATA)
    workdir = REAL_CONFIGURE_ESPEAK()
    assert str(workdir) == LONG_DATA.rsplit("/", 1)[0]
    w.EspeakAPI("lib.dylib", LONG_DATA)
    assert calls == ["."]


def test_configure_espeak_is_idempotent(monkeypatch, phonemizer_wrapper):
    w, calls = phonemizer_wrapper
    _set_data_path(monkeypatch, LONG_DATA)
    first = REAL_CONFIGURE_ESPEAK()
    patched = w.EspeakAPI
    second = REAL_CONFIGURE_ESPEAK()
    assert second == first and w.EspeakAPI is patched
    w.EspeakAPI("lib", LONG_DATA)
    assert calls == ["."]


def test_engine_runs_kokoro_inside_workdir_and_restores_cwd(monkeypatch, tmp_path):
    import os

    seen = {}

    class CwdKokoro(FakeKokoro):
        def __init__(self, model_path, voices_path, espeak_config=None):
            seen["init_cwd"] = os.getcwd()
            seen["config"] = espeak_config
            super().__init__(model_path, voices_path, espeak_config)

        def create(self, text, voice, speed=1.0, lang="en-us"):
            seen["create_cwd"] = os.getcwd()
            return super().create(text, voice, speed, lang)

    monkeypatch.setattr(engine, "configure_espeak", lambda: tmp_path)
    before = os.getcwd()
    engine.Engine("m", "v", kokoro_factory=CwdKokoro).synthesize("hola")
    assert seen["init_cwd"] == seen["create_cwd"] == str(tmp_path.resolve())
    assert seen["config"].data_path == "."
    assert os.getcwd() == before


def test_cwd_restored_when_create_fails(monkeypatch, tmp_path):
    import os

    class Boom(FakeKokoro):
        def create(self, *a, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(engine, "configure_espeak", lambda: tmp_path)
    before = os.getcwd()
    with pytest.raises(RuntimeError):
        engine.Engine("m", "v", kokoro_factory=Boom).synthesize("hola")
    assert os.getcwd() == before


def test_no_espeak_config_when_workaround_not_needed(eng):
    eng.synthesize("hola")
    assert FakeKokoro.instances[0].espeak_config is None
