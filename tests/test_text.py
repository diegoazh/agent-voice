import pytest

from agent_voice.text import DEFAULT_MAX_CHARS, MAX_SPOKEN_CODE_CHARS, chunks, clean

CODE = "Ver el código en el texto."
LINK = "ver el link en el texto"
TABLE = "Ver la tabla en el texto."


# 1. code
def test_fenced_block_becomes_placeholder_sentence():
    assert clean("Intro.\n```python\nprint(1)\n```\nFin.") == f"Intro. {CODE} Fin."


def test_tilde_fence_becomes_placeholder():
    assert clean("~~~\nx\n~~~") == CODE


def test_reply_only_code_block_is_not_empty():
    assert clean("```\nx = 1\n```") == CODE


def test_consecutive_code_blocks_collapse():
    assert clean("```\na\n```\n\n```\nb\n```") == CODE


# 1b. inline code: identifiers, links, real code (once per sentence)
INLINE = "ver el código en el texto"


def test_real_inline_code_becomes_inline_placeholder():
    assert clean("Usá `x = [1]` acá.") == f"Usá {INLINE} acá."


@pytest.mark.parametrize(
    ("span", "spoken"),
    [
        ("round()", "round"),
        ("round", "round"),
        ("Decimal", "Decimal"),
        ("ROUND_HALF_UP", "ROUND HALF UP"),
        ("kokoro-onnx", "kokoro onnx"),
        ("cli.py", "cli punto py"),
        ("os.path.join()", "os punto path punto join"),
        (".env", "punto env"),
        ("--flag", "flag"),
        ("v3.5", "v3 punto 5"),
        ("y/o", "y/o"),
    ],
)
def test_short_identifier_is_spoken_without_symbols(span, spoken):
    assert clean(f"Usá `{span}` acá.") == f"Usá {spoken} acá."


@pytest.mark.parametrize(
    "span",
    ["a + b", "foo(x)", "foo(a, b)", "x" * 60, "'hi'", "[1]", "--", "...", "foo()()"],
)
def test_non_identifier_spans_are_real_code(span):
    assert clean(f"Usá `{span}` acá.") == f"Usá {INLINE} acá."


def test_identifier_length_limit_is_forty_chars_then_spoken_code_to_48():
    assert clean(f"Usá `{'x' * 40}` acá.") == f"Usá {'x' * 40} acá."
    assert clean(f"Usá `{'x' * 48}` acá.") == f"Usá {'x' * 48} acá."
    assert clean(f"Usá `{'x' * 49}` acá.") == f"Usá {INLINE} acá."


@pytest.mark.parametrize(
    "span", ["src/payments/round.py:42", "~/.claude/settings.json", "https://x.dev", "main.py:7", "C:\\x\\y.txt"]
)
def test_unspeakable_path_or_url_in_backticks_becomes_link_placeholder(span):
    assert clean(f"Mirá `{span}` ya.") == f"Mirá {LINK} ya."


@pytest.mark.parametrize(
    ("span", "spoken"),
    [
        ("<23", "menor que 23"),
        ("==", "igual a"),
        ("keras ≥3.15", "keras mayor o igual que 3 punto 15"),
        ("boto3/botocore", "boto3 barra botocore"),
        ("pyarrow 24", "pyarrow 24"),
        ("s3fs/fsspec", "s3fs barra fsspec"),
        (">=2.0", "mayor o igual que 2 punto 0"),
        ("<=2.0", "menor o igual que 2 punto 0"),
        ("≤4", "menor o igual que 4"),
        ("!=1", "distinto de 1"),
        ("~=1.4", "compatible con 1 punto 4"),
        ("a=b", "a igual a b"),
        ("a > b", "a mayor que b"),
        ("shared/", "shared"),
        ("core/", "core"),
        ("/core", "core"),
        ("core/types.py", "core barra types punto py"),
        ("helpers/job_cli.py", "helpers barra job cli punto py"),
        ("scripts/debug_alerts.py", "scripts barra debug alerts punto py"),
        ("src/a/b.py", "src barra a barra b punto py"),
    ],
)
def test_short_spec_like_code_is_spoken_with_operator_words(span, spoken):
    assert clean(f"Usá `{span}` acá.") == f"Usá {spoken} acá."


def test_spoken_code_does_not_use_the_real_code_budget():
    assert clean("Con `<23` y `a + b` y `>=1`.") == f"Con menor que 23 y {INLINE} y mayor o igual que 1."


