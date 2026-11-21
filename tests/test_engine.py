"""Tests for the retrieval engine.

These are the tests that matter most: the engine is where a wrong answer
becomes a confident wrong answer, and it is pure functions, so it can be
tested exhaustively without a database or a server.
"""

from __future__ import annotations

import pytest

from app.engine.answer import synthesize
from app.engine.bm25 import BM25Index
from app.engine.chunker import chunk_text
from app.engine.hybrid import ChunkRecord, HybridRetriever
from app.engine.text import content_terms, split_sentences, tokenize, truncate
from app.engine.vector import TfidfVectorIndex, similarity

# ── Text ─────────────────────────────────────────────────────────────────────


def test_tokenize_folds_case_and_accents():
    assert tokenize("Café CAFE cafe") == ["cafe", "cafe", "cafe"]


def test_tokenize_keeps_non_latin_scripts():
    # Urdu must survive tokenisation; a Latin-only regex would drop it.
    tokens = tokenize("رقم کی واپسی")
    assert len(tokens) == 3


def test_content_terms_drops_stopwords_and_duplicates():
    terms = content_terms("The refund policy for the refund of the order")
    assert terms == ["refund", "policy", "order"]


def test_content_terms_keeps_single_letters():
    # "C" and "R" are real terms in technical documents.
    assert "c" in content_terms("The C compiler and the R runtime")


def test_split_sentences_handles_abbreviations():
    text = "Dr. Smith joined in 2019. The policy changed."
    assert split_sentences(text) == ["Dr. Smith joined in 2019.", "The policy changed."]


def test_split_sentences_handles_decimals():
    text = "The rate is 3.14 percent. That is final."
    assert len(split_sentences(text)) == 2


def test_split_sentences_keeps_terminator_runs():
    assert split_sentences("Really?! Yes!") == ["Really?!", "Yes!"]


def test_split_sentences_handles_urdu_terminator():
    sentences = split_sentences("یہ پہلا جملہ ہے۔ یہ دوسرا جملہ ہے۔")
    assert len(sentences) == 2


def test_split_sentences_empty_input():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_truncate_respects_word_boundary():
    result = truncate("The quick brown fox jumps over the lazy dog", limit=20)
    assert result.endswith("\u2026")
    assert len(result) <= 21


def test_truncate_leaves_short_text_alone():
    assert truncate("short", limit=50) == "short"


# ── Chunker ──────────────────────────────────────────────────────────────────


def test_chunk_text_respects_max_chars():
    text = " ".join(f"This is sentence number {i} about policy." for i in range(60))
    chunks = chunk_text(text, target_chars=200, max_chars=300)
    assert chunks
    assert all(len(c.text) <= 300 for c in chunks)


def test_chunk_text_produces_multiple_chunks():
    text = " ".join(f"Sentence {i} covers a distinct topic." for i in range(40))
    chunks = chunk_text(text, target_chars=150, max_chars=250)
    assert len(chunks) > 1


def test_chunk_text_overlaps_consecutive_chunks():
    text = " ".join(f"Sentence {i} is here." for i in range(30))
    chunks = chunk_text(text, target_chars=120, max_chars=200, overlap_sentences=1)
    assert len(chunks) >= 2
    # The last sentence of chunk 0 should reappear at the start of chunk 1.
    tail = chunks[0].text.split(".")[-2].strip()
    assert tail in chunks[1].text


def test_chunk_text_empty_returns_nothing():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_rejects_bad_limits():
    with pytest.raises(ValueError):
        chunk_text("some text", target_chars=0)
    with pytest.raises(ValueError):
        chunk_text("some text", target_chars=500, max_chars=100)


def test_chunk_text_splits_one_enormous_sentence():
    text = "word " * 500  # no punctuation at all
    chunks = chunk_text(text, target_chars=200, max_chars=300)
    assert chunks
    assert all(len(c.text) <= 300 for c in chunks)


def test_chunk_indices_are_sequential():
    text = " ".join(f"Sentence {i} here." for i in range(30))
    chunks = chunk_text(text, target_chars=100, max_chars=180)
    assert [c.index for c in chunks] == list(range(len(chunks)))


# ── BM25 ─────────────────────────────────────────────────────────────────────


def test_bm25_ranks_matching_document_first():
    index = BM25Index()
    index.add("a", "refunds are issued within thirty days")
    index.add("b", "the office is closed on sunday")
    results = index.search("refund")
    assert results
    assert results[0].doc_id == "a"


def test_bm25_idf_is_never_negative():
    """A term in every document must not penalise the documents holding it."""
    index = BM25Index()
    for i in range(5):
        index.add(f"d{i}", "common word here")
    assert index.idf("common") >= 0.0


def test_bm25_rare_term_outweighs_common_term():
    index = BM25Index()
    index.add("a", "policy policy policy")
    index.add("b", "policy refund")
    index.add("c", "policy shipping")
    assert index.idf("refund") > index.idf("policy")


def test_bm25_remove_updates_index():
    index = BM25Index()
    index.add("a", "refund policy")
    index.add("b", "shipping policy")
    index.remove("a")
    results = index.search("refund")
    assert all(r.doc_id != "a" for r in results)


def test_bm25_rejects_invalid_parameters():
    with pytest.raises(ValueError):
        BM25Index(k1=0)
    with pytest.raises(ValueError):
        BM25Index(b=1.5)


def test_bm25_empty_query_returns_nothing():
    index = BM25Index()
    index.add("a", "refund policy")
    assert index.search("") == []
    assert index.search("the and of") == []


