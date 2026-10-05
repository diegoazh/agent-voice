"""Turn Markdown agent replies into speakable prose and sentence chunks.

Pure module: no I/O, no network, no logging. Text never leaves memory.
"""

import re
import unicodedata

# Kokoro errors past ~510 phonemes; Spanish averages roughly 1 phoneme per
# character, so ~300 characters leaves a wide safety margin.
DEFAULT_MAX_CHARS = 300
# Fragments shorter than this are merged with a neighbour ("Sí.", "Sr.").
MIN_CHUNK_CHARS = 15
# Hard input caps, applied before any cleaning (keeps every later step cheap).
MAX_LINE_CHARS = 2000
MAX_REPLY_CHARS = 50000
TRUNCATED_NOTICE = "El resto de la respuesta es demasiado largo."
# Bound on how far an inline Markdown construct may reach: keeps the regexes linear.
_SPAN = 300
CODE_SENTENCE = "Ver el código en el texto."
CODE_INLINE = "ver el código en el texto"
TABLE_SENTENCE = "Ver la tabla en el texto."
LINK_INLINE = "ver el link en el texto"

_SENTENCE_END = ".!?…"
_TERMINAL = _SENTENCE_END + ":;"
_CLOSERS = "\"'”’»)]}"
_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
MAX_IDENTIFIER_CHARS = 40
_IDENTIFIER = re.compile(r"[\w.\-]+(?:\(\))?")
_SLASH_PAIR = re.compile(r"[^\W\d_]+/[^\W\d_]+")
_SENTENCE_BREAK = re.compile(rf"[{re.escape(_SENTENCE_END)}][{re.escape(_CLOSERS)}]*\s")
_IMAGE = re.compile(rf"!\[([^\]\n]{{0,{_SPAN}}})\]\([^)\n]{{0,{_SPAN}}}\)")
_HTML_TAG = re.compile(rf"</?[A-Za-z][^>\n]{{0,{_SPAN}}}>")
_EMPHASIS = (
    re.compile(rf"(?<!\w)__(?=\S)(.{{1,{_SPAN}}}?)(?<=\S)__(?!\w)"),
    re.compile(rf"\*\*(?=\S)(.{{1,{_SPAN}}}?)(?<=\S)\*\*"),
    re.compile(rf"(?<![\w*])\*(?=[^\s*])(.{{1,{_SPAN}}}?)(?<=[^\s*])\*(?![\w*])"),
    re.compile(rf"(?<!\w)_(?=[^\s_])(.{{1,{_SPAN}}}?)(?<=[^\s_])_(?!\w)"),
    re.compile(rf"~~(?=\S)(.{{1,{_SPAN}}}?)(?<=\S)~~"),
)
_LEFTOVER_MARKERS = re.compile(r"~~|\*\*")
_QUOTE_PREFIX = re.compile(r"^\s*(?:>\s?)+")
_HRULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
MAX_TABLE_COLUMNS = 4
MAX_TABLE_ROWS = 8
# Hex strings of 7+ chars that mix digits and a-f letters (commit SHAs, digests).
_HASH = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}\b", re.IGNORECASE)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?…])")
_DECORATIVE = frozenset("•·●○■□▪▫→←↑↓↔⇒⇐")
_MD_LINK = re.compile(rf"\[([^\]\n]{{0,{_SPAN}}})\]\([^)\n]{{0,{_SPAN}}}\)")
_AUTOLINK = re.compile(r"<(?:https?://|www\.)[^>\s]*>")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\]\)]*[^\s<>\]\).,;:!?]")
_SEG = r"[\w.\-@]*\w"
_LINE_SUFFIX = r"(?::\d+){0,2}"
_PATH = re.compile(
    r"(?<![\w/.~\\:-])(?:"
    # ~/x, ./x, ../x
    rf"(?:~|\.{{1,2}})/{_SEG}(?:/{_SEG})*"
    # /a/b (2+ segments) or /a.ext
    rf"|/{_SEG}(?:/{_SEG})+"
    r"|/[\w\-@]+\.[A-Za-z]\w*"
    # a/b/c.ext: 2+ segments, last one a name with a letter-led extension
    rf"|(?:{_SEG}/)+[A-Za-z_][\w\-@]*\.[A-Za-z]\w{{0,5}}"
    # C:\x\y
    r"|[A-Za-z]:\\[^\s]*\w"
    # main.py:123 (bare file only when it carries a line number)
    r"|[A-Za-z_][\w\-]*\.[A-Za-z]\w{0,5}(?=:\d)"
    rf"){_LINE_SUFFIX}"
)


def _strip_symbols(text: str) -> str:
    """Drop emojis and decorative symbols (categories So/Sk/Cf, variation selectors)."""
    return "".join(
        ch
        for ch in text
        if ch == "`"  # Sk, but needed to detect code
        or (
            ch not in _DECORATIVE
            and unicodedata.category(ch) not in {"So", "Sk", "Cf"}
            and not "\ufe00" <= ch <= "\ufe0f"
        )
    )


