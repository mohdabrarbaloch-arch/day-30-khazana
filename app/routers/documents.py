"""Collections, documents and ingestion.

Every lookup is scoped to the owner. A collection belonging to someone else
returns 404 rather than 403: a 403 confirms the record exists, which is
information the caller has no right to.
"""

from __future__ import annotations

import io

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Chunk, Collection, Document, User
from app.schemas import (
    ChunkOut,
    CollectionCreate,
    CollectionOut,
    DocumentCreate,
    DocumentOut,
)
from app.security import get_current_user
from app.services import delete_document, ingest_document, registry

router = APIRouter(prefix="/api", tags=["documents"])


def _owned_collection(db: Session, user: User, collection_id: str) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None or collection.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return collection


def _counts(db: Session, collection_id: str) -> tuple[int, int]:
    docs = db.scalar(
        select(func.count()).select_from(Document).where(Document.collection_id == collection_id)
    )
    chunks = db.scalar(
        select(func.count())
        .select_from(Chunk)
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.collection_id == collection_id)
    )
    return int(docs or 0), int(chunks or 0)


@router.post("/collections", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
def create_collection(
    payload: CollectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CollectionOut:
    collection = Collection(owner_id=user.id, name=payload.name, description=payload.description)
    db.add(collection)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a collection with that name",
        ) from None
    db.refresh(collection)
    return CollectionOut.model_validate(collection)


@router.get("/collections", response_model=list[CollectionOut])
def list_collections(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[CollectionOut]:
    rows = db.scalars(
        select(Collection)
        .where(Collection.owner_id == user.id)
        .order_by(Collection.created_at.desc())
    ).all()

    out: list[CollectionOut] = []
    for collection in rows:
        doc_count, chunk_count = _counts(db, collection.id)
        item = CollectionOut.model_validate(collection)
        item.document_count = doc_count
        item.chunk_count = chunk_count
        out.append(item)
    return out


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def remove_collection(
    collection_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    collection = _owned_collection(db, user, collection_id)
    db.delete(collection)
    db.commit()
    registry.invalidate(collection_id)


@router.post(
    "/collections/{collection_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
def add_document(
    collection_id: str,
    payload: DocumentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentOut:
    settings = get_settings()
    collection = _owned_collection(db, user, collection_id)

    if len(payload.text) > settings.max_document_chars:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Document is {len(payload.text):,} characters; the limit is "
                f"{settings.max_document_chars:,}."
            ),
        )

    document = ingest_document(db, collection, payload.title, payload.text, "text")
    return DocumentOut.model_validate(document)


@router.post(
    "/collections/{collection_id}/upload",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    collection_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentOut:
    """Ingest a .txt, .md, .csv or .pdf file.

    The size check happens on the bytes actually read, not on the
    ``Content-Length`` header, because the header is client-supplied and a
    lying client would otherwise stream past the limit.
    """
    settings = get_settings()
    collection = _owned_collection(db, user, collection_id)

    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File is {len(raw) / 1024 / 1024:.1f} MB; the limit is "
                f"{settings.max_upload_bytes / 1024 / 1024:.0f} MB."
            ),
        )

    filename = file.filename or "untitled"
    lowered = filename.lower()

    if lowered.endswith(".pdf"):
        text = _extract_pdf(raw)
        source_type = "pdf"
    elif lowered.endswith((".txt", ".md", ".markdown", ".csv")):
        text = raw.decode("utf-8", errors="replace")
        source_type = "text"
    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Supported file types: .txt, .md, .csv, .pdf",
        )

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No readable text found in that file.",
        )

    if len(text) > settings.max_document_chars:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Extracted text is {len(text):,} characters; the limit is "
                f"{settings.max_document_chars:,}."
            ),
        )

    document = ingest_document(db, collection, title or filename, text, source_type)
    return DocumentOut.model_validate(document)


def _extract_pdf(raw: bytes) -> str:
    """Pull text out of a PDF, page by page.

    A scanned PDF has no text layer and yields an empty string. That is
    reported as "no readable text" rather than silently ingesting nothing —
    OCR is a separate job and pretending otherwise would be a lie.
    """
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is declared
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF support is not installed on this server.",
        ) from None

    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That file could not be read as a PDF.",
        ) from None

    return "\n\n".join(p.strip() for p in pages if p.strip())


@router.get("/collections/{collection_id}/documents", response_model=list[DocumentOut])
def list_documents(
    collection_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DocumentOut]:
    _owned_collection(db, user, collection_id)
    rows = db.scalars(
        select(Document)
        .where(Document.collection_id == collection_id)
        .order_by(Document.created_at.desc())
    ).all()
    return [DocumentOut.model_validate(d) for d in rows]


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkOut])
def list_chunks(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ChunkOut]:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    _owned_collection(db, user, document.collection_id)

    rows = db.scalars(
        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
    ).all()
    return [ChunkOut.model_validate(c) for c in rows]


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def remove_document(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    _owned_collection(db, user, document.collection_id)
    delete_document(db, document)