def test_spoken_code_length_limit_is_48_chars():
    assert clean(f"Usá `{'a ' * 24}` acá.") != f"Usá {INLINE} acá."
    assert clean(f"Usá `{'a ' * 25}b` acá.") == f"Usá {INLINE} acá."


@pytest.mark.parametrize("span", ["foo(x, y)", "a[0]", "a[0] = 1", "x = [1, 2]"])
def test_code_with_brackets_or_commas_stays_placeholder(span):
    assert clean(f"Mirá `{span}` acá.") == f"Mirá {INLINE} acá."


def test_identifier_and_path_regressions_unchanged():
    assert clean("`pyproject.toml`") == "pyproject punto toml"
    assert clean("`openmeteo-sdk`") == "openmeteo sdk"
    assert clean("`round()`") == "round"
    assert clean("Mirá `src/a/b.py` y `https://x.dev`.") == f"Mirá src barra a barra b punto py y {LINK}."
    assert clean("mover `shared/` ahora") == "mover shared ahora"
    assert clean("`https://example.com/x`") == LINK


def test_long_path_over_spoken_limit_is_not_read():
    path = "/".join(["segment"] * 8) + "/file.py"
    assert len(path) > MAX_SPOKEN_CODE_CHARS
    out = clean(f"Mirá `{path}` ya.")
    assert out == f"Mirá {LINK} ya."
    assert "barra" not in out


def test_second_real_code_in_sentence_is_dropped():
    assert clean("Usá `x = foo(a, b) + 1` y después `y = bar(2)`.") == f"Usá {INLINE} y después."


def test_code_placeholder_limit_resets_each_sentence():
    assert clean("Uno `a + b`. Dos `c + d`.") == f"Uno {INLINE}. Dos {INLINE}."


def test_identifiers_and_links_do_not_count_toward_code_limit():
    assert clean("Con `round()` y `a + b` y `src/a.py` y `c + d`.") == (
        f"Con round y {INLINE} y src barra a punto py y."
    )


def test_realistic_reply_sample():
    text = (
        "El problema estaba en `src/payments/round.py:42`: el redondeo usaba "
        "`round()` en vez de `Decimal`. Agregué tests en `tests/test_round.py`."
    )
    assert clean(text) == (
        f"El problema estaba en {LINK}: el redondeo usaba round en vez de Decimal. "
        "Agregué tests en tests barra test round punto py."
    )


# 2. URLs and links
def test_bare_url_becomes_link_placeholder():
    assert clean("Mirá https://example.com/a?b=1 ahora.") == f"Mirá {LINK} ahora."


def test_www_url_becomes_placeholder():
    assert clean("Entrá a www.example.com hoy") == f"Entrá a {LINK} hoy"


def test_markdown_link_speaks_label_only():
    assert clean("Leé [la guía](https://x.dev/guide) primero") == "Leé la guía primero"


def test_autolink_becomes_placeholder():
    assert clean("Ver <https://x.dev/a>.") == f"Ver {LINK}."


# 3. file paths
def test_absolute_path_becomes_placeholder():
    assert clean("Editá /Users/x/a.py ahora.") == f"Editá {LINK} ahora."


def test_home_path_becomes_placeholder():
    assert clean("Mirá ~/.claude/settings.json.") == f"Mirá {LINK}."


def test_relative_path_with_extension_becomes_placeholder():
    assert clean("Cambié src/agent_voice/cli.py hoy") == f"Cambié {LINK} hoy"


def test_dot_slash_and_dotdot_paths():
    assert clean("Corré ./x y ../y/z") == f"Corré {LINK} y {LINK}"


def test_windows_path():
    assert clean(r"Abrí C:\x\y.txt ya") == f"Abrí {LINK} ya"


def test_file_with_line_number():
    assert clean("Falla en main.py:123 siempre") == f"Falla en {LINK} siempre"


def test_path_with_line_suffix_is_one_placeholder():
    assert clean("En src/a/b.py:10:5 falla") == f"En {LINK} falla"


def test_y_slash_o_is_not_a_path():
    assert clean("Elegí rojo y/o azul") == "Elegí rojo y/o azul"


def test_fraction_and_date_are_not_paths():
    assert clean("Queda 1/2 y 1.5/2.0 el 12/10/2025") == "Queda 1/2 y 1.5/2.0 el 12/10/2025"


# 4. markdown stripping
def test_heading_becomes_sentence():
    assert clean("# Resumen\nTexto acá.") == "Resumen. Texto acá."


