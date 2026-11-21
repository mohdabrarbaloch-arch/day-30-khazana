"""Question answering, retrieval explanation and usage stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.engine import synthesize
from app.models import Collection, Document, QueryLog, User
from app.schemas import (
    QueryLogOut,
    QueryRequest,
    QueryResponse,
    StatsOut,
)
from app.security import get_current_user
from app.services import registry

router = APIRouter(prefix="/api", tags=["search"])


def _owned_collection(db: Session, user: User, collection_id: str) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None or collection.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return collection


@router.post("/query", response_model=QueryResponse)
def ask(
    payload: QueryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QueryResponse:
    """Answer a question from one collection, or from all of the user's.

    When no collection is named, every collection the user owns is searched
    together. That is the common case for a personal knowledge base — you do
    not want to remember which folder a fact lives in.
    """
    settings = get_settings()

    if payload.collection_id:
        collection = _owned_collection(db, user, payload.collection_id)
        retriever = registry.get(db, collection.id)
        collection_ids = [collection.id]
    else:
        owned = db.scalars(select(Collection).where(Collection.owner_id == user.id)).all()
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="You have no collections yet — add one and upload a document first.",
            )
        collection_ids = [c.id for c in owned]
        retriever = _merged_retriever(db, collection_ids)

    result = synthesize(
        payload.question,
        retriever,
        limit=payload.limit,
        min_confidence=settings.min_confidence,
    )

    db.add(
        QueryLog(
            user_id=user.id,
            collection_id=payload.collection_id,
            question=payload.question,
            answered=result.answered,
            confidence=result.confidence,
            reason=result.reason,
        )
    )
    db.commit()

    return QueryResponse(**result.to_dict())


def _merged_retriever(db: Session, collection_ids: list[str]):
    """One retriever spanning several collections.

    Built fresh rather than cached: a cross-collection query is the rarer
    path, and caching a merged index would need invalidating on every write
    to any of its members.
    """
    from app.engine import ChunkRecord, HybridRetriever

    merged = HybridRetriever()
    for collection_id in collection_ids:
        source = registry.get(db, collection_id)
        for chunk_id in list(source._records):
            record = source.get_record(chunk_id)
            if record is not None:
                merged.add_chunk(
                    ChunkRecord(
                        chunk_id=record.chunk_id,
                        document_id=record.document_id,
                        document_title=record.document_title,
                        chunk_index=record.chunk_index,
                        text=record.text,
                    )
                )
    return merged


@router.post("/query/explain")
def explain(
    payload: QueryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Show how each ranker voted, for one question.

    This is the endpoint that makes the system auditable: it returns both
    rankers' positions and scores for every candidate, so a surprising answer
    can be traced to the ranker that produced it.
    """
    if payload.collection_id:
        collection = _owned_collection(db, user, payload.collection_id)
        retriever = registry.get(db, collection.id)
    else:
        owned = db.scalars(select(Collection).where(Collection.owner_id == user.id)).all()
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No collections to search"
            )
        retriever = _merged_retriever(db, [c.id for c in owned])

    return retriever.explain(payload.question, limit=payload.limit)


@router.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> StatsOut:
    """Usage summary, including the questions that could not be answered.

    The unanswered list is the point of this endpoint. It is a to-do list for
    the corpus, written by the people who needed the answer.
    """
    collection_ids = [
        c.id for c in db.scalars(select(Collection).where(Collection.owner_id == user.id))
    ]

    doc_count = 0
    chunk_count = 0
    if collection_ids:
        doc_count = int(
            db.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.collection_id.in_(collection_ids))
            )
            or 0
        )
        from app.models import Chunk

        chunk_count = int(
            db.scalar(
                select(func.count())
                .select_from(Chunk)
                .join(Document, Chunk.document_id == Document.id)
                .where(Document.collection_id.in_(collection_ids))
            )
            or 0
        )

    asked = int(
        db.scalar(select(func.count()).select_from(QueryLog).where(QueryLog.user_id == user.id))
        or 0
    )
    answered = int(
        db.scalar(
            select(func.count())
            .select_from(QueryLog)
            .where(QueryLog.user_id == user.id, QueryLog.answered.is_(True))
        )
        or 0
    )

    unanswered = db.scalars(
        select(QueryLog)
        .where(QueryLog.user_id == user.id, QueryLog.answered.is_(False))
        .order_by(QueryLog.created_at.desc())
        .limit(10)
    ).all()

    return StatsOut(
        collections=len(collection_ids),
        documents=doc_count,
        chunks=chunk_count,
        questions_asked=asked,
        questions_answered=answered,
        answer_rate=round(answered / asked, 4) if asked else 0.0,
        unanswered_questions=[QueryLogOut.model_validate(q) for q in unanswered],
    )
