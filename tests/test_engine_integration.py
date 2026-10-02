import pytest

from agent_voice import models
from agent_voice.engine import Engine

pytestmark = pytest.mark.integration


def test_real_fp32_synthesis_is_audible_length():
    try:
        model, voices = models.resolve("fp32")
    except models.ModelsMissingError as exc:
        pytest.skip(str(exc))
    samples, sr = Engine(model, voices).synthesize("Listo, ya está.")
    assert sr == 24000
    assert 0.5 <= len(samples) / sr <= 4
