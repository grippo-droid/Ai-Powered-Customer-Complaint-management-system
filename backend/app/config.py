"""
Application configuration.

Every secret and environment-specific value enters the app through this
file and nowhere else. Nothing in the codebase reads os.environ directly,
so there is exactly one place to look when something is misconfigured.

Values are loaded from `backend/.env` (git-ignored). The committed
`backend/.env.example` documents which keys are required.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[0] = app/ , parents[1] = backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    """Typed view of the environment. Missing required keys fail at startup."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,   # GROQ_API_KEY in .env -> groq_api_key here
        extra="ignore",
    )

    # --- Groq ---
    # Server-side only. This value is never returned by any endpoint and
    # never reaches the React bundle; the frontend talks to our API, and
    # our API talks to Groq.
    groq_api_key: str = Field(..., description="Groq API key, from console.groq.com/keys")
    # Defaults to the model the assignment mandates. Groq has since
    # decommissioned it, so .env.example ships llama-3.3-70b-versatile - the
    # alternative the brief itself names - and the documented setup path
    # (copy .env.example to .env) therefore runs on a live model.
    groq_model: str = Field("gemma2-9b-it", description="Model used by every LangGraph node")

    # --- Database ---
    database_url: str = Field(
        ...,
        description="SQLAlchemy URL, e.g. mysql+pymysql://user:pass@localhost:3306/complaint_qms",
    )

    # --- HTTP ---
    # Stored as a raw string, not List[str], on purpose: pydantic-settings
    # tries to JSON-decode complex types from env vars, so a plain
    # comma-separated value would raise a parse error. We split it ourselves
    # in `cors_origin_list` below.
    cors_origins: str = Field(
        "http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated list of allowed browser origins",
    )

    # --- Uploads ---
    max_upload_bytes: int = Field(10 * 1024 * 1024, description="10 MB cap on uploaded documents")

    @property
    def cors_origin_list(self) -> List[str]:
        """Explicit origins for CORSMiddleware. Never '*' - we send credentials-bearing
        requests and a wildcard would let any site call this API from a browser."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    Cached accessor - the .env file is read once per process.

    Wrapped so that a missing key produces an actionable message instead of
    a bare pydantic ValidationError during app import.
    """
    try:
        return Settings()
    except Exception as exc:  # noqa: BLE001 - we re-raise with better context
        raise RuntimeError(
            f"Configuration error: {exc}\n\n"
            f"Expected an environment file at: {ENV_FILE}\n"
            f"Copy backend/.env.example to backend/.env and fill in "
            f"GROQ_API_KEY and DATABASE_URL."
        ) from exc


settings = get_settings()
