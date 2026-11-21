"""Text normalisation, tokenisation and sentence segmentation.

Chunking, BM25, the vector index and answer synthesis all depend on these
functions agreeing about what a word and a sentence are. They live in one
module so that agreement is visible in one place rather than assumed across
four.

Two rules worth stating up front:

1. Normalisation never touches the text that gets displayed. Chunks keep the
   original characters so a citation shows the document as it was written.
   Folding happens per token, inside ``tokenize``.
2. The stoplist is deliberately small. An aggressive one hurts short queries
   far more than it helps long documents, and BM25's IDF term already
   discounts common words on its own.
"""

from __future__ import annotations

import re
import unicodedata

STOPWORDS: frozenset[str] = frozenset(
    """
    a an and are as at be been being but by can could did do does doing for from
    had has have having he her here hers him his how i if in into is it its just
    me more most my no nor not of off on once only or other our out over own same
    she should so some such than that the their them then there these they this
    those through to too under until up very was we were what when where which
    while who whom why will with would you your

    long take takes much many often usually typically
    """.split()
)

# The second group is question framing rather than content. "How long do
# refunds take?" is a question about duration; the words "long" and "take"
# carry no information about *which* document answers it, but they do match
# "if it takes longer" in an unrelated onboarding guide — which is exactly how
# a lexical retriever ends up citing the wrong document with confidence. The
# answer to a duration question is expressed as a number ("within 30 days"),
# not by repeating the word "long".

# Alphanumeric runs in any script. ``[^\W_]`` is "word character except
# underscore", which keeps Urdu, Arabic, Cyrillic and CJK letters while
# dropping punctuation.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Sentence terminators across the scripts this tool is likely to see:
# Latin full stop / bang / question, Arabic question mark, Urdu full stop.
_TERMINATORS = ".!?\u061f\u06d4"

# Abbreviations that end in a full stop without ending a sentence. Compared
# lowercased and with trailing dots stripped, so "e.g." is stored as "e.g".
_ABBREV: frozenset[str] = frozenset(
    """
    mr mrs ms dr prof sr jr st vs etc e.g i.e fig figs no inc ltd co corp dept
    est approx cf al ed eds vol pp jan feb mar apr jun jul aug sep sept oct nov
    dec mon tue tues wed thu thur thurs fri sat sun
    """.split()
)


# Suffixes stripped by ``stem``, longest first. Only inflectional endings that
# change the grammatical form of the same word — not derivational ones that
# change the meaning ("-ment", "-ness"), which would collapse distinct concepts.
#
# "-er" and "-est" are deliberately absent. They are derivational (agent nouns
# and comparatives), and stripping them turns "order" into "ord" and "server"
# into "serv" — collisions that merge unrelated words. The plural and verb
# endings below are what actually matter for retrieval.
_SUFFIXES: tuple[str, ...] = (
    "ational",
    "ations",
    "ation",
    "ating",
    "ates",
    "ated",
    "ate",
    "ions",
    "ion",
    "ings",
    "ing",
    "ies",
    "ied",
    "ed",
    "es",
    "s",
)