def test_heading_with_punctuation_keeps_it():
    assert clean("## ¿Qué sigue?\nNada.") == "¿Qué sigue? Nada."


def test_emphasis_markers_removed():
    assert clean("Esto es **fuerte**, _suave_, *itálica* y ~~tachado~~.") == (
        "Esto es fuerte, suave, itálica y tachado."
    )


def test_snake_case_underscores_survive():
    assert clean("La variable my_var_name existe.") == "La variable my_var_name existe."


def test_blockquote_marker_removed():
    assert clean("> Cita importante.\n> Otra línea.") == "Cita importante. Otra línea."


def test_list_items_become_sentences():
    text = "- uno\n* dos.\n+ tres\n1. cuatro\n2) cinco"
    assert clean(text) == "uno. dos. tres. cuatro. cinco."


def test_nested_list_indentation_ignored():
    assert clean("- a\n  - b") == "a. b."


def test_horizontal_rules_removed():
    assert clean("Antes.\n\n---\n\n***\n\nDespués.") == "Antes. Después."


def test_image_alt_spoken_or_nothing():
    assert clean("![logo](a.png) Hola") == "logo Hola"
    assert clean("![](a.png)") == ""


def test_html_tags_removed():
    assert clean("Hola <b>mundo</b><br/> fin") == "Hola mundo fin"


def test_plain_paragraph_lines_join():
    assert clean("línea uno\nlínea dos") == "línea uno línea dos"


# 5. tables
PLAIN = "| Nombre | Edad |\n|---|---|\n| Ana | 30 |\n| Luis | 25 |"


def test_plain_table_read_row_by_row():
    assert clean(PLAIN) == "Nombre: Ana, Edad: 30. Nombre: Luis, Edad: 25."


def test_table_alignment_row_never_spoken():
    out = clean("| a | b |\n|:--|--:|\n| x | y |")
    assert out == "a: x, b: y."
    assert "-" not in out


def test_table_without_outer_pipes():
    assert clean("a | b\n--- | ---\nx | y") == "a: x, b: y."


def test_table_between_paragraphs():
    assert clean(f"Antes:\n\n{PLAIN}\n\nDespués.") == (
        "Antes: Nombre: Ana, Edad: 30. Nombre: Luis, Edad: 25. Después."
    )


def test_table_with_url_cell_becomes_placeholder():
    assert clean("| a | b |\n|---|---|\n| x | https://x.dev |") == TABLE


def test_table_with_code_cell_becomes_placeholder():
    assert clean("| a | b |\n|---|---|\n| x | `y` |") == TABLE


def test_table_with_path_cell_becomes_placeholder():
    assert clean("| a | b |\n|---|---|\n| x | src/a/b.py |") == TABLE


def test_five_column_table_becomes_placeholder():
    header = "| a | b | c | d | e |\n|---|---|---|---|---|\n"
    assert clean(header + "| 1 | 2 | 3 | 4 | 5 |") == TABLE


def test_four_column_table_is_plain():
    header = "| a | b | c | d |\n|---|---|---|---|\n"
    assert clean(header + "| 1 | 2 | 3 | 4 |") == "a: 1, b: 2, c: 3, d: 4."


def test_nine_body_rows_becomes_placeholder():
    rows = "\n".join(f"| {i} | x |" for i in range(9))
    assert clean("| n | v |\n|---|---|\n" + rows) == TABLE


def test_eight_body_rows_is_plain():
    rows = "\n".join(f"| {i} | x |" for i in range(8))
    assert clean("| n | v |\n|---|---|\n" + rows).count("n:") == 8


def test_header_only_table_becomes_placeholder():
    assert clean("| a | b |\n|---|---|") == TABLE


def test_empty_cells_are_skipped_and_empty_row_dropped():
    assert clean("| a | b |\n|---|---|\n| x |  |\n|  |  |") == "a: x."


def test_row_with_extra_cells_keeps_them():
    assert clean("| a |\n|---|\n| x | y |") == "a: x, y."


def test_consecutive_tables_collapse_placeholder():
    t = "| a | b |\n|---|---|\n| `x` | y |"
    assert clean(t + "\n\n" + t) == TABLE


def test_pipe_in_prose_is_not_a_table():
    assert clean("Usá a | b para elegir") == "Usá a | b para elegir"


# 6. emoji, hashes, whitespace; 7. empty result
def test_emojis_removed():
    assert clean("Listo 🎉 todo ✅ bien 👍🏽 ok") == "Listo todo bien ok"