def _replace_fences(text: str) -> str:
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                out.append(CODE_SENTENCE)
            else:
                out.append(line)
        elif match and match.group(1) == fence:
            fence = None
    return "\n".join(out)


def _collapse_repeats(text: str) -> str:
    for placeholder in (CODE_SENTENCE, TABLE_SENTENCE, LINK_INLINE, CODE_INLINE):
        pattern = re.compile(rf"(?:{re.escape(placeholder)}\s*)+(?={re.escape(placeholder)})")
        text = pattern.sub("", text)
    return text


def _speak_identifier(token: str) -> str:
    """Speak an identifier: drop "()", dots become " punto ", "_" and "-" become spaces."""
    token = token.removesuffix("()")
    token = token.replace(".", " punto ").replace("_", " ").replace("-", " ")
    return " ".join(token.split())


def _inline_code(line: str) -> str:
    """Replace each inline code span by what is worth saying aloud.

    - Identifier (letters/digits/"_"/"."/"-", optional trailing "()", at most
      MAX_IDENTIFIER_CHARS): spoken without symbols, e.g. `round()` -> "round",
      `ROUND_HALF_UP` -> "ROUND HALF UP", `kokoro-onnx` -> "kokoro onnx",
      `cli.py` -> "cli punto py". A bare two-word `y/o` is read as written.
    - URL or path (per the prose heuristics): the link placeholder.
    - Anything else is real code: the code placeholder, at most once per
      sentence of the line; further real-code spans in it are dropped.
    """
    state = {"end": 0, "has_code": False}

    def replace(match: re.Match[str]) -> str:
        if _SENTENCE_BREAK.search(line, state["end"], match.start()):
            state["has_code"] = False
        state["end"] = match.end()
        body = match.group(0)[1:-1]
        if _URL.search(body):
            return LINK_INLINE
        if len(body) <= MAX_IDENTIFIER_CHARS:
            if _SLASH_PAIR.fullmatch(body):
                return body
            if _IDENTIFIER.fullmatch(body) and any(ch.isalnum() for ch in body):
                return _speak_identifier(body)
        if _PATH.search(body):
            return LINK_INLINE
        if state["has_code"]:
            return ""
        state["has_code"] = True
        return CODE_INLINE

    return _INLINE_CODE.sub(replace, line)


def _inline(line: str) -> str:
    """Clean inline Markdown, links, code and paths of one line."""
    line = _inline_code(line)
    # Unwrap emphasis first: removing a SHA/path/URL inside a span would leave
    # whitespace next to the markers and stop them from matching.
    for pattern in _EMPHASIS:
        line = pattern.sub(r"\1", line)
    # Spans longer than _SPAN are not unwrapped above, leaving their delimiters in
    # place; drop leftover bold/strikethrough markers so they are not spoken
    # (e.g. a long "~~...~~" would otherwise be read as "tilde tilde").
    line = _LEFTOVER_MARKERS.sub("", line)
    line = _IMAGE.sub(r"\1", line)
    line = _MD_LINK.sub(r"\1", line)
    line = _AUTOLINK.sub(LINK_INLINE, line)
    line = _URL.sub(LINK_INLINE, line)
    line = _HTML_TAG.sub("", line)
    line = _PATH.sub(LINK_INLINE, line)
    line = _HASH.sub("", line)
    return line.strip()


def _as_sentence(line: str) -> str:
    """Append a period unless the line already ends with sentence punctuation."""
    stripped = line.rstrip(_CLOSERS)
    if stripped and stripped[-1] not in _TERMINAL:
        return line + "."
    return line


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line)]


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        "|" in lines[i]
        and i + 1 < len(lines)
        and "|" in lines[i + 1]
        and "-" in lines[i + 1]
        and _TABLE_SEPARATOR.match(lines[i + 1]) is not None
    )


def _is_plain_cell(cell: str) -> bool:
    return not (
        "`" in cell or _URL.search(cell) or _AUTOLINK.search(cell) or _PATH.search(cell)
    )


def _read_table(header: list[str], body: list[list[str]]) -> list[str]:
    """Read a table as one sentence per body row, or the table placeholder.

    A table is plain when every cell is plain text (no code, URL or path),
    it has at most MAX_TABLE_COLUMNS columns and MAX_TABLE_ROWS body rows,
    and at least one body row. A row reads "Header1: cell1, Header2: cell2.";
    empty cells are skipped, extra cells without a header are read bare.
    """
    columns = max([len(header)] + [len(row) for row in body])
    cells = [*header, *(c for row in body for c in row)]
    if (
        not body
        or columns > MAX_TABLE_COLUMNS
        or len(body) > MAX_TABLE_ROWS
        or not all(_is_plain_cell(c) for c in cells)
    ):
        return [TABLE_SENTENCE]
    sentences: list[str] = []
    for row in body:
        parts = []
        for index, cell in enumerate(row):
            value = _inline(cell)
            if not value:
                continue
            label = _inline(header[index]) if index < len(header) else ""
            parts.append(f"{label}: {value}" if label else value)
        if parts:
            sentences.append(_as_sentence(", ".join(parts)))
    return sentences


