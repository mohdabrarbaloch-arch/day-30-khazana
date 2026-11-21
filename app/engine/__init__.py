"""Retrieval and answer synthesis.

Nothing in this package imports the database, the web framework or the
filesystem. It is pure functions and plain data structures over strings, which
is why the ranking behaviour can be tested in microseconds and why the API
layer cannot leak its concerns in here.
"""

from app.engine.answer import Answer, Citation, synthesize
from app.engine.bm25 import BM25Index, ScoredDoc
from app.engine.chunker import Chunk, chunk_text
from app.engine.hybrid import ChunkRecord, FusedHit, HybridRetriever
from app.engine.text import content_terms, split_sentences, tokenize
from app.engine.vector import TfidfVectorIndex, VectorHit

__all__ = [
    "Answer",
    "BM25Index",
    "Chunk",
    "ChunkRecord",
    "Citation",
    "FusedHit",
    "HybridRetriever",
    "ScoredDoc",
    "TfidfVectorIndex",
    "VectorHit",
    "chunk_text",
    "content_terms",
    "split_sentences",
    "synthesize",
    "tokenize",
]
