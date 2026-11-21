"""Extractive answer synthesis with citations and honest abstention.

This is the part that decides whether the tool is trustworthy. A RAG system
that always produces an answer is worse than useless: it launders "I don't
know" into confident prose, and the user has no way to tell the difference.

So the design is extractive, not generative. Every sentence in the answer is
lifted verbatim from a retrieved chunk and carries the id of the chunk it came
from. Nothing is paraphrased, nothing is invented, and if the retrieved text
does not actually contain the query's terms, the system says so instead of
answering.

Three signals decide whether to answer:

1. **Coverage** — what fraction of the query's content terms appear anywhere
   in the retrieved text. A question about "refund policy" that retrieves
   chunks containing neither word has coverage 0 and cannot be answered.
2. **Retrieval strength** — the best fused RRF score, normalised. Weak
   retrieval means the corpus probably does not cover the topic.
3. **Sentence support** — whether any individual sentence scores well against
   the query. A chunk can be topically near a question without containing the
   answer to it.

All three must clear their floor. Any one of them failing produces an
abstention with the reason attached, which is far more useful to a user than a
plausible-sounding guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.engine.hybrid import HybridRetriever
from app.engine.text import content_terms, split_sentences, truncate

# A sentence must share at least this fraction of the query's content terms.
MIN_SENTENCE_COVERAGE = 0.34
# The corpus as a whole must cover at least this much of the query.
MIN_CORPUS_COVERAGE = 0.5
# Best fused score, normalised against the theoretical maximum for the pool.
MIN_RETRIEVAL_STRENGTH = 0.18
# Sentences shorter than this are fragments and make poor answers.
MIN_SENTENCE_CHARS = 25
# How many sentences the answer may contain.
MAX_ANSWER_SENTENCES = 4

# A supporting sentence must score at least this fraction of the best one.
# 0.55 keeps a genuine second half of an answer (which usually scores close to
# the first) while dropping the incidental keyword match from an unrelated
# document.
MIN_RELATIVE_SENTENCE = 0.55


@dataclass
class Citation:
    """One sentence of the answer, tied to the chunk it was taken from."""

    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    chunk_index: int
    sentence: str
    relevance: float


@dataclass
class Answer:
    """The result of a query, whether or not it could be answered."""

    query: str
    answered: bool
    answer: str
    citations: list[Citation] = field(default_factory=list)
    confidence: float = 0.0
    confidence_label: str = "none"
    reason: str = ""
    coverage: float = 0.0
    retrieval_strength: float = 0.0
    missing_terms: list[str] = field(default_factory=list)
    sources_considered: int = 0

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "answered": self.answered,
            "answer": self.answer,
            "confidence": round(self.confidence, 4),
            "confidence_label": self.confidence_label,
            "reason": self.reason,
            "coverage": round(self.coverage, 4),
            "retrieval_strength": round(self.retrieval_strength, 4),
            "missing_terms": self.missing_terms,
            "sources_considered": self.sources_considered,
            "citations": [
                {
                    "marker": c.marker,
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "document_title": c.document_title,
                    "chunk_index": c.chunk_index,
                    "sentence": c.sentence,
                    "relevance": round(c.relevance, 4),
                }
                for c in self.citations
            ],
        }


def _label(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.5:
        return "medium"
    if confidence >= 0.25:
        return "low"
    return "none"


def _sentence_score(
    sentence: str, query_terms: set[str], weights: dict[str, float]
) -> tuple[float, set[str]]:
    """IDF-weighted fraction of the query's signal present in the sentence.

    Weighting by IDF is what makes this work on real questions. "How long do
    refunds take" contains three content terms, but "long" and "take" are
    generic English that appear everywhere; "refunds" is the one that carries
    the question. Counting terms equally would score a correct answer at 0.33
    and refuse it. Weighting by IDF scores it near 1.0, because the term that
    matters is the term that is present.

    Terms absent from the whole corpus get weight 0 and drop out of the
    denominator entirely — the system does not penalise a document for not
    containing a word that appears nowhere.
    """
    if not query_terms:
        return 0.0, set()

    total = sum(weights.get(t, 0.0) for t in query_terms)
    if total <= 0.0:
        return 0.0, set()

    sentence_terms = set(content_terms(sentence))

    # Match a query term to a sentence term either exactly or as a prefix.
    # "escalate" must match "Escalation", and "refund" must match "refunds" —
    # without this, a sentence that answers the question perfectly scores zero
    # because the author used a different inflection.
    present: set[str] = set()
    for term in query_terms:
        if term in sentence_terms:
            present.add(term)
            continue
        if any(st.startswith(term) or term.startswith(st) for st in sentence_terms):
            present.add(term)

    earned = sum(weights.get(t, 0.0) for t in present)
    return earned / total, present


def _normalise_strength(best_fused: float, rrf_k: int) -> float:
    """Map a fused RRF score onto [0, 1].

    The ceiling is a chunk ranked first by both rankers, which scores
    ``2 / (k + 1)``. Dividing by that makes the number comparable across
    different ``k`` settings instead of being an arbitrary small float.
    """
    ceiling = 2.0 / (rrf_k + 1)
    if ceiling <= 0:
        return 0.0
    return min(best_fused / ceiling, 1.0)


def synthesize(
    query: str,
    retriever: HybridRetriever,
    limit: int = 8,
    min_confidence: float = 0.25,
) -> Answer:
    """Answer ``query`` from the retriever's corpus, or refuse to.

    The refusal path is a first-class outcome, not an error. It returns
    ``answered=False`` with a reason naming which signal failed, so the UI can
    tell the user *why* the knowledge base could not help.
    """
    query_terms = set(content_terms(query))

    if not query_terms:
        return Answer(
            query=query,
            answered=False,
            answer="",
            reason="The query has no searchable terms — try asking a full question.",
            confidence_label="none",
        )

    hits = retriever.search(query, limit=limit)
    if not hits:
        return Answer(
            query=query,
            answered=False,
            answer="",
            reason="Nothing in this knowledge base matched the query.",
            missing_terms=sorted(query_terms),
            confidence_label="none",
        )

    # --- Signal 1: does the retrieved text contain the query's terms at all?
    # Weighted by IDF, so a generic word like "long" cannot dilute the signal
    # from a specific one like "refunds". Terms absent from the whole corpus
    # carry zero weight and drop out of the denominator.
    weights = {term: retriever.term_idf(term) for term in query_terms}
    weight_total = sum(weights.values())

    # The single most informative term in the question, used as a guard
    # against keyword collisions in supporting sentences.
    top_term = max(weights, key=lambda t: weights[t]) if weights else None

    corpus_terms: set[str] = set()
    for hit in hits:
        record = retriever.get_record(hit.chunk_id)
        if record is not None:
            corpus_terms |= set(content_terms(record.text))

    present = query_terms & corpus_terms
    coverage = sum(weights[t] for t in present) / weight_total if weight_total > 0.0 else 0.0
    # Terms the corpus never mentions. Deliberately NOT filtered by weight:
    # a term absent from every document has IDF 0, and filtering on weight
    # would hide exactly the words that explain why the answer was refused.
    missing = sorted(query_terms - corpus_terms)

    # --- Signal 2: how strong was the best retrieval?
    strength = _normalise_strength(hits[0].fused_score, retriever.rrf_k)

    # --- Signal 3: is there a sentence that actually supports an answer?
    candidates: list[tuple[float, int, Citation]] = []
    ordinal = 0
    for hit in hits:
        record = retriever.get_record(hit.chunk_id)
        if record is None:
            continue
        # A chunk's own retrieval quality weights its sentences, so a good
        # sentence inside a poorly-ranked chunk does not beat a good sentence
        # inside a well-ranked one.
        chunk_weight = 0.5 + 0.5 * _normalise_strength(hit.fused_score, retriever.rrf_k)
        for sentence in split_sentences(record.text):
            if len(sentence) < MIN_SENTENCE_CHARS:
                continue
            score, _ = _sentence_score(sentence, query_terms, weights)
            if score <= 0.0:
                continue
            candidates.append(
                (
                    score * chunk_weight,
                    ordinal,
                    Citation(
                        marker=0,
                        chunk_id=record.chunk_id,
                        document_id=record.document_id,
                        document_title=record.document_title,
                        chunk_index=record.chunk_index,
                        sentence=sentence,
                        relevance=score,
                    ),
                )
            )
            ordinal += 1

    best_sentence = max((c[0] for c in candidates), default=0.0)

    # --- Decide.
    if coverage < MIN_CORPUS_COVERAGE:
        return Answer(
            query=query,
            answered=False,
            answer="",
            reason=(
                "The knowledge base does not cover this question. "
                f"Missing from every retrieved passage: {', '.join(missing[:6])}."
            ),
            coverage=coverage,
            retrieval_strength=strength,
            missing_terms=missing,
            sources_considered=len(hits),
            confidence_label="none",
        )

    if strength < MIN_RETRIEVAL_STRENGTH:
        return Answer(
            query=query,
            answered=False,
            answer="",
            reason=(
                "Retrieval was too weak to answer from — the closest passages "
                "are only loosely related to the question."
            ),
            coverage=coverage,
            retrieval_strength=strength,
            missing_terms=missing,
            sources_considered=len(hits),
            confidence_label="none",
        )

    if best_sentence < MIN_SENTENCE_COVERAGE:
        return Answer(
            query=query,
            answered=False,
            answer="",
            reason=(
                "Passages mention these terms but no single sentence answers "
                "the question. Try rephrasing, or add a document that covers it."
            ),
            coverage=coverage,
            retrieval_strength=strength,
            missing_terms=missing,
            sources_considered=len(hits),
            confidence_label="none",
        )

    # --- Build the answer from the best distinct sentences.
    #
    # Ties are broken by document order, not alphabetically. When two
    # sentences score identically — which happens whenever both contain every
    # query term — the earlier one is almost always the general rule and the
    # later one an exception or a special case. "Refunds are issued within 30
    # days" comes before "Enterprise refunds above 500,000 rupees require
    # written approval". Sorting by sentence text would put the exception
    # first, which answers the question with the wrong half of the policy.
    candidates.sort(key=lambda c: (-c[0], c[1]))
    chosen: list[Citation] = []
    seen_sentences: set[str] = set()
    seen_chunks: set[str] = set()

    def _eligible(score: float, citation: Citation) -> bool:
        # Only sentences genuinely competitive with the best one.
        #
        # Without this, a question about refunds pulls in the top sentence
        # from the refund policy and then a second sentence from an unrelated
        # document that merely shares a word — "Setup should take under half a
        # day" cited next to a refund answer. The reader has to work out which
        # half of the answer is real, which is worse than a shorter answer.
        if best_sentence > 0.0 and score < best_sentence * MIN_RELATIVE_SENTENCE:
            return False

        # The sentence must contain the question's most informative term.
        #
        # "Who approves expenses over 50,000?" shares "approval" and "50,000"
        # with a sentence about enterprise *refunds* — a real keyword
        # collision, and one that scored high enough to pass the relative
        # test. But the sentence never mentions expenses, which is the word
        # that makes the question about expenses. Requiring the top-weighted
        # term is a cheap, explainable guard against that class of error.
        if top_term is not None:
            sentence_terms = set(content_terms(citation.sentence))
            if not any(
                st == top_term or st.startswith(top_term) or top_term.startswith(st)
                for st in sentence_terms
            ):
                return False

        return True

    # Pass 1: breadth. One sentence per chunk, so the answer draws on as many
    # documents as it legitimately can.
    for score, _, citation in candidates:
        if len(chosen) >= MAX_ANSWER_SENTENCES:
            break
        key = citation.sentence.strip().lower()
        if key in seen_sentences or citation.chunk_id in seen_chunks:
            continue
        if not _eligible(score, citation):
            continue
        seen_sentences.add(key)
        seen_chunks.add(citation.chunk_id)
        chosen.append(citation)

    # Pass 2: depth. Fill any remaining slots from chunks already used.
    #
    # This is what makes a tiered policy answerable. "Expenses up to 25,000
    # may be approved by a line manager" is the first rung of a ladder, and
    # the rungs above it live in the same chunk. Stopping at one sentence per
    # chunk would answer "what is the approval limit" with the lowest limit
    # and silently omit the two that matter more.
    for score, _, citation in candidates:
        if len(chosen) >= MAX_ANSWER_SENTENCES:
            break
        key = citation.sentence.strip().lower()
        if key in seen_sentences:
            continue
        if not _eligible(score, citation):
            continue
        seen_sentences.add(key)
        chosen.append(citation)

    # Restore document order so the answer reads the way the source does.
    #
    # The ordinal is the position the sentence had in the corpus, captured
    # before sorting. Using the sorted position instead would order the answer
    # by score — which put "between 25,000 and 50,000" above "up to 25,000",
    # reading a tiered policy backwards.
    order = {id(c): ordinal for _, ordinal, c in candidates}
    chosen.sort(key=lambda c: order.get(id(c), 0))

    if not chosen:
        return Answer(
            query=query,
            answered=False,
            answer="",
            reason="No sentence in the retrieved passages could support an answer.",
            coverage=coverage,
            retrieval_strength=strength,
            missing_terms=missing,
            sources_considered=len(hits),
            confidence_label="none",
        )

    for i, citation in enumerate(chosen, start=1):
        citation.marker = i

    answer_text = " ".join(f"{c.sentence} [{c.marker}]" for c in chosen)

    # Confidence blends the three signals. Coverage and sentence support carry
    # the most weight because they are the ones that mean "the text actually
    # says this"; retrieval strength is a tiebreaker.
    #
    # Capped at 0.95 on purpose. An extractive system that reports 1.00 is
    # claiming certainty it cannot have: the chunk it quoted may be stale,
    # out of date, or answering a slightly different question. Leaving a
    # visible margin is more honest than a round number.
    confidence = (
        0.40 * coverage
        + 0.35 * min(best_sentence / max(MIN_SENTENCE_COVERAGE, 1e-9), 1.0)
        + 0.25 * strength
    )
    confidence = max(0.0, min(confidence, 0.95))

    if confidence < min_confidence:
        return Answer(
            query=query,
            answered=False,
            answer="",
            reason=(
                f"Confidence {confidence:.2f} is below the {min_confidence:.2f} "
                "threshold — the evidence is too thin to answer responsibly."
            ),
            coverage=coverage,
            retrieval_strength=strength,
            missing_terms=missing,
            sources_considered=len(hits),
            confidence=confidence,
            confidence_label="none",
        )

    return Answer(
        query=query,
        answered=True,
        answer=answer_text,
        citations=chosen,
        confidence=confidence,
        confidence_label=_label(confidence),
        reason="",
        coverage=coverage,
        retrieval_strength=strength,
        missing_terms=missing,
        sources_considered=len(hits),
    )


def preview(text: str, limit: int = 200) -> str:
    """Short display form of a passage, for source cards."""
    return truncate(text, limit)
