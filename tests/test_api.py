"""API tests: auth, ownership isolation, ingestion and querying."""

from __future__ import annotations

from tests.conftest import SAMPLE_TEXT

# ── Auth ─────────────────────────────────────────────────────────────────────


def test_register_returns_token_and_user(client):
    response = client.post(
        "/api/auth/register",
        json={
            "email": "new@example.com",
            "display_name": "New Person",
            "password": "a-good-password",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == "new@example.com"


def test_first_user_becomes_admin(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "first@example.com", "display_name": "First", "password": "password123"},
    )
    assert response.json()["user"]["is_admin"] is True


def test_second_user_is_not_admin(client):
    client.post(
        "/api/auth/register",
        json={"email": "first@example.com", "display_name": "First", "password": "password123"},
    )
    response = client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "display_name": "Second", "password": "password123"},
    )
    assert response.json()["user"]["is_admin"] is False


def test_duplicate_email_returns_409(client):
    payload = {"email": "dup@example.com", "display_name": "Dup", "password": "password123"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_login_with_wrong_password_returns_401(client):
    client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "display_name": "User", "password": "password123"},
    )
    response = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_login_with_unknown_email_returns_401(client):
    response = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "password123"}
    )
    assert response.status_code == 401


def test_login_succeeds_with_correct_password(client):
    client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "display_name": "User", "password": "password123"},
    )
    response = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "password123"}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_me_requires_a_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_a_garbage_token(client):
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


def test_me_returns_the_current_user(client, auth):
    response = client.get("/api/auth/me", headers=auth)
    assert response.status_code == 200
    assert response.json()["email"] == "tester@example.com"


def test_short_password_is_rejected(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "x@example.com", "display_name": "X", "password": "short"},
    )
    assert response.status_code == 422


def test_invalid_email_is_rejected(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "display_name": "X", "password": "password123"},
    )
    assert response.status_code == 422


# ── Collections ──────────────────────────────────────────────────────────────


def test_create_collection(client, auth):
    response = client.post("/api/collections", json={"name": "HR", "description": ""}, headers=auth)
    assert response.status_code == 201
    assert response.json()["name"] == "HR"


def test_duplicate_collection_name_returns_409(client, auth):
    client.post("/api/collections", json={"name": "HR", "description": ""}, headers=auth)
    response = client.post("/api/collections", json={"name": "HR", "description": ""}, headers=auth)
    assert response.status_code == 409


def test_list_collections_requires_auth(client):
    assert client.get("/api/collections").status_code == 401


def test_collections_are_isolated_between_users(client):
    first = client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "display_name": "A", "password": "password123"},
    ).json()
    second = client.post(
        "/api/auth/register",
        json={"email": "b@example.com", "display_name": "B", "password": "password123"},
    ).json()

    client.post(
        "/api/collections",
        json={"name": "A's docs", "description": ""},
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )

    response = client.get(
        "/api/collections", headers={"Authorization": f"Bearer {second['access_token']}"}
    )
    assert response.json() == []


