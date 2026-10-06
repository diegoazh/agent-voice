"""Heuristic Spanish-vs-English language detection for a chunk of text.

Pure module: no I/O, no third-party dependency. It compares how many common
Spanish function words a chunk contains against how many common English ones,
and returns the winner. Function words (articles, prepositions, conjunctions,
pronouns) are chosen because they are extremely frequent and rarely overlap
between the two languages, so even a short sentence usually carries a clear
signal. Content words (nouns, verbs) are deliberately ignored: many are shared
or look alike across the two languages and would only add noise.

This is intentionally simple. Broader multi-language support would adopt a real
detection library instead (see ROADMAP.md).
"""

import re

# Short language codes this detector can return.
SPANISH = "es"
ENGLISH = "en"

# Word-ish tokens: letters (incl. Spanish accents and ñ) plus apostrophes
# ("don't", "it's"). Everything else (digits, punctuation, symbols) is a gap.
_WORD = re.compile(r"[a-záéíóúüñ']+")

# Common Spanish function words. No accents are needed here because they are
# matched against the already-lowercased token text, accents included.
_SPANISH_WORDS = frozenset(
    {
        "el", "la", "los", "las", "un", "una", "unos", "unas",
        "que", "de", "del", "en", "con", "por", "para", "sin", "sobre",
        "es", "son", "está", "están", "ser", "y", "o", "u", "no", "sí",
        "se", "su", "sus", "lo", "le", "les", "al", "como", "pero",
        "más", "este", "esta", "esto", "estos", "estas", "ese", "esa",
        "ya", "muy", "también", "cuando", "donde", "porque", "cómo",
        "nos", "te", "mi", "tu", "si", "hay", "fue", "ha", "han",
    }
)

# Common English function words. "a" and "an" are included; "a" never collides
# with a Spanish function word, so it is a safe English signal.
_ENGLISH_WORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "nor", "so", "yet",
        "is", "are", "was", "were", "be", "been", "being", "am",
        "of", "to", "in", "on", "at", "by", "for", "with", "from",
        "that", "this", "these", "those", "it", "its", "as", "if",
        "you", "your", "we", "our", "they", "their", "he", "she",
        "not", "no", "do", "does", "did", "have", "has", "had",
        "will", "would", "should", "can", "could", "there", "about",
    }
)


def detect_lang(text: str, previous: str | None = None) -> str:
    """Return "es" or "en" for `text`, falling back to `previous` on a tie.

    Counts how many Spanish and English function words appear in `text` and
    returns the language with more hits. On a tie (including no hits at all)
    the result is `previous`, so a low-signal chunk keeps the language of the
    chunk before it; when `previous` is None the default is Spanish ("es").
    """
    spanish_hits = 0
    english_hits = 0
    for token in _WORD.findall(text.lower()):
        if token in _SPANISH_WORDS:
            spanish_hits += 1
        if token in _ENGLISH_WORDS:
            english_hits += 1
    if spanish_hits > english_hits:
        return SPANISH
    if english_hits > spanish_hits:
        return ENGLISH
    # Tie or no signal: stay with the previous chunk's language, else Spanish.
    return previous if previous is not None else SPANISH
