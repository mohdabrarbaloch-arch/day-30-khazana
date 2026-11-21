"""BM25 ranking, implemented directly.

Why not a library: the whole point of this project is that retrieval is
inspectable. BM25 is four lines of arithmetic and a dictionary of counts, and
writing it out means the score breakdown can be shown to a user instead of
being a number that came out of a black box.

BM25 scores a document for a query as the sum, over query terms, of

    idf(t) * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))

where ``tf`` is the term's frequency in the document, ``dl`` the document
length in tokens, ``avgdl`` the mean document length, ``k1`` controls how
quickly repeated terms stop helping, and ``b`` how strongly long documents are
penalised.

The IDF variant used here is the "probabilistic" one with a +0.5 floor, which
is the form that stays positive for terms appearing in more than half the
corpus. The textbook ``log((N - df + 0.5) / (df + 0.5))`` goes negative there,
which makes a common term actively penalise a document that contains it — a
real bug in a lot of hand-rolled implementations.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from app.engine.text import content_terms

DEFAULT_K1 = 1.5
DEFAULT_B = 0.75


@dataclass
class ScoredDoc:
    """A document's score plus the per-term contributions that produced it."""

    doc_id: str
    score: float
    matched_terms: list[str] = field(default_factory=list)
    contributions: dict[str, float] = field(default_factory=dict)


class BM25Index:
    """An in-memory inverted index over a fixed set of documents."""

    def __init__(self, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be positive")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be between 0 and 1")
        self.k1 = k1
        self.b = b
        self._term_freqs: dict[str, Counter[str]] = {}
        self._lengths: dict[str, int] = {}
        self._doc_freq: Counter[str] = Counter()
        self._avg_length: float = 0.0

    def __len__(self) -> int:
        return len(self._term_freqs)

    @property
    def avg_length(self) -> float:
        return self._avg_length

    def add(self, doc_id: str, text: str) -> None:
        """Index one document. Re-adding an id replaces it cleanly."""
        if doc_id in self._term_freqs:
            self.remove(doc_id)

        terms = content_terms(text)
        counts = Counter(terms)
        self._term_freqs[doc_id] = counts
        self._lengths[doc_id] = len(terms)
        for term in counts:
            self._doc_freq[term] += 1
        self._recompute_avg()

    def remove(self, doc_id: str) -> None:
        counts = self._term_freqs.pop(doc_id, None)
        if counts is None:
            return
        self._lengths.pop(doc_id, None)
        for term in counts:
            self._doc_freq[term] -= 1
            if self._doc_freq[term] <= 0:
                del self._doc_freq[term]
        self._recompute_avg()

    def _recompute_avg(self) -> None:
        if not self._lengths:
            self._avg_length = 0.0
            return
        self._avg_length = sum(self._lengths.values()) / len(self._lengths)

    def idf(self, term: str) -> float:
        """Inverse document frequency, floored so it never goes negative."""
        n = len(self._term_freqs)
        if n == 0:
            return 0.0
        df = self._doc_freq.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def vocabulary(self) -> list[str]:
        """Every term the index knows about.

        Used by the answer synthesiser to resolve a query term to the corpus
        forms it prefixes ("escalate" -> "escalation").
        """
        return list(self._doc_freq)

    def document_frequency(self, term: str) -> int:
        """How many indexed documents contain ``term``."""
        return self._doc_freq.get(term, 0)

    def search(self, query: str, limit: int = 10) -> list[ScoredDoc]:
        """Rank documents against ``query``, best first.

        Query terms are matched by prefix against the corpus vocabulary, so
        "refund" finds a document that only says "refunds". BM25 has no
        stemming of its own, and without this a user asking about "refund"
        would get nothing from a document about "refunds" — which is the most
        common way a lexical search fails in practice.

        An exact match is preferred over a prefix match for the same term, so
        a document containing the literal word still outranks one that only
        contains a longer form.
        """
        if not self._term_freqs or limit <= 0:
            return []

        terms = content_terms(query)
        if not terms:
            return []

        # Expand each query term to the corpus terms it prefixes.
        expanded: dict[str, list[str]] = {}
        for term in terms:
            matches = [t for t in self._doc_freq if t == term or t.startswith(term)]
            if matches:
                expanded[term] = matches

        if not expanded:
            return []

        avgdl = self._avg_length or 1.0
        results: list[ScoredDoc] = []

        for doc_id, counts in self._term_freqs.items():
            dl = self._lengths[doc_id]
            score = 0.0
            contributions: dict[str, float] = {}

            for query_term, corpus_terms in expanded.items():
                # Sum over the corpus forms this query term covers, but count
                # the query term once in the breakdown so the explanation
                # stays readable.
                term_score = 0.0
                for corpus_term in corpus_terms:
                    tf = counts.get(corpus_term, 0)
                    if tf == 0:
                        continue
                    idf = self.idf(corpus_term)
                    if idf <= 0.0:
                        continue
                    denom = tf + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                    term_score += idf * (tf * (self.k1 + 1.0)) / denom

                if term_score > 0.0:
                    contributions[query_term] = term_score
                    score += term_score

            if score > 0.0:
                results.append(
                    ScoredDoc(
                        doc_id=doc_id,
                        score=score,
                        matched_terms=list(contributions),
                        contributions=contributions,
                    )
                )

        results.sort(key=lambda r: (-r.score, r.doc_id))
        return results[:limit]
