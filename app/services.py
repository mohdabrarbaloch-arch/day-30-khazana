"""Ingestion and the in-memory index registry.

The indexes live in process memory, keyed by collection. They are built lazily
on first use and rebuilt from the database whenever a document is added or
removed, which keeps the source of truth in one place: the ``chunks`` table.

Rebuilding rather than incrementally patching is a deliberate trade. A
collection with a few hundred chunks rebuilds in milliseconds, and a full
rebuild cannot drift out of sync with the database the way incremental updates
can. The cost only becomes real at tens of thousands of chunks, at which point
the answer is a real vector store, not a cleverer cache.
"""

from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engine import ChunkRecord, HybridRetriever, chunk_text
from app.models import Chunk, Collection, Document


class RetrieverRegistry:
    """One ``HybridRetriever`` per collection, built on demand."""

    def __init__(self) -> None:
        self._retrievers: dict[str, HybridRetriever] = {}
        self._lock = threading.Lock()

    def _build(self, db: Session, collection_id: str) -> HybridRetriever:
        retriever = HybridRetriever()

        rows = db.execute(
            select(Chunk, Document.title)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.collection_id == collection_id)
            .order_by(Document.id, Chunk.chunk_index)
        ).all()

        records = [
            ChunkRecord(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=title,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
            )
            for chunk, title in rows
        ]
        retriever.add_chunks(records)
        return retriever

    def get(self, db: Session, collection_id: str) -> HybridRetriever:
        with self._lock:
            cached = self._retrievers.get(collection_id)
        if cached is not None:
            return cached
        built = self._build(db, collection_id)
        with self._lock:
            self._retrievers[collection_id] = built
        return built

    def invalidate(self, collection_id: str) -> None:
        with self._lock:
            self._retrievers.pop(collection_id, None)

    def clear(self) -> None:
        with self._lock:
            self._retrievers.clear()

    @property
    def cached_collections(self) -> int:
        with self._lock:
            return len(self._retrievers)


registry = RetrieverRegistry()


def ingest_document(
    db: Session,
    collection: Collection,
    title: str,
    text: str,
    source_type: str = "text",
) -> Document:
    """Chunk ``text``, store the chunks, and invalidate the collection index.

    The document row is written first so that a failure part-way through
    chunking leaves a document with zero chunks rather than orphaned chunks
    with no parent. Zero chunks is visible and fixable; orphans are not.
    """
    settings = get_settings()

    document = Document(
        collection_id=collection.id,
        title=title,
        source_type=source_type,
        char_count=len(text),
    )
    db.add(document)
    db.flush()

    chunks = chunk_text(
        text,
        target_chars=settings.chunk_target_chars,
        max_chars=settings.chunk_max_chars,
        overlap_sentences=settings.chunk_overlap_sentences,
    )

    for piece in chunks:
        db.add(
            Chunk(
                document_id=document.id,
                chunk_index=piece.index,
                text=piece.text,
                char_start=piece.char_start,
                char_end=piece.char_end,
            )
        )

    document.chunk_count = len(chunks)
    db.commit()
    db.refresh(document)

    registry.invalidate(collection.id)
    return document


def delete_document(db: Session, document: Document) -> None:
    collection_id = document.collection_id
    db.delete(document)
    db.commit()
    registry.invalidate(collection_id)
