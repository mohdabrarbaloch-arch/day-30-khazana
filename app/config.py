"""Application settings, read from the environment only.

No secret has a usable default. ``SECRET_KEY`` in particular has no fallback:
a default signing key in a public repository is how sessions get forged, so
the app refuses to start without one rather than quietly using a known value.
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Khazana"
    environment: str = Field(default="development")
    debug: bool = False

    # Required. No default — see the module docstring.
    secret_key: str = Field(default="")
    access_token_expire_minutes: int = 60 * 12
    algorithm: str = "HS256"

    database_url: str = "sqlite:///./khazana.db"

    # Upload limits. A knowledge base that accepts a 200 MB PDF will run out
    # of memory on the first ingestion, so the ceiling is explicit.
    max_upload_bytes: int = 5 * 1024 * 1024
    max_document_chars: int = 400_000

    # Retrieval tuning, exposed so it can be changed without a code edit.
    chunk_target_chars: int = 900
    chunk_max_chars: int = 1400
    chunk_overlap_sentences: int = 1
    retrieval_limit: int = 8
    min_confidence: float = 0.25

    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    @field_validator("secret_key")
    @classmethod
    def _require_secret(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "SECRET_KEY is not set. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if len(value) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    """Cached settings. Tests clear this cache to inject their own."""
    return Settings()


def generate_secret() -> str:
    """Convenience for the setup instructions."""
    return secrets.token_urlsafe(48)
