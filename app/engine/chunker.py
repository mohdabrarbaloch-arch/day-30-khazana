"""Sentence-aware chunking.

The naive chunker cuts every N characters, which is why so many RAG systems
quote half a sentence back at you. This one accumulates whole sentences and
only breaks between them, so every chunk is readable on its own and every
citation is a complete thought.

Overlap exists because the answer to a question often straddles a boundary:
the setup is at the end of one chunk and the payoff at the start of the next.
Carrying the last sentence forward means the retriever can still see them
together.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.text import split_sentences

DEFAULT_TARGET_CHARS = 900
DEFAULT_MAX_CHARS = 1400
DEFAULT_OVERLAP_SENTENCES = 1


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit, with enough provenance to cite it."""

    index: int
    text: str
    char_start: int
    char_end: int
    sentence_count: int


def _hard_split(sentence: str, max_chars: int) -> list[str]:
    """Break a single over-long sentence on word boundaries.

    Only reached when one sentence exceeds the whole chunk budget — a wall of
    text with no punctuation, or a table flattened into a line. Splitting is
    better than emitting a chunk that blows past the limit.
    """
    pieces: list[str] = []
    remaining = sentence
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return [p for p in pieces if p]


def chunk_text(
    text: str,
    target_chars: int = DEFAULT_TARGET_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_sentences: int = DEFAULT_OVERLAP_SENTENCES,
) -> list[Chunk]:
    """Split ``text`` into overlapping, sentence-aligned chunks.

    ``target_chars`` is the size the accumulator aims for; ``max_chars`` is the
    hard ceiling a chunk may not exceed. They differ so that a chunk can take
    one more sentence when that sentence is short, instead of leaving a stub
    behind.
    """
    if not text or not text.strip():
        return []
    if target_chars <= 0 or max_chars < target_chars:
        raise ValueError("require 0 < target_chars <= max_chars")

    sentences = split_sentences(text)
    if not sentences:
        return []

    # Expand any sentence that is on its own larger than a whole chunk.
    expanded: list[str] = []
    for sentence in sentences:
        if len(sentence) > max_chars:
            expanded.extend(_hard_split(sentence, max_chars))
        else:
            expanded.append(sentence)

    chunks: list[Chunk] = []
    cursor = 0
    i = 0
    n = len(expanded)

    while i < n:
        buffer: list[str] = []
        size = 0
        j = i

        while j < n:
            addition = len(expanded[j]) + (1 if buffer else 0)
            # Always take the first sentence, even if it alone is over target.
            if buffer and size + addition > target_chars:
                break
            if buffer and size + addition > max_chars:
                break
            buffer.append(expanded[j])
            size += addition
            j += 1

        if not buffer:  # defensive: cannot happen, but never loop forever
            buffer = [expanded[i]]
            j = i + 1

        body = " ".join(buffer)
        chunks.append(
            Chunk(
                index=len(chunks),
                text=body,
                char_start=cursor,
                char_end=cursor + len(body),
                sentence_count=len(buffer),
            )
        )
        cursor += len(body) + 1

        if j >= n:
            break

        # Step back for overlap, but never so far that we fail to advance.
        step_back = min(overlap_sentences, len(buffer) - 1)
        i = max(j - step_back, i + 1)

    return chunks