def test_decorative_symbols_removed():
    assert clean("Paso uno → paso dos • fin ★") == "Paso uno paso dos fin"


def test_accents_and_inverted_marks_survive():
    assert clean("¿Qué pasó? ¡Ñandú!") == "¿Qué pasó? ¡Ñandú!"


def test_commit_sha_removed():
    assert clean("Commit b5ffe5e arregla el bug") == "Commit arregla el bug"


def test_long_hash_removed():
    sha = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
    assert clean(f"Hash {sha}, listo.") == "Hash, listo."


def test_plain_numbers_and_words_are_not_hashes():
    assert clean("Llamá al 1234567 por facade deadbeef") == "Llamá al 1234567 por facade deadbeef"


def test_whitespace_collapsed():
    assert clean("  uno \t\n\n\n  dos   ") == "uno dos"


def test_nothing_speakable_returns_empty():
    assert clean("") == ""
    assert clean("   \n\t") == ""
    assert clean("🎉 ✅") == ""
    assert clean("---\n***") == ""
    assert clean("- 🎉") == ""


# 8. chunks
def test_chunks_split_at_sentence_boundaries():
    text = "Primera oración bastante larga. Segunda oración también larga! ¿Y la tercera pregunta?"
    assert chunks(text) == [
        "Primera oración bastante larga.",
        "Segunda oración también larga!",
        "¿Y la tercera pregunta?",
    ]


def test_chunks_cleans_markdown_first():
    assert chunks("# Título largo del documento\nTexto del cuerpo del documento.") == [
        "Título largo del documento.",
        "Texto del cuerpo del documento.",
    ]


def test_chunks_keep_question_intact():
    out = chunks("Encontré dos problemas serios. ¿Querés que los arregle ahora?")
    assert out == ["Encontré dos problemas serios.", "¿Querés que los arregle ahora?"]


def test_chunks_keep_inverted_span_with_inner_period_intact():
    out = chunks("Dije algo importante antes. ¡Atención. Esto es urgente! Seguimos adelante.")
    assert "¡Atención. Esto es urgente!" in out


def test_chunks_do_not_split_decimals():
    assert chunks("La versión 3.5 es estable y la usamos.") == ["La versión 3.5 es estable y la usamos."]


def test_chunks_merge_short_fragments_with_next():
    assert chunks("Sí. Claro. Entonces hacemos el cambio completo.") == [
        "Sí. Claro. Entonces hacemos el cambio completo."
    ]


def test_chunks_merge_trailing_short_fragment_with_previous():
    assert chunks("Esta es una oración suficientemente larga. Ok.") == [
        "Esta es una oración suficientemente larga. Ok."
    ]


def test_chunks_hard_split_long_sentence_at_commas():
    sentence = ", ".join(["alpha beta gamma"] * 10) + "."
    out = chunks(sentence, max_chars=50)
    assert all(len(c) <= 50 for c in out)
    assert all(c for c in out)
    assert out[0].endswith(",")
    assert " ".join(out) == sentence


def test_chunks_hard_split_at_spaces_without_commas():
    sentence = " ".join(["palabra"] * 30) + "."
    out = chunks(sentence, max_chars=40)
    assert all(len(c) <= 40 for c in out)
    assert " ".join(out) == sentence


def test_chunks_hard_split_unbroken_token():
    out = chunks("x" * 95, max_chars=40)
    assert [len(c) for c in out] == [40, 40, 15]


def test_chunks_default_limit_is_respected():
    out = chunks("palabra " * 200)
    assert len(out) > 1
    assert all(0 < len(c) <= DEFAULT_MAX_CHARS for c in out)


def test_chunks_empty_when_nothing_speakable():
    assert chunks("") == []
    assert chunks("```\ncode\n```") == [CODE]


def test_chunks_reject_non_positive_limit():
    with pytest.raises(ValueError):
        chunks("hola", max_chars=0)


def test_chunks_unterminated_inverted_mark_still_bounded():
    out = chunks("¿" + "palabra " * 100, max_chars=60)
    assert all(len(c) <= 60 for c in out)


# ReDoS / unbounded input (security audit G2, G3)
import time

from agent_voice import text as _text

_SLOW = 1.0


def _elapsed(fn, *args):
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


