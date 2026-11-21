"""Hybrid retrieval: lexical BM25 fused with hashed TF-IDF vectors.

Two rankers, one list. BM25 is precise about exact words and hopeless about
morphology; the vector index is the reverse. Fusing them means a query for
"refund policy" finds the chunk that says "refunds are issued" without losing
the chunk that says "refund policy" verbatim.

Fusion is reciprocal rank fusion rather than a weighted sum of raw scores.
That choice matters: BM25 scores are unbounded and depend on corpus statistics,
cosine similarities are bounded in [0, 1], and any fixed weighting between them
is a constant that has to be re-tuned every time the corpus changes. RRF only
looks at *position*, so it needs no calibration and cannot be broken by one
ranker producing a wild outlier score.

    RRF(d) = sum over rankers of 1 / (k + rank(d))

with k = 60 from the original Cormack et al. paper. A large k flattens the
curve so that being ranked 1st versus 2nd matters less than being present in
both lists at all — which is exactly the behaviour you want when the two
rankers disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.engine.bm25 import BM25Index
from app.engine.vector import TfidfVectorIndex

DEFAULT_RRF_K = 60


@dataclass(frozen=True)
class ChunkRecord:
    """A chunk plus the provenance needed to cite it back to its source."""

    chunk_id: str
    document_id: str
    document_title: str
    chunk_index: int
    text: str


@dataclass
class FusedHit:
    """One result, carrying both rankers' opinions so the UI can show them."""

    chunk_id: str
    fused_score: float
    bm25_rank: int | None = None
    bm25_score: float = 0.0
    vector_rank: int | None = None
    vector_score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)

    @property
    def found_by_both(self) -> bool:
        return self.bm25_rank is not None and self.vector_rank is not None


class HybridRetriever:
    """Holds both indexes and the chunk text, and answers ranked queries."""

    def __init__(
        self,
        rrf_k: int = DEFAULT_RRF_K,
        bm25_k1: float = 1.5,
        bm25_b: float = 0.75,
        dimensions: int = 4096,
        ngram: int = 3,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        self.rrf_k = rrf_k
        self.bm25 = BM25Index(k1=bm25_k1, b=bm25_b)
        self.vectors = TfidfVectorIndex(dimensions=dimensions, ngram=ngram)
        self._records: dict[str, ChunkRecord] = {}

    def __len__(self) -> int:
        return len(self._records)

    @property
    def chunk_count(self) -> int:
        return len(self._records)

    def add_chunk(self, record: ChunkRecord) -> None:
        """Index one chunk. Re-adding an id replaces it in both indexes."""
        self._records[record.chunk_id] = record
        self.bm25.add(record.chunk_id, record.text)
        self.vectors.add(record.chunk_id, record.text)

    def add_chunks(self, records: list[ChunkRecord]) -> int:
        for record in records:
            self.add_chunk(record)
        return len(records)

    def remove_document(self, document_id: str) -> int:
        """Drop every chunk belonging to a document. Returns how many went."""
        doomed = [cid for cid, rec in self._records.items() if rec.document_id == document_id]
        for chunk_id in doomed:
            self._records.pop(chunk_id, None)
            self.bm25.remove(chunk_id)
            self.vectors.remove(chunk_id)
        return len(doomed)

    def get_record(self, chunk_id: str) -> ChunkRecord | None:
        return self._records.get(chunk_id)

    def term_idf(self, term: str) -> float:
        """Corpus IDF for a term, resolving morphological variants.

        Answer synthesis uses this to weight query terms by how much signal
        they actually carry. A term appearing in no document scores 0 and is
        ignored; a term appearing in one document out of a thousand dominates.

        The lookup is prefix-aware for the same reason BM25's search is: a
        user asks about "escalate" while the document says "Escalation". An
        exact-only lookup returns IDF 0 for "escalate", which silently drops
        the single most important word in the question from the weighting —
        and then the answer is chosen on the strength of "support" alone.
        """
        exact = self.bm25.idf(term)
        if exact > 0.0:
            return exact

        # No exact match: take the best-scoring corpus term this prefixes.
        best = 0.0
        for corpus_term in self.bm25.vocabulary():
            if corpus_term.startswith(term) and len(corpus_term) > len(term):
                best = max(best, self.bm25.idf(corpus_term))
        return best

    def document_frequency(self, term: str) -> int:
        """How many indexed chunks contain ``term``."""
        return self.bm25.document_frequency(term)

    def clear(self) -> None:
        self.bm25 = BM25Index(k1=self.bm25.k1, b=self.bm25.b)
        self.vectors = TfidfVectorIndex(
            dimensions=self.vectors.dimensions, ngram=self.vectors.ngram
        )
        self._records.clear()

    def search(self, query: str, limit: int = 8, pool: int = 50) -> list[FusedHit]:
        """Fuse both rankers and return the top ``limit`` chunks.

        ``pool`` is how deep each ranker is asked to go before fusion. It is
        larger than ``limit`` on purpose: a chunk ranked 30th by BM25 and 2nd
        by the vector index should still be able to win the fusion, and it
        cannot do that if BM25 was only asked for its top 8.
        """
        if not self._records or limit <= 0:
            return []

        lexical = self.bm25.search(query, limit=pool)
        semantic = self.vectors.search(query, limit=pool)

        fused: dict[str, FusedHit] = {}

        for rank, hit in enumerate(lexical, start=1):
            entry = fused.setdefault(hit.doc_id, FusedHit(chunk_id=hit.doc_id, fused_score=0.0))
            entry.bm25_rank = rank
            entry.bm25_score = hit.score
            entry.matched_terms = list(hit.matched_terms)
            entry.fused_score += 1.0 / (self.rrf_k + rank)

        for rank, hit in enumerate(semantic, start=1):
            entry = fused.setdefault(hit.doc_id, FusedHit(chunk_id=hit.doc_id, fused_score=0.0))
            entry.vector_rank = rank
            entry.vector_score = hit.score
            entry.fused_score += 1.0 / (self.rrf_k + rank)

        results = list(fused.values())
        results.sort(key=lambda h: (-h.fused_score, h.chunk_id))
        return results[:limit]

    def explain(self, query: str, limit: int = 8) -> dict:
        """A full, serialisable account of how a query was answered.

        Used by the API's ``/query/explain`` endpoint and by the tests that
        assert the two rankers actually disagree somewhere — if they never do,
        the hybrid is decoration and one of them should be deleted.
        """
        hits = self.search(query, limit=limit)
        return {
            "query": query,
            "chunk_count": len(self._records),
            "rrf_k": self.rrf_k,
            "results": [
                {
                    "chunk_id": hit.chunk_id,
                    "fused_score": round(hit.fused_score, 6),
                    "bm25_rank": hit.bm25_rank,
                    "bm25_score": round(hit.bm25_score, 4),
                    "vector_rank": hit.vector_rank,
                    "vector_score": round(hit.vector_score, 4),
                    "found_by_both": hit.found_by_both,
                    "matched_terms": hit.matched_terms,
                }
                for hit in hits
            ],
        }
