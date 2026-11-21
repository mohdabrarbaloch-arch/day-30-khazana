"""Test fixtures.

The app is imported *after* the environment is set, because ``app.config``
reads settings at import time and ``app.db`` builds its engine from them. A
test database in a temp directory keeps the developer's real ``khazana.db``
untouched.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="khazana-test-")
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-validation"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["ENVIRONMENT"] = "test"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import registry  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_database():
    """Fresh schema, empty index cache and empty rate-limit buckets per test.

    The rate limiter keeps its buckets in module-level state, so without this
    the eleventh login test in a file would get a 429 from the ten before it.
    That is correct behaviour in production and pure noise in a test suite.
    """
    from app.main import _buckets

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    registry.clear()
    _buckets.clear()
    yield
    registry.clear()
    _buckets.clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth(client: TestClient) -> dict:
    """Register a user and return headers carrying their token."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": "tester@example.com",
            "display_name": "Test User",
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def collection(client: TestClient, auth: dict) -> dict:
    response = client.post(
        "/api/collections",
        json={"name": "Policies", "description": "Company policy documents"},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()


SAMPLE_TEXT = (
    "Refunds are issued within 30 days of purchase. "
    "To start a refund, contact support with your order number. "
    "Enterprise customers receive a dedicated account manager. "
    "Support hours are 9am to 6pm, Monday through Friday. "
    "Expenses over 50,000 rupees require director approval. "
    "Onboarding takes two weeks for new engineering hires."
)