@pytest.mark.parametrize(
    "line",
    [
        "# a" + " " * 6000 + "x",
        "#" + " " * 6000,
        "*a " * 7000,
        "_a " * 7000,
        "**a " * 5000,
        "~~a " * 5000,
        "![" * 10000,
        "[" * 20000,
        "<a" * 10000,
    ],
    ids=["heading-spaces", "heading-only-spaces", "star", "under", "bold", "tilde", "image", "link", "html"],
)
def test_adversarial_line_is_processed_in_linear_time(line):
    assert _elapsed(_text._blocks, line, _text._VOCAB["es"]) < _SLOW


def test_sentence_split_of_closer_runs_is_linear():
    assert _elapsed(_text._sentences, "." * 20000 + "x") < _SLOW
    assert _elapsed(_text._sentences, ".\"" * 10000 + "x") < _SLOW


def test_hard_split_of_a_huge_unbroken_sentence_is_linear():
    assert _elapsed(_text._hard_split, "a" * 200000, 300) < _SLOW


def test_heading_trailing_hashes_and_spaces_are_still_stripped():
    assert clean("## Título ##  ") == "Título."
    assert clean("#   Título   ") == "Título."


def test_emphasis_still_wraps_ordinary_spans():
    assert clean("Esto es *muy* **claro** y ~~no~~ _así_.") == "Esto es muy claro y no así."


def test_overlong_line_is_truncated_before_cleaning():
    out = clean("a" * 5000 + " cola.\nSegunda línea.")
    assert "cola" not in out
    assert out.startswith("a" * _text.MAX_LINE_CHARS)
    assert "Segunda línea." in out


def test_overlong_reply_is_cut_with_a_short_spoken_notice():
    reply = "\n".join(["Esta es una frase de prueba."] * 5000)
    out = clean(reply)
    assert out.endswith(_text.TRUNCATED_NOTICE)
    assert len(out) < _text.MAX_REPLY_CHARS + 200


def test_reply_within_the_cap_has_no_notice():
    assert _text.TRUNCATED_NOTICE not in clean("Hola mundo, esto es corto.")


@pytest.mark.parametrize("raw", ["*a " * 40000, "# a" + " " * 100000 + "x", "![" * 60000, "." * 100000 + "x"])
def test_whole_pipeline_on_hostile_100k_input_is_fast(raw):
    assert _elapsed(chunks, raw) < _SLOW


# bold spans containing a commit SHA
def test_bold_span_starting_with_sha_leaves_no_asterisks():
    out = clean("**1a2b3c4 chore: tidy imports** quedó listo")
    assert "*" not in out
    assert "chore: tidy imports" in out
    assert "quedó listo" in out


def test_bold_span_with_only_a_sha_leaves_no_asterisks():
    out = clean("**9f8e7d6 refactor(parser):**")
    assert "*" not in out
    assert "refactor(parser)" in out


def test_ordinary_bold_still_unwraps_after_emphasis_reorder():
    out = clean("texto **negrita** fin")
    assert "*" not in out
    assert "negrita" in out


def test_bold_span_with_a_path_leaves_no_asterisks():
    out = clean("mirá **/usr/local/bin/tool** ahora")
    assert "*" not in out
    assert _text.LINK_INLINE in out


def test_bold_span_with_a_url_leaves_no_asterisks():
    out = clean("**https://example.com** fin")
    assert "*" not in out
    assert _text.LINK_INLINE in out


def test_bold_span_with_an_autolink_leaves_no_asterisks():
    out = clean("**<https://example.com>** fin")
    assert "*" not in out
    assert _text.LINK_INLINE in out


