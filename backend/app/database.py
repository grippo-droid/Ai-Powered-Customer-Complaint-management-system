"""
Database engine, session factory and the FastAPI dependency.

The API layer itself holds no state: every request opens a short-lived
SQLAlchemy session, reads the conversation row it needs from MySQL, does
its work, commits and closes. That is what lets the chat remember an
earlier turn without the server keeping anything in memory.

All access goes through the ORM (see models.py). There is no raw SQL
string building anywhere in this project.
"""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base that ConversationSession and Complaint inherit from."""


engine = create_engine(
    settings.database_url,
    # MySQL closes idle connections after `wait_timeout` (8 hours by
    # default). Without these two settings the first request after a long
    # idle period fails with "MySQL server has gone away".
    pool_pre_ping=True,   # cheap SELECT 1 before handing out a connection
    pool_recycle=3600,    # proactively drop connections older than an hour
    pool_size=5,
    max_overflow=10,
    echo=False,           # set True to watch the generated SQL during the demo
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # keep ORM objects readable after commit()
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency: yields a session and always closes it.

    Usage:  def endpoint(db: Session = Depends(get_db)) -> ...

    Endpoints call db.commit() explicitly when they mean to persist. On an
    unhandled exception we roll back so a half-written session row is never
    left behind.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """
    Create any missing tables.

    Called once from main.py's startup hook. Importing app.models here (not
    at module top) both avoids a circular import and guarantees every ORM
    class is registered on Base.metadata before create_all runs.

    create_all only creates what is absent - it never alters or drops an
    existing table. A real deployment would use Alembic migrations; for an
    assignment this keeps setup to a single command.
    """
    from app import models  # noqa: F401  (imported for its side effect)

    Base.metadata.create_all(bind=engine)
