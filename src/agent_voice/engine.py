"""Thin synthesis wrapper over kokoro_onnx."""

from __future__ import annotations

import contextlib
import io
import os
import wave
from pathlib import Path

import numpy as np

DEFAULT_VOICE = "em_alex"
DEFAULT_LANG = "es-419"
MIN_SPEED = 0.5
MAX_SPEED = 2.0

# The bundled espeak-ng failed (rc=1, no traceback) with a ~190 char data path
# in the spike and worked with a 133 char one. The exact limit is unknown
# (likely a fixed 160 byte buffer, inferred), so stay well below it.
MAX_DATA_PATH_LEN = 100


def _default_factory(model_path, voices_path, espeak_config=None):
    from kokoro_onnx import Kokoro

    return Kokoro(model_path, voices_path, espeak_config=espeak_config)


class Engine:
    def __init__(self, model_path, voices_path, *, kokoro_factory=None):
        self._model_path = str(model_path)
        self._voices_path = str(voices_path)
        self._factory = kokoro_factory or _default_factory
        self._kokoro = None
        self._workdir = None

    def _get(self):
        if self._kokoro is None:
            self._workdir = configure_espeak()
            config = None
            if self._workdir is not None:
                from kokoro_onnx.config import EspeakConfig

                config = EspeakConfig(data_path=".")
            with _working_dir(self._workdir):
                self._kokoro = self._factory(
                    self._model_path, self._voices_path, espeak_config=config
                )
        return self._kokoro

    def available_voices(self):
        return list(self._get().get_voices())

    def synthesize(self, text, *, voice=DEFAULT_VOICE, speed=1.0, lang=DEFAULT_LANG):
        if not MIN_SPEED <= speed <= MAX_SPEED:
            raise ValueError(
                f"speed must be between {MIN_SPEED} and {MAX_SPEED}, got {speed!r}"
            )
        voices = self.available_voices()
        if voice not in voices:
            raise ValueError(
                f"unknown voice {voice!r}; available: {', '.join(voices)}"
            )
        kokoro = self._get()
        with _working_dir(self._workdir):
            samples, sr = kokoro.create(text, voice=voice, speed=speed, lang=lang)
        return np.asarray(samples, dtype=np.float32), int(sr)


def to_wav_bytes(samples, sample_rate):
    """Encode float samples in [-1, 1] as 16-bit PCM mono WAV, in memory."""
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def configure_espeak():
    """Prepare espeak-ng to cope with long data paths.

    Returns None when the bundled data path is short enough (nothing to do).
    Otherwise returns the directory that must be the process working directory
    while espeak-ng runs, and makes phonemizer hand espeak-ng the relative
    path "." instead of the long absolute one. Idempotent.

    Observed (spike): espeak-ng resolves "." lazily, on every phonemization,
    so the working directory must be that directory during Kokoro construction
    AND every create() call, not only at initialization. Callers use
    `_working_dir` around those calls to keep the window as short as possible
    (the working directory is process-wide).
    """
    import espeakng_loader

    data_path = Path(espeakng_loader.get_data_path())
    if len(str(data_path)) <= MAX_DATA_PATH_LEN:
        return None

    import phonemizer.backend.espeak.wrapper as wrapper

    if not getattr(wrapper.EspeakAPI, "_agent_voice_short_path", False):
        original = wrapper.EspeakAPI

        def short_path_api(library, _data_path):
            return original(library, ".")

        short_path_api._agent_voice_short_path = True
        wrapper.EspeakAPI = short_path_api
    return data_path.parent


@contextlib.contextmanager
def _working_dir(path):
    """Temporarily chdir into `path` (no-op for None); always restore."""
    if path is None:
        yield
        return
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