def _over_long_span() -> str:
    return "palabra " * (_text._SPAN // 8 + 20)


def test_strikethrough_longer_than_span_leaves_no_tildes():
    long = _over_long_span()
    assert len(long) > _text._SPAN
    out = clean(f"~~{long}~~")
    assert "~~" not in out
    assert "palabra" in out


def test_bold_longer_than_span_leaves_no_asterisks():
    long = _over_long_span()
    assert len(long) > _text._SPAN
    out = clean(f"**{long}**")
    assert "*" not in out
    assert "palabra" in out


def test_short_strikethrough_and_bold_still_unwrap():
    out = clean("lo ~~viejo~~ y **negrita**")
    assert "viejo" in out and "negrita" in out
    assert "~" not in out and "*" not in out


def test_home_relative_path_becomes_link_placeholder():
    assert _text.LINK_INLINE in clean("ver ~/configuración ahora")


def test_single_tilde_not_in_path_survives_marker_cleanup():
    approx = clean("aprox ~5 dolares")
    assert "~" in approx
    assert "aprox" in approx and "5" in approx and "dolares" in approx
    spaced = clean("esto ~ eso")
    assert "~" in spaced
    assert "esto" in spaced and "eso" in spaced


def test_emoji_inside_bold_leaves_no_asterisks():
    out = clean("**🎯 Resumen del día**")
    assert "*" not in out
    assert "Resumen del día" in out


def test_emoji_before_bold_leaves_no_asterisks():
    out = clean("🎯 **Resumen del día**")
    assert "*" not in out
    assert "Resumen del día" in out


# ---------------------------------------------------------------------------
# Language-aware normalization: English chunks must NOT receive Spanish words.
# Spanish (the default) must stay byte-for-byte identical.
# ---------------------------------------------------------------------------

import agent_voice.text as _text  # noqa: E402

EN_CODE = "See the code in the text."
EN_INLINE = "see the code in the text"
EN_LINK = "see the link in the text"
EN_TABLE = "See the table in the text."


def test_lang_defaults_to_spanish():
    sample = "Instalá `>=3.15` y mirá `src/a/b.py`."
    assert clean(sample) == clean(sample, lang="es")


def test_english_fenced_block_uses_english_placeholder():
    assert clean("Intro.\n```python\nprint(1)\n```\nEnd.", lang="en") == f"Intro. {EN_CODE} End."


def test_english_real_inline_code_uses_english_placeholder():
    assert clean("Use `x = [1]` here.", lang="en") == f"Use {EN_INLINE} here."


def test_english_identifier_dot_becomes_english_word():
    assert clean("`cli.py`", lang="en") == "cli dot py"
    assert clean("`os.path.join()`", lang="en") == "os dot path dot join"


def test_english_operator_words_are_english():
    assert clean("keras `>=3.15`", lang="en") == "keras greater than or equal to 3 dot 15"
    assert clean("`<=2.0`", lang="en") == "less than or equal to 2 dot 0"
    assert clean("`~=1.4`", lang="en") == "compatible with 1 dot 4"
    assert clean("`a == b`", lang="en") == "a equals b"
    assert clean("`a != b`", lang="en") == "a not equal to b"


def test_english_path_slash_becomes_english_word():
    assert clean("`core/types.py`", lang="en") == "core slash types dot py"


def test_english_link_placeholder_is_english():
    out = clean("See `https://x.dev` now.", lang="en")
    assert EN_LINK in out
    assert "link en el texto" not in out


def test_english_table_placeholder_is_english():
    table = "| a | b |\n| - | - |\n| `x=1` | y |"
    assert clean(table, lang="en") == EN_TABLE


def test_english_truncation_notice_is_english():
    out = clean("word " * 20000, lang="en")
    assert out.endswith("The rest of the reply is too long.")
    assert "El resto de la respuesta" not in out


def test_english_chunks_split_in_english():
    out = chunks("Use `x = [1]` here. And `cli.py` too.", lang="en")
    joined = " ".join(out)
    assert EN_INLINE in joined
    assert "cli dot py" in joined
    assert "código" not in joined and "punto" not in joined


# ---------------------------------------------------------------------------
# split_blocks: raw blocks for per-block language detection.
# ---------------------------------------------------------------------------

def test_split_blocks_separates_on_blank_lines_and_ignores_leading_blanks():
    assert _text.split_blocks("\n\n\nHola.\n\nAdios.\n") == ["Hola.", "Adios."]


def test_split_blocks_keeps_a_fenced_code_block_whole_with_inner_blank_lines():
    assert _text.split_blocks("Intro.\n\n```py\na\n\nb\n```\n\nFin.") == [
        "Intro.",
        "```py\na\n\nb\n```",
        "Fin.",
    ]


def test_split_blocks_keeps_an_unterminated_fence_as_one_block():
    assert _text.split_blocks("```\nx\ny") == ["```\nx\ny"]


def test_split_blocks_on_only_whitespace_is_empty():
    assert _text.split_blocks("\n   \n\n") == []


def test_without_fenced_code_keeps_only_prose_lines():
    block = "Intro line\n```py\nthe code\n~~~\nstill code\n```\nOutro line"
    assert _text.without_fenced_code(block) == "Intro line\nOutro line"


def test_without_fenced_code_on_code_only_block_is_empty():
    assert _text.without_fenced_code("~~~\nreturn this\n~~~").strip() == ""