def test_bm25_contributions_sum_to_score():
    index = BM25Index()
    index.add("a", "refund policy for enterprise customers")
    result = index.search("refund enterprise")[0]
    assert abs(sum(result.contributions.values()) - result.score) < 1e-9


# ── Vector index ─────────────────────────────────────────────────────────────


def test_vector_index_matches_morphological_variants():
    """The whole point of character n-grams: refund/refunds should be close."""
    index = TfidfVectorIndex()
    index.add("a", "refunds are issued within thirty days")
    index.add("b", "the office is closed on sunday")
    hits = index.search("refund")
    assert hits
    assert hits[0].doc_id == "a"


def test_vector_similarity_is_symmetric():
    a = "refund policy for enterprise customers"
    b = "enterprise refund policy"
    assert abs(similarity(a, b) - similarity(b, a)) < 1e-9


def test_vector_similarity_identical_text_is_one():
    text = "the refund policy is thirty days"
    assert abs(similarity(text, text) - 1.0) < 1e-6


def test_vector_similarity_unrelated_text_is_low():
    assert similarity("refund policy", "quantum chromodynamics") < 0.2


def test_vector_index_rejects_bad_dimensions():
    with pytest.raises(ValueError):
        TfidfVectorIndex(dimensions=10)
    with pytest.raises(ValueError):
        TfidfVectorIndex(ngram=1)


def test_vector_index_remove():
    index = TfidfVectorIndex()
    index.add("a", "refund policy")
    index.add("b", "shipping policy")
    index.remove("a")
    assert all(h.doc_id != "a" for h in index.search("refund"))


# ── Hybrid ───────────────────────────────────────────────────────────────────


def _retriever() -> HybridRetriever:
    retriever = HybridRetriever()
    retriever.add_chunks(
        [
            ChunkRecord(
                "c1", "d1", "Refund Policy", 0, "Refunds are issued within 30 days of purchase."
            ),
            ChunkRecord(
                "c2",
                "d1",
                "Refund Policy",
                1,
                "Enterprise customers receive a dedicated account manager.",
            ),
            ChunkRecord(
                "c3", "d2", "Shipping", 0, "Orders ship within two business days via courier."
            ),
        ]
    )
    return retriever


def test_hybrid_finds_the_right_chunk():
    hits = _retriever().search("how long do refunds take")
    assert hits
    assert hits[0].chunk_id == "c1"


def test_hybrid_fused_score_is_sum_of_reciprocal_ranks():
    retriever = _retriever()
    hit = retriever.search("refund")[0]
    expected = 0.0
    if hit.bm25_rank:
        expected += 1.0 / (retriever.rrf_k + hit.bm25_rank)
    if hit.vector_rank:
        expected += 1.0 / (retriever.rrf_k + hit.vector_rank)
    assert abs(hit.fused_score - expected) < 1e-9


def test_hybrid_remove_document_clears_all_its_chunks():
    retriever = _retriever()
    removed = retriever.remove_document("d1")
    assert removed == 2
    assert all(h.chunk_id != "c1" for h in retriever.search("refund"))


def test_hybrid_explain_reports_both_rankers():
    data = _retriever().explain("refund")
    assert data["results"]
    first = data["results"][0]
    assert "bm25_rank" in first and "vector_rank" in first


def test_hybrid_empty_corpus_returns_nothing():
    assert HybridRetriever().search("anything") == []


def test_hybrid_rejects_bad_rrf_k():
    with pytest.raises(ValueError):
        HybridRetriever(rrf_k=0)


# ── Answer synthesis ─────────────────────────────────────────────────────────


def test_synthesize_answers_an_in_scope_question():
    answer = synthesize("how long do refunds take", _retriever())
    assert answer.answered
    assert "30 days" in answer.answer
    assert answer.citations


def test_synthesize_citations_carry_document_titles():
    answer = synthesize("how long do refunds take", _retriever())
    assert answer.citations[0].document_title == "Refund Policy"


def test_synthesize_abstains_on_out_of_scope_question():
    answer = synthesize("what is the capital of Mongolia", _retriever())
    assert not answer.answered
    assert answer.reason


def test_synthesize_abstains_when_terms_are_missing():
    answer = synthesize("how do I reset my password", _retriever())
    assert not answer.answered
    assert "password" in answer.missing_terms


def test_synthesize_confidence_never_reaches_one():
    """An extractive system must not claim certainty it cannot have."""
    answer = synthesize("how long do refunds take", _retriever())
    assert answer.confidence <= 0.95


def test_synthesize_confidence_label_matches_value():
    answer = synthesize("how long do refunds take", _retriever())
    if answer.confidence >= 0.75:
        assert answer.confidence_label == "high"
    elif answer.confidence >= 0.5:
        assert answer.confidence_label == "medium"


def test_synthesize_empty_query_abstains():
    answer = synthesize("", _retriever())
    assert not answer.answered


def test_synthesize_stopword_only_query_abstains():
    answer = synthesize("the and of", _retriever())
    assert not answer.answered


def test_synthesize_answer_markers_match_citations():
    answer = synthesize("how long do refunds take", _retriever())
    for citation in answer.citations:
        assert f"[{citation.marker}]" in answer.answer


def test_synthesize_to_dict_is_serialisable():
    import json

    answer = synthesize("how long do refunds take", _retriever())
    payload = json.dumps(answer.to_dict())
    assert "citations" in payload


def test_synthesize_respects_min_confidence():
    answer = synthesize("how long do refunds take", _retriever(), min_confidence=0.99)
    assert not answer.answered
