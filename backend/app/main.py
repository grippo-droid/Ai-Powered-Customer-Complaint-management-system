"""
FastAPI application entry point.

Run it with:

    cd backend
    .venv\\Scripts\\activate
    uvicorn app.main:app --reload

Interactive API docs are then at http://127.0.0.1:8000/docs - useful for the
walkthrough video, since every endpoint can be driven from the browser
without the React app running.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.complaints import router as complaints_router
from app.config import settings
from app.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown.

    Creating tables on startup keeps setup to one command for a reviewer:
    create the empty MySQL database, run uvicorn, done. A production system
    would run Alembic migrations instead of create_all.
    """
    logger.info("Starting up - model=%s", settings.groq_model)
    init_db()
    logger.info("Database tables ready")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="AI-Powered Customer Complaint Management System",
    description=(
        "Pharmaceutical QMS complaint intake. A LangGraph agent extracts complaint "
        "details from documents or chat, generates an initial risk assessment, and "
        "applies conversational corrections to individual fields."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Explicit origins only - never allow_origins=["*"]. A wildcard would let any
# website in the user's browser call this API. The list comes from the
# CORS_ORIGINS environment variable so deployment can change it without a
# code edit.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(complaints_router)


@app.get("/health", tags=["system"])
def health() -> dict:
    """
    Liveness check.

    Reports the configured model but NEVER the API key, and no part of the
    Groq credential is exposed by any endpoint in this application.
    """
    return {"status": "ok", "model": settings.groq_model}