def _blocks(text: str) -> list[str]:
    """Convert each Markdown line to a speakable line; drop empty ones."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _is_table_start(lines, i):
            header = _split_row(lines[i])
            i += 2
            body: list[list[str]] = []
            while i < len(lines) and "|" in lines[i]:
                body.append(_split_row(lines[i]))
                i += 1
            out.extend(_read_table(header, body))
            continue
        line = _QUOTE_PREFIX.sub("", lines[i])
        i += 1
        if _HRULE.match(line):
            continue
        heading = _HEADING.match(line)
        item = _LIST_ITEM.match(line)
        if heading:
            # Trailing "#" and spaces are stripped here, not in the regex (linear time).
            line = _as_sentence(_inline(heading.group(1).rstrip().rstrip("#").rstrip()))
        elif item:
            line = _as_sentence(_inline(item.group(1)))
        else:
            line = _inline(line)
        if any(ch.isalnum() for ch in line):
            out.append(line)
    return out


def _cap(text: str) -> tuple[str, bool]:
    """Apply the hard input caps: each line to MAX_LINE_CHARS, the reply to MAX_REPLY_CHARS.

    Returns (capped text, whether the reply was cut short).
    """
    cut = len(text) > MAX_REPLY_CHARS
    lines = text[:MAX_REPLY_CHARS].split("\n")
    return "\n".join(line[:MAX_LINE_CHARS] for line in lines), cut


def clean(text: str) -> str:
    """Return speakable prose for a Markdown reply, or "" if nothing remains.

    Input is capped first (MAX_LINE_CHARS per line, MAX_REPLY_CHARS overall); a cut
    reply ends with TRUNCATED_NOTICE.
    """
    text, cut = _cap(text.replace("\r\n", "\n"))
    text = _replace_fences(text)
    text = " ".join(_blocks(_strip_symbols(text)))
    text = re.sub(r"\s+", " ", text).strip()
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _collapse_repeats(text)
    return f"{text} {TRUNCATED_NOTICE}".strip() if cut else text


def _sentences(text: str) -> list[str]:
    """Split at . ! ? … keeping ¿...? and ¡...! spans whole."""
    out: list[str] = []
    start = 0
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "¿¡":
            depth += 1
        elif ch in "?!" and depth:
            depth -= 1
        if ch in _SENTENCE_END and not depth:
            j = i + 1
            while j < n and text[j] in _SENTENCE_END + _CLOSERS:
                j += 1
            if j == n or text[j].isspace():
                out.append(text[start:j].strip())
                start = i = j
                continue
            # No boundary after this run, so none inside it either (same end `j`); only
            # the depth bookkeeping of its "?"/"!" characters is left to replay.
            for ch in text[i + 1 : j]:
                if ch in "?!" and depth:
                    depth -= 1
            i = j
            continue
        i += 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def _hard_split(sentence: str, max_chars: int) -> list[str]:
    """Split an over-long sentence at the last comma, else space, within the limit."""
    parts: list[str] = []
    pos = 0
    n = len(sentence)
    while n - pos > max_chars:
        window = sentence[pos : pos + max_chars + 1]
        cut = max(window.rfind(","), window.rfind(";"))
        if cut > 0:
            cut += 1
        else:
            cut = window.rfind(" ")
            if cut <= 0:
                cut = max_chars
        parts.append(window[:cut].strip())
        pos += cut
        while pos < n and sentence[pos].isspace():
            pos += 1
    rest = sentence[pos:].strip()
    if rest:
        parts.append(rest)
    return parts


def chunks(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Clean `text` and split it into sentence chunks of at most `max_chars`.

    Sentences end at . ! ? … (never inside a ¿...? or ¡...! span). Fragments
    shorter than MIN_CHUNK_CHARS are merged with the next one (or the previous
    one at the end) while the result fits. Sentences longer than `max_chars`
    are cut at the last comma/semicolon, else the last space, else hard-cut.
    Never returns empty chunks; returns [] when nothing is speakable.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    pieces = [
        part
        for sentence in _sentences(clean(text))
        for part in _hard_split(sentence, max_chars)
    ]
    merged: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) < MIN_CHUNK_CHARS and len(current) + 1 + len(piece) <= max_chars:
            current = f"{current} {piece}"
            continue
        if current:
            merged.append(current)
        current = piece
    if current:
        if merged and len(current) < MIN_CHUNK_CHARS and len(merged[-1]) + 1 + len(current) <= max_chars:
            merged[-1] = f"{merged[-1]} {current}"
        else:
            merged.append(current)
    return merged
