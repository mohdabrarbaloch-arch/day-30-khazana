"""Tests for the behaviours that make answers trustworthy.

These cover the bugs found by actually running the app against the demo
corpus, not by reading the code. Each one would have shipped a wrong answer
that looked confident.
"""

from __future__ import annotations

from app.engine.answer import synthesize
from app.engine.hybrid import ChunkRecord, HybridRetriever
from app.engine.text import normalise_whitespace, split_sentences, stem

REFUND = (
    "Refunds are issued within 30 days of purchase, provided the item is unused "
    "and in its original packaging. To start a refund, contact support with your "
    "order number and the email address used at checkout. Refunds are processed "
    "to the original payment method and typically appear within five to seven "
    "business days. Enterprise customers on an annual contract receive a "
    "dedicated account manager and a 60-day refund window instead of 30. "
    "Digital products and gift cards are non-refundable once the licence key "
    "has been issued."
)

EXPENSE = (
    "Expenses up to 25,000 rupees may be approved by a direct line manager. "
    "Expenses between 25,000 and 50,000 rupees require approval from the "
    "department head. Any expense over 50,000 rupees requires director "
    "approval, and anything above 200,000 rupees additionally requires "
    "sign-off from the finance committee."
)

SUPPORT = (
    "Standard support hours are 9am to 6pm, Monday through Friday, Pakistan "
    "Standard Time. Escalation to engineering happens when a ticket has been "
    "open for more than three business days without resolution, or immediately "
    "for any severity-one incident."
)

ONBOARDING = (
    "Setup should take under half a day; if it takes longer, that is a bug in "
    "the documentation and should be reported to the platform team."
)


def _retriever() -> HybridRetriever:
    r = HybridRetriever()
    r.add_chunks(
        [
            ChunkRecord("c1", "d1", "Refund & Returns Policy", 0, REFUND),
            ChunkRecord("c2", "d2", "Expense & Approval Policy", 0, EXPENSE),
            ChunkRecord("c3", "d3", "Support Hours & Escalation", 0, SUPPORT),
            ChunkRecord("c4", "d4", "Onboarding Guide", 0, ONBOARDING),
        ]
    )
    return r


# ── Whitespace unwrapping ────────────────────────────────────────────────────


def test_hard_wrapped_sentence_is_one_sentence():
    """A sentence split across lines must not be cut in half.

    PDFs, emails and pasted text arrive wrapped at 80 columns. Splitting on
    the newline produced citations that ended mid-thought.
    """
    wrapped = (
        "Refunds are issued within 30 days of purchase, provided the item is\n"
        "unused and in its original packaging."
    )
    sentences = split_sentences(wrapped)
    assert len(sentences) == 1
    assert sentences[0].endswith("original packaging.")


def test_paragraph_break_is_preserved():
    text = "First paragraph here.\n\nSecond paragraph here."
    assert normalise_whitespace(text) == "First paragraph here.\n\nSecond paragraph here."


def test_line_ending_in_terminator_keeps_its_break():
    text = "One sentence.\nAnother sentence."
    assert split_sentences(text) == ["One sentence.", "Another sentence."]


def test_crlf_is_normalised():
    assert normalise_whitespace("a\r\nb") == "a b"


# ── Stemming ─────────────────────────────────────────────────────────────────


def test_stem_merges_verb_and_noun_forms():
    """'escalate' and 'escalation' must be the same term.

    Prefix matching cannot do this — they diverge at the seventh character —
    so a question asked in the verb form found nothing in a document written
    in the noun form.
    """
    assert stem("escalate") == stem("escalation")


def test_stem_merges_plural_and_singular():
    assert stem("refunds") == stem("refund")
    assert stem("tickets") == stem("ticket")


def test_stem_does_not_destroy_short_words():
    """'-er' is derivational and must not be stripped: 'order' is not 'ord'."""
    assert stem("order") == "order"
    assert stem("server") == "server"


def test_stem_leaves_short_words_alone():
    assert stem("is") == "is"
    assert stem("as") == "as"


# ── Answer quality ───────────────────────────────────────────────────────────


def test_duration_question_gets_the_duration_sentence():
    """'How long do refunds take?' must answer with the 30-day rule.

    The words 'long' and 'take' are question framing, not content. Left in,
    they matched 'if it takes longer' in the onboarding guide and the answer
    cited the wrong document.
    """
    answer = synthesize("How long do refunds take?", _retriever())
    assert answer.answered
    assert "30 days" in answer.answer
    assert "Onboarding" not in {c.document_title for c in answer.citations}


def test_tiered_policy_returns_the_whole_ladder():
    """An approval-limit question must not stop at the lowest tier.

    The rungs live in the same chunk, so a strict one-sentence-per-chunk rule
    answered with '25,000' and silently omitted the two limits above it.
    """
    answer = synthesize("What is the expense approval limit?", _retriever())
    assert answer.answered
    assert "25,000" in answer.answer
    assert "50,000" in answer.answer
    assert "200,000" in answer.answer


def test_unrelated_document_is_not_cited():
    """A keyword collision must not drag an unrelated document into the answer."""
    answer = synthesize("How do I escalate a support ticket?", _retriever())
    assert answer.answered
    titles = {c.document_title for c in answer.citations}
    assert "Onboarding Guide" not in titles


def test_out_of_scope_question_abstains():
    answer = synthesize("What is the capital of France?", _retriever())
    assert not answer.answered
    assert answer.answer == ""
    assert answer.citations == []
    assert answer.confidence_label == "none"


def test_abstention_names_the_missing_terms():
    answer = synthesize("What is the capital of France?", _retriever())
    assert "capital" in answer.missing_terms or "france" in answer.missing_terms


def test_answer_citations_are_numbered_in_order():
    answer = synthesize("What is the expense approval limit?", _retriever())
    markers = [c.marker for c in answer.citations]
    assert markers == list(range(1, len(markers) + 1))


def test_answer_text_markers_match_citations():
    answer = synthesize("What is the expense approval limit?", _retriever())
    for citation in answer.citations:
        assert f"[{citation.marker}]" in answer.answer


def test_confidence_is_bounded():
    answer = synthesize("How long do refunds take?", _retriever())
    assert 0.0 <= answer.confidence <= 1.0


def test_empty_query_abstains():
    answer = synthesize("", _retriever())
    assert not answer.answered


def test_stopword_only_query_abstains():
    answer = synthesize("the and of", _retriever())
    assert not answer.answered