def fold(text: str) -> str:
    """Casefold and strip diacritics without changing character count much.

    NFKD splits an accented Latin letter into base plus combining mark, and
    also normalises Arabic presentation forms into their base letters. Dropping
    the combining marks then makes "café" and "cafe" the same token, and makes
    Urdu text written with or without vowel marks match.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def tokenize(text: str) -> list[str]:
    """Every alphanumeric token, folded, lowercased and stemmed, in order.

    Stemming happens here rather than at the call sites so that indexing and
    querying cannot disagree. If the index stored "escalation" and the query
    produced "escalate", the two would never meet — and that mismatch is
    invisible until someone asks a question in the wrong grammatical form.
    """
    return [stem(token) for token in _TOKEN_RE.findall(fold(text))]


def content_terms(text: str) -> list[str]:
    """Tokens that carry retrieval signal, deduplicated, order preserved.

    Single letters survive on purpose: "C", "R" and "X" are real terms in
    technical documents, and the stoplist has already removed the ones that
    are not ("a", "I").
    """
    seen: set[str] = set()
    out: list[str] = []
    for token in tokenize(text):
        if token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _is_boundary(text: str, i: int, j: int) -> bool:
    """Decide whether the terminator run ``text[i:j]`` ends a sentence."""
    n = len(text)

    k = j
    while k < n and text[k].isspace():
        k += 1
    if k >= n:
        return True

    nxt = text[k]
    # A sentence starts with a capital, a digit, or an opening quote/bracket.
    #
    # Scripts without letter case — Urdu, Arabic, Hebrew, CJK — have no
    # uppercase to test for, so requiring ``isupper()`` would mean those
    # languages never split a sentence at all. For a cased script the capital
    # is the signal; for an uncased one any letter is.
    if nxt.isalpha() and not (nxt.isupper() or nxt.islower()):
        # Neither upper nor lower: an uncased script. Accept it.
        pass
    elif not (nxt.isupper() or nxt.isdigit() or nxt in "\"'\u201c\u2018([\u00ab"):
        return False

    # Only the full stop needs the decimal and abbreviation guards. "!" and
    # "?" are unambiguous.
    if text[i] != ".":
        return True

    # Decimal: 3.14, 0.5
    if i > 0 and text[i - 1].isdigit() and text[k].isdigit():
        return False

    # Walk back to the start of the word this full stop belongs to.
    b = i
    while b > 0 and (text[b - 1].isalnum() or text[b - 1] == "."):
        b -= 1
    raw = text[b:i]
    word = raw.lower().strip(".")

    if word in _ABBREV:
        return False

    # A single capital letter is an initial: "J. Smith", "A. Rahman".
    return not (len(word) == 1 and word.isalpha() and raw.isupper())


def normalise_whitespace(text: str) -> str:
    """Unwrap hard-wrapped lines while keeping paragraph breaks.

    Documents arrive wrapped at 80 columns — PDFs, emails, pasted text, and
    most README files. A sentence that spans two lines is still one sentence,
    but a naive splitter sees the newline as a boundary and cuts it in half.
    The result is a citation that reads "Refunds are issued within 30 days of
    purchase, provided the item is unused and" — truncated mid-thought, which
    is worse than no citation at all.

    The rule: a single newline between two lines of text is a wrap and becomes
    a space. A blank line is a paragraph break and is preserved. A line that
    already ends in a terminator keeps its newline, because the author meant
    it as a break.
    """
    if not text:
        return ""

    # Normalise line endings first so \r\n and \r behave like \n.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = text.split("\n")
    out: list[str] = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            # Blank line: paragraph break. Collapse runs of blanks to one.
            if out and out[-1] != "":
                out.append("")
            continue

        if not out or out[-1] == "":
            out.append(stripped)
            continue

        previous = out[-1]
        # Join when the previous line did not end a sentence — that is a wrap.
        if previous and previous[-1] not in _TERMINATORS + ":;,":
            out[-1] = previous + " " + stripped
        else:
            out.append(stripped)

    return "\n".join(out).strip()


def stem(word: str) -> str:
    """A deliberately small suffix stripper.

    Not Porter, and not trying to be. The job is to make "escalate" and
    "escalation" the same term, and "refund" and "refunds" the same term, so a
    question phrased in the verb form finds a document written in the noun
    form. Prefix matching cannot do this: "escalate" and "escalation" diverge
    at the seventh character, so neither is a prefix of the other.

    The rules are ordered longest-suffix-first so "escalation" loses "ation"
    before it can lose "ion". A minimum stem length of 3 stops "is" becoming
    "i" and "as" becoming "a".
    """
    if len(word) <= 3:
        return word

    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]

    return word


def split_sentences(text: str) -> list[str]:
    """Split into sentences, keeping the terminator attached to its sentence.

    A scanner rather than a regex because the guards above need to look at
    both sides of each candidate boundary, and a lookbehind cannot express
    "unless the preceding word is an abbreviation".
    """
    if not text or not text.strip():
        return []

    # Unwrap first: a hard-wrapped sentence is one sentence, and splitting on
    # the newline would cut it in half.
    text = normalise_whitespace(text)
    if not text:
        return []

    sentences: list[str] = []
    start = 0
    i = 0
    n = len(text)

    while i < n:
        if text[i] not in _TERMINATORS:
            i += 1
            continue

        # Consume a run of terminators so "?!" and "..." stay together.
        j = i
        while j < n and text[j] in _TERMINATORS:
            j += 1

        if _is_boundary(text, i, j):
            piece = text[start:j].strip()
            if piece:
                sentences.append(piece)
            start = j

        i = j

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)

    return sentences


def truncate(text: str, limit: int = 240) -> str:
    """Shorten on a word boundary, with an ellipsis when anything was cut."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "\u2026"
