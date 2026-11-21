"""A hashed TF-IDF vector index with cosine similarity.

BM25 matches words. It cannot tell you that "car" and "automobile" are the
same idea, and it cannot match a paraphrase. A dense embedding model would fix
that, but it needs a model download, a GPU or a paid API — none of which
belong in a project that has to run with ``pip install`` and nothing else.

The middle ground used here is the hashing trick over character n-grams plus
word tokens. Character trigrams give partial paraphrase tolerance ("refund"
and "refunds" share most of their trigrams; "authenticate" and
"authentication" likewise), and hashing into a fixed-width vector means no
vocabulary has to be stored or grown.

It is not a semantic embedding and this file does not pretend otherwise. It
catches morphology and near-duplicates, which is where most real queries
actually fail, and it costs nothing to run.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from app.engine.text import content_terms, fold

DEFAULT_DIMENSIONS = 4096
DEFAULT_NGRAM = 3


def _hash_bucket(feature: str, dimensions: int) -> int:
    """Stable bucket for a feature.

    ``hash()`` is salted per process in Python 3, so it would give different
    buckets on every restart and silently invalidate every stored vector.
    blake2b is stable across processes and machines.
    """
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


def _features(text: str, ngram: int) -> list[str]:
    """Word tokens plus character n-grams of each token."""
    features: list[str] = []
    for token in content_terms(text):
        features.append("w:" + token)
        if len(token) > ngram:
            padded = f"<{token}>"
            for i in range(len(padded) - ngram + 1):
                features.append("c:" + padded[i : i + ngram])
    return features


@dataclass
class VectorHit:
    doc_id: str
    score: float


class TfidfVectorIndex:
    """Sparse hashed TF-IDF vectors, L2-normalised so cosine is a dot product."""

    def __init__(
        self,
        dimensions: int = DEFAULT_DIMENSIONS,
        ngram: int = DEFAULT_NGRAM,
    ) -> None:
        if dimensions < 64:
            raise ValueError("dimensions must be at least 64")
        if ngram < 2:
            raise ValueError("ngram must be at least 2")
        self.dimensions = dimensions
        self.ngram = ngram
        self._vectors: dict[str, dict[int, float]] = {}
        self._doc_freq: dict[int, int] = {}

    def __len__(self) -> int:
        return len(self._vectors)

    def _raw_counts(self, text: str) -> dict[int, float]:
        counts: dict[int, float] = {}
        for feature in _features(text, self.ngram):
            bucket = _hash_bucket(feature, self.dimensions)
            counts[bucket] = counts.get(bucket, 0.0) + 1.0
        return counts

    def _weighted(self, counts: dict[int, float]) -> dict[int, float]:
        """Apply the current corpus IDF to raw counts.

        Split out from ``add`` so that ``similarity`` can re-weight an existing
        document against the final corpus instead of the corpus as it stood
        when that document was inserted.
        """
        if not counts:
            return {}
        n = max(len(self._vectors), 1)
        vector: dict[int, float] = {}
        for bucket, tf in counts.items():
            df = self._doc_freq.get(bucket, 0)
            idf = math.log((1.0 + n) / (1.0 + df)) + 1.0
            vector[bucket] = (1.0 + math.log(tf)) * idf
        return vector

    def add(self, doc_id: str, text: str) -> None:
        if doc_id in self._vectors:
            self.remove(doc_id)

        counts = self._raw_counts(text)
        if not counts:
            self._vectors[doc_id] = {}
            return

        n = len(self._vectors) + 1
        vector: dict[int, float] = {}
        for bucket, tf in counts.items():
            df = self._doc_freq.get(bucket, 0) + 1
            # Smoothed IDF, always positive.
            idf = math.log((1.0 + n) / (1.0 + df)) + 1.0
            vector[bucket] = (1.0 + math.log(tf)) * idf

        self._vectors[doc_id] = self._normalise(vector)
        for bucket in counts:
            self._doc_freq[bucket] = self._doc_freq.get(bucket, 0) + 1

    def remove(self, doc_id: str) -> None:
        vector = self._vectors.pop(doc_id, None)
        if vector is None:
            return
        for bucket in vector:
            remaining = self._doc_freq.get(bucket, 0) - 1
            if remaining <= 0:
                self._doc_freq.pop(bucket, None)
            else:
                self._doc_freq[bucket] = remaining

    @staticmethod
    def _normalise(vector: dict[int, float]) -> dict[int, float]:
        norm = math.sqrt(sum(v * v for v in vector.values()))
        if norm == 0.0:
            return {}
        return {k: v / norm for k, v in vector.items()}

    def query_vector(self, text: str) -> dict[int, float]:
        """Build a query vector using the corpus IDF, then normalise it."""
        counts = self._raw_counts(text)
        if not counts:
            return {}
        n = max(len(self._vectors), 1)
        vector: dict[int, float] = {}
        for bucket, tf in counts.items():
            df = self._doc_freq.get(bucket, 0)
            idf = math.log((1.0 + n) / (1.0 + df)) + 1.0
            vector[bucket] = (1.0 + math.log(tf)) * idf
        return self._normalise(vector)

    def search(self, text: str, limit: int = 10) -> list[VectorHit]:
        """Cosine similarity against every stored vector."""
        if not self._vectors or limit <= 0:
            return []

        query = self.query_vector(text)
        if not query:
            return []

        hits: list[VectorHit] = []
        for doc_id, vector in self._vectors.items():
            if not vector:
                continue
            # Both sides are unit length, so the dot product is the cosine.
            if len(query) < len(vector):
                score = sum(w * vector.get(b, 0.0) for b, w in query.items())
            else:
                score = sum(w * query.get(b, 0.0) for b, w in vector.items())
            if score > 0.0:
                hits.append(VectorHit(doc_id=doc_id, score=score))

        hits.sort(key=lambda h: (-h.score, h.doc_id))
        return hits[:limit]


def similarity(a: str, b: str, dimensions: int = DEFAULT_DIMENSIONS) -> float:
    """Cosine similarity between two strings, for tests and dedup checks.

    Both documents are added to one index and then re-vectorised against the
    *final* corpus statistics. Computing each vector at insertion time would
    give the first document an IDF based on a corpus of one and the second an
    IDF based on a corpus of two — different weights for the same feature, and
    a dot product that is not symmetric. That asymmetry is a real bug: it made
    ``similarity(a, b) != similarity(b, a)``.
    """
    index = TfidfVectorIndex(dimensions=dimensions)
    index.add("a", a)
    index.add("b", b)

    # Rebuild both vectors with the same IDF table.
    va = index._normalise(index._weighted(index._raw_counts(a)))
    vb = index._normalise(index._weighted(index._raw_counts(b)))
    if not va or not vb:
        return 0.0
    if len(va) < len(vb):
        return sum(w * vb.get(k, 0.0) for k, w in va.items())
    return sum(w * va.get(k, 0.0) for k, w in vb.items())


def normalise_for_display(text: str) -> str:
    """Folded text, used only for comparison — never for display."""
    return fold(text)
