import pytest

from agent_voice.lang import detect_lang


@pytest.mark.parametrize(
    "text",
    [
        "The function returns the value and prints it to the console.",
        "This is a short note about the new feature and how to use it.",
        "We should add a test for that case before we merge the change.",
    ],
)
def test_clearly_english_text_is_detected_as_english(text):
    assert detect_lang(text) == "en"


@pytest.mark.parametrize(
    "text",
    [
        "La función devuelve el valor y lo imprime en la consola.",
        "Esta es una nota corta sobre la nueva función y cómo usarla.",
        "Deberíamos agregar un test para ese caso antes de mezclar el cambio.",
    ],
)
def test_clearly_spanish_text_is_detected_as_spanish(text):
    assert detect_lang(text) == "es"


def test_tied_signal_returns_previous():
    # "the" (en) and "que" (es) balance out: the winner is whatever came before.
    assert detect_lang("the que", previous="en") == "en"
    assert detect_lang("the que", previous="es") == "es"


def test_no_signal_returns_previous():
    assert detect_lang("12345 >>> ---", previous="en") == "en"
    assert detect_lang("", previous="es") == "es"


def test_no_signal_without_previous_defaults_to_spanish():
    assert detect_lang("12345 >>> ---") == "es"
    assert detect_lang("", previous=None) == "es"


def test_previous_is_only_a_tiebreaker_not_an_override():
    # A clearly English chunk wins even when the previous chunk was Spanish.
    assert detect_lang("the value is on the list", previous="es") == "en"
    # ...and the reverse.
    assert detect_lang("el valor es de la lista", previous="en") == "es"