def test_other_users_collection_returns_404(client):
    first = client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "display_name": "A", "password": "password123"},
    ).json()
    second = client.post(
        "/api/auth/register",
        json={"email": "b@example.com", "display_name": "B", "password": "password123"},
    ).json()

    created = client.post(
        "/api/collections",
        json={"name": "Private", "description": ""},
        headers={"Authorization": f"Bearer {first['access_token']}"},
    ).json()

    response = client.get(
        f"/api/collections/{created['id']}/documents",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    # 404, not 403: a 403 would confirm the collection exists.
    assert response.status_code == 404


# ── Documents ────────────────────────────────────────────────────────────────


def test_add_document_chunks_it(client, auth, collection):
    response = client.post(
        f"/api/collections/{collection['id']}/documents",
        json={"title": "Policies", "text": SAMPLE_TEXT},
        headers=auth,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["chunk_count"] >= 1
    assert body["char_count"] == len(SAMPLE_TEXT)


def test_document_listing(client, auth, collection):
    client.post(
        f"/api/collections/{collection['id']}/documents",
        json={"title": "Policies", "text": SAMPLE_TEXT},
        headers=auth,
    )
    response = client.get(f"/api/collections/{collection['id']}/documents", headers=auth)
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_chunks_are_retrievable(client, auth, collection):
    created = client.post(
        f"/api/collections/{collection['id']}/documents",
        json={"title": "Policies", "text": SAMPLE_TEXT},
        headers=auth,
    ).json()
    response = client.get(f"/api/documents/{created['id']}/chunks", headers=auth)
    assert response.status_code == 200
    assert len(response.json()) == created["chunk_count"]


def test_delete_document(client, auth, collection):
    created = client.post(
        f"/api/collections/{collection['id']}/documents",
        json={"title": "Policies", "text": SAMPLE_TEXT},
        headers=auth,
    ).json()
    assert client.delete(f"/api/documents/{created['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/collections/{collection['id']}/documents", headers=auth).json() == []


def test_oversized_document_returns_413(client, auth, collection):
    huge = "word " * 200_000  # ~1 MB, over the 400k char limit
    response = client.post(
        f"/api/collections/{collection['id']}/documents",
        json={"title": "Huge", "text": huge},
        headers=auth,
    )
    assert response.status_code == 413


def test_upload_text_file(client, auth, collection):
    response = client.post(
        f"/api/collections/{collection['id']}/upload",
        files={"file": ("policy.txt", SAMPLE_TEXT.encode(), "text/plain")},
        headers=auth,
    )
    assert response.status_code == 201
    assert response.json()["source_type"] == "text"


def test_upload_unsupported_type_returns_415(client, auth, collection):
    response = client.post(
        f"/api/collections/{collection['id']}/upload",
        files={"file": ("image.png", b"\x89PNG\r\n", "image/png")},
        headers=auth,
    )
    assert response.status_code == 415


def test_upload_empty_file_returns_422(client, auth, collection):
    response = client.post(
        f"/api/collections/{collection['id']}/upload",
        files={"file": ("empty.txt", b"   ", "text/plain")},
        headers=auth,
    )
    assert response.status_code == 422


# ── Query ────────────────────────────────────────────────────────────────────


def _seed(client, auth, collection):
    client.post(
        f"/api/collections/{collection['id']}/documents",
        json={"title": "Policies", "text": SAMPLE_TEXT},
        headers=auth,
    )


def test_query_answers_from_ingested_document(client, auth, collection):
    _seed(client, auth, collection)
    response = client.post(
        "/api/query",
        json={"question": "how long do refunds take", "collection_id": collection["id"]},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is True
    assert "30 days" in body["answer"]
    assert body["citations"]


def test_query_abstains_on_unrelated_question(client, auth, collection):
    _seed(client, auth, collection)
    response = client.post(
        "/api/query",
        json={"question": "what is the capital of Mongolia", "collection_id": collection["id"]},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["answered"] is False


def test_query_without_collection_searches_everything(client, auth, collection):
    _seed(client, auth, collection)
    response = client.post(
        "/api/query", json={"question": "how long do refunds take"}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["answered"] is True


def test_query_with_no_collections_returns_404(client, auth):
    response = client.post("/api/query", json={"question": "anything"}, headers=auth)
    assert response.status_code == 404


def test_query_requires_auth(client):
    assert client.post("/api/query", json={"question": "anything"}).status_code == 401


def test_query_rejects_blank_question(client, auth, collection):
    response = client.post(
        "/api/query",
        json={"question": "   ", "collection_id": collection["id"]},
        headers=auth,
    )
    assert response.status_code == 422


def test_query_rejects_bad_limit(client, auth, collection):
    response = client.post(
        "/api/query",
        json={"question": "refund", "collection_id": collection["id"], "limit": 999},
        headers=auth,
    )
    assert response.status_code == 422


def test_explain_returns_ranker_positions(client, auth, collection):
    _seed(client, auth, collection)
    response = client.post(
        "/api/query/explain",
        json={"question": "refund", "collection_id": collection["id"]},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["results"]


def test_query_is_logged_in_stats(client, auth, collection):
    _seed(client, auth, collection)
    client.post(
        "/api/query",
        json={"question": "how long do refunds take", "collection_id": collection["id"]},
        headers=auth,
    )
    stats = client.get("/api/stats", headers=auth).json()
    assert stats["questions_asked"] == 1
    assert stats["questions_answered"] == 1


def test_unanswered_questions_appear_in_stats(client, auth, collection):
    _seed(client, auth, collection)
    client.post(
        "/api/query",
        json={"question": "what is the capital of Mongolia", "collection_id": collection["id"]},
        headers=auth,
    )
    stats = client.get("/api/stats", headers=auth).json()
    assert len(stats["unanswered_questions"]) == 1


# ── Health and headers ───────────────────────────────────────────────────────


def test_health_reports_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_security_headers_are_present(client):
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers


def test_index_serves_the_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Khazana" in response.text


def test_unknown_route_returns_404(client):
    assert client.get("/api/nope").status_code == 404
