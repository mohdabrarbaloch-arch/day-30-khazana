"""Request and response models.

Every input is bounded. An unbounded string field is a memory exhaustion
vector, and an unbounded list is a denial-of-service vector, so both have
explicit ceilings here rather than relying on the client to behave.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# --- Auth -------------------------------------------------------------------


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("display_name cannot be blank")
        return cleaned


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    is_admin: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- Collections ------------------------------------------------------------


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    created_at: datetime
    document_count: int = 0
    chunk_count: int = 0


# --- Documents --------------------------------------------------------------


class DocumentCreate(BaseModel):
    """Ingest a document from text supplied in the request body."""

    title: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("title cannot be blank")
        return cleaned


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    collection_id: str
    title: str
    source_type: str
    char_count: int
    chunk_count: int
    created_at: datetime


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chunk_index: int
    text: str
    char_start: int
    char_end: int


# --- Query ------------------------------------------------------------------


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    collection_id: str | None = None
    limit: int = Field(default=8, ge=1, le=25)

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question cannot be blank")
        return cleaned


class CitationOut(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    chunk_index: int
    sentence: str
    relevance: float


class QueryResponse(BaseModel):
    query: str
    answered: bool
    answer: str
    confidence: float
    confidence_label: str
    reason: str
    coverage: float
    retrieval_strength: float
    missing_terms: list[str]
    sources_considered: int
    citations: list[CitationOut]


class QueryLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    question: str
    answered: bool
    confidence: float
    reason: str
    created_at: datetime


# --- Stats ------------------------------------------------------------------


class StatsOut(BaseModel):
    collections: int
    documents: int
    chunks: int
    questions_asked: int
    questions_answered: int
    answer_rate: float
    unanswered_questions: list[QueryLogOut]


class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    indexed_chunks: int
