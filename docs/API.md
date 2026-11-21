# API reference

Base URL: `http://127.0.0.1:8000`

All endpoints except `/api/health`, `/api/auth/register` and `/api/auth/login`
require `Authorization: Bearer <token>`.

Errors return `{"detail": "..."}` with a status code that says what went wrong.

---

## Health

### `GET /api/health`

```json
{
  "status": "ok",
  "version": "1.0.0",
  "environment": "development",
  "chunks_indexed": 5
}
```

---

## Auth

### `POST /api/auth/register`

```json
{
  "email": "you@example.com",
  "password": "at-least-8-chars",
  "display_name": "Your Name"
}
```

**201** returns `{ "access_token": "...", "token_type": "bearer", "user": {...} }`

| Status | When |
|---|---|
| 201 | created |
| 409 | email already registered |
| 422 | invalid email, short password, missing field |

### `POST /api/auth/login`

```json
{ "email": "you@example.com", "password": "..." }
```

**200** returns the same shape as register. **401** on bad credentials — the
same response whether the email exists or not, so the endpoint cannot be used
to enumerate accounts.

### `GET /api/auth/me`

Returns the current user. **401** without a valid token.

---

## Collections

### `GET /api/collections`

```json
[
  {
    "id": "f57f7047e94a4f6492c187a53e91e1e4",
    "name": "Company Handbook",
    "description": "Policies, onboarding and support documentation",
    "created_at": "2026-11-21T19:11:04.953793",
    "document_count": 4,
    "chunk_count": 5
  }
]
```

### `POST /api/collections`

```json
{ "name": "Engineering Docs", "description": "optional" }
```

**201** returns the created collection.

### `DELETE /api/collections/{collection_id}`

**204** on success. **404** if it does not exist or belongs to someone else.

---

## Documents

### `POST /api/documents`

```json
{
  "collection_id": "f57f7047e94a4f6492c187a53e91e1e4",
  "title": "Refund Policy",
  "text": "Refunds are issued within 30 days..."
}
```

**201** returns the document with its chunk count.

| Status | When |
|---|---|
| 201 | ingested |
| 404 | collection not found or not yours |
| 413 | text exceeds `MAX_UPLOAD_BYTES` |
| 422 | empty text, missing field |

### `GET /api/collections/{collection_id}/documents`

Lists documents in a collection. **404** if the collection is not yours.

### `GET /api/documents/{document_id}/chunks`

Returns the chunks with their text and positions. Useful for showing a citation
in context.

### `DELETE /api/documents/{document_id}`

**204** on success, **404** if not found or not yours.

---

## Query

### `POST /api/query`

```json
{
  "question": "How long do refunds take?",
  "collection_id": null
}
```

`collection_id` is optional — omit it to search everything you own.

**200** — answered:

```json
{
  "query": "How long do refunds take?",
  "answered": true,
  "answer": "Refunds are issued within 30 days of purchase, provided the item is unused and in its original packaging. [1]",
  "confidence": 0.95,
  "confidence_label": "high",
  "reason": "",
  "coverage": 1.0,
  "retrieval_strength": 1.0,
  "missing_terms": [],
  "sources_considered": 5,
  "citations": [
    {
      "marker": 1,
      "chunk_id": "e792d888c08c4327af8047435979fc51",
      "document_id": "762e7be166f04f99aa598c82645cd3e4",
      "document_title": "Refund & Returns Policy",
      "chunk_index": 0,
      "sentence": "Refunds are issued within 30 days of purchase, provided the item is unused and in its original packaging.",
      "relevance": 0.6129
    }
  ]
}
```

**200** — refused:

```json
{
  "query": "What is the capital of France?",
  "answered": false,
  "answer": "",
  "confidence": 0.0,
  "confidence_label": "none",
  "reason": "The knowledge base does not cover this question. Missing from every retrieved passage: capital, france.",
  "coverage": 0.0,
  "retrieval_strength": 0.0,
  "missing_terms": ["capital", "france"],
  "sources_considered": 5,
  "citations": []
}
```

A refusal is a **200**, not an error. The request succeeded; the answer is that
the knowledge base cannot help. The `reason` field says which signal failed.

### `POST /api/query/explain`

Same input, returns the retrieval internals without synthesising an answer —
both rankers' results, their fused scores, and which terms matched. For
debugging why a question retrieves what it does.

---

## Stats

### `GET /api/stats`

```json
{
  "collections": 1,
  "documents": 4,
  "chunks": 5,
  "questions_asked": 12,
  "questions_answered": 9,
  "answer_rate": 0.75,
  "unanswered_questions": ["What is the capital of France?"]
}
```

`unanswered_questions` is the most useful field in the API. It is a list of
questions your knowledge base could not answer, which is a direct to-do list of
documents to add.

---

## Status codes used

| Code | Meaning here |
|---|---|
| 200 | success |
| 201 | created |
| 204 | deleted, no body |
| 401 | missing, malformed or expired token; bad credentials |
| 404 | not found **or** not yours — deliberately indistinguishable |
| 409 | duplicate email |
| 413 | document too large |
| 422 | validation failure |
| 429 | rate limit exceeded |
