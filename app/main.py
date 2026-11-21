"""FastAPI application: middleware, routers, static UI.

Rate limiting is a small in-process token bucket rather than a dependency.
A decorator-based limiter (slowapi) wraps the view function, and FastAPI then
resolves the endpoint's string type annotations against the *wrapper's* module
globals — which is not where the schemas live. The result is that every
request body is silently reinterpreted as a query parameter and every POST
returns 422. Middleware has none of that problem because it never touches the
signature.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app import __version__
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import Chunk
from app.routers import auth, documents, search
from app.schemas import HealthOut

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Khazana",
    description=(
        "A private knowledge base that answers with citations — and says so "
        "when it cannot answer."
    ),
    version=__version__,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# --- Rate limiting ----------------------------------------------------------

# Requests per window, per client IP. Login and register are tighter because
# they are the endpoints worth brute-forcing.
_LIMITS: dict[str, tuple[int, int]] = {
    "/api/auth/login": (10, 60),
    "/api/auth/register": (5, 60),
    "/api/query": (60, 60),
    "/api/query/explain": (30, 60),
}
_DEFAULT_LIMIT = (240, 60)

_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_and_headers(request: Request, call_next):
    path = request.url.path
    limit, window = _LIMITS.get(path, _DEFAULT_LIMIT)

    if request.method != "OPTIONS" and path.startswith("/api/"):
        key = (_client_key(request), path)
        now = time.monotonic()
        bucket = _buckets[key]

        while bucket and now - bucket[0] > window:
            bucket.popleft()

        if len(bucket) >= limit:
            retry_after = int(window - (now - bucket[0])) + 1
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests — slow down and try again."},
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)

    response = await call_next(request)

    # Security headers. The CSP allows the inline styles the single-page UI
    # uses but nothing else, so an injected script cannot execute.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# --- Routers ----------------------------------------------------------------

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(search.router)


@app.get("/api/health", response_model=HealthOut, tags=["health"])
def health() -> HealthOut:
    """Liveness plus a real database read.

    A health check that only returns a constant tells you the process is up,
    not that it can serve. This one counts chunks, so a broken database
    connection surfaces here rather than on the first user request.
    """
    db_status = "ok"
    indexed = 0
    try:
        with SessionLocal() as db:
            indexed = int(db.scalar(select(func.count()).select_from(Chunk)) or 0)
    except Exception:
        db_status = "error"

    return HealthOut(
        status="ok" if db_status == "ok" else "degraded",
        version=__version__,
        environment=settings.environment,
        database=db_status,
        indexed_chunks=indexed,
    )


# --- Static UI --------------------------------------------------------------

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse("app/static/index.html")
