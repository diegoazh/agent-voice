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


# "a" is a very common Spanish preposition and "he" a common Spanish auxiliary
# ("he hecho"), so neither may count as an English signal.
@pytest.mark.parametrize(
    "text",
    [
        "Voy a ir a la casa a ver a mi madre.",
        "A ver, no sé si a Juan le va a gustar.",
        "Ya he terminado.",
    ],
)
@pytest.mark.parametrize("previous", ["es", "en"])
def test_spanish_with_preposition_a_and_auxiliary_he_is_spanish(text, previous):
    assert detect_lang(text, previous) == "es"


@pytest.mark.parametrize("previous", ["es", None])
def test_short_spanish_with_only_preposition_a_is_not_english(previous):
    # No Spanish function word besides "a": it must not tip the chunk to English.
    assert detect_lang("Fui a casa.", previous) == "es"


@pytest.mark.parametrize(
    "text",
    [
        "The agent returned the results. I will now check the tests and report back.",
        "I fixed it.",
        "Looks good to me.",
    ],
)
@pytest.mark.parametrize("previous", ["es", "en"])
def test_short_english_replies_stay_english(text, previous):
    assert detect_lang(text, previous) == "en"


@pytest.mark.parametrize("previous", ["es", "en"])
def test_inline_code_identifiers_do_not_count_as_english(previous):
    # `is_owned_by_the_user` holds "is", "by" and "the": inline code is not prose,
    # so only the Spanish around it may decide.
    assert detect_lang("Llamé a `is_owned_by_the_user` y devolvió el usuario.", previous) == "es"


def test_text_that_is_only_inline_code_has_no_signal():
    assert detect_lang("`the_value_is_in_the_list`", "es") == "es"
    assert detect_lang("`el_valor_de_la_lista`", "en") == "en"
