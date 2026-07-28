"""
SQLAlchemy ORM models - the three MySQL tables.

    conversation_sessions : the LIVE, in-progress intake conversation.
                            One row per browser session. Holds the working
                            form state and the full chat history as JSON.

    complaints            : the PERMANENT QMS record. One row is written
                            when the user clicks "Commit to QMS Ledger".
                            Flat columns, queryable, never mutated by the AI.

    users                 : who is allowed to do the above, and which of
                            them may sign a complaint off into the ledger.

The split between the first two is the point: the AI is free to rewrite the
session row on every turn, but it can only ever *append* to the ledger, and
only when a human presses the button. That mirrors how a real pharma QMS
separates a draft intake from a controlled record.

All access is through this ORM. No raw SQL strings anywhere in the project.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import JSON, Boolean, DateTime, Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    """Naive UTC timestamp.

    MySQL DATETIME columns do not store a timezone, so we drop the tzinfo
    rather than let SQLAlchemy silently discard it. Using this instead of
    the deprecated datetime.utcnow() keeps us clean on Python 3.12+.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserRole(str, enum.Enum):
    """
    Who may do what.

    Two roles, because the system has exactly one privileged action: writing
    a complaint into the permanent ledger. A reviewer does the intake work -
    uploading documents, correcting the AI, editing fields. A lead signs it
    off. Anything finer-grained would be invented complexity for a project
    with one gated operation.

    Inheriting from `str` matters: the member compares equal to its own
    string, so it survives a round trip through JSON, a JWT payload and
    Pydantic without a custom encoder anywhere.
    """

    QA_REVIEWER = "qa_reviewer"
    QA_LEAD = "qa_lead"


class User(Base):
    """
    An account.

    Only the bcrypt hash is stored - the plaintext password exists in this
    process for the few microseconds it takes to hash or verify it, and is
    never written to the database, a log line, or an API response. See
    app/security.py, which is the only module that handles it at all.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Unique so signup cannot create two accounts for one address, and indexed
    # because login looks a user up by email on every single request to
    # /auth/login. The uniqueness is enforced by the DATABASE, not just by a
    # check in the endpoint: two concurrent signups for the same address would
    # both pass an application-level "does this email exist?" test and then
    # both insert. The constraint is what actually makes that impossible.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # A bcrypt hash is 60 characters. 255 leaves room to migrate to a
    # different algorithm later without an ALTER TABLE, since passlib encodes
    # the scheme into the string itself.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # `values_callable` is not optional here. By default SQLAlchemy stores the
    # enum's NAME ("QA_REVIEWER"), not its value, so the MySQL column would
    # hold values that no longer match the strings used in the JWT payload and
    # the React UI. This makes the database column ENUM('qa_reviewer','qa_lead')
    # exactly as written above.
    #
    # A native ENUM rather than a plain String because, unlike complaint
    # severity, this genuinely is a closed set: an unrecognised role is a bug,
    # and the database should be the thing that refuses it. The cost is that
    # adding a third role later needs an ALTER TABLE - a fair trade for a
    # column that gates who can sign off a regulated record.
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
        default=UserRole.QA_REVIEWER,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        # Deliberately no hash, not even truncated. A __repr__ ends up in
        # debuggers, tracebacks and log lines, which is exactly where a
        # password hash should never appear.
        return f"<User id={self.id} email={self.email!r} role={self.role.value!r}>"


class ConversationSession(Base):
    """
    One intake conversation.

    `form_state` and `messages` are MySQL JSON columns. They hold, verbatim,
    the serialised ComplaintFormState and list[ChatMessage] from schemas.py -
    so the shape the LLM produces, the shape stored in MySQL, and the shape
    the React form renders are all the same shape. No mapping layer.

    IMPORTANT - mutation gotcha:
        SQLAlchemy does NOT detect in-place edits of a JSON column.

            session.form_state["fields"]["batch_number"] = "X"   # LOST on commit
            session.form_state = new_dict                        # persisted

        Always REASSIGN the whole attribute. The API layer does exactly that,
        rebuilding the dict from the Pydantic model each turn.
    """

    __tablename__ = "conversation_sessions"

    # UUID4 string, generated by the API. Not an auto-increment int: the id
    # is handed to the browser, and sequential ids would let anyone walk
    # other people's in-progress complaints by guessing.
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # Serialised ComplaintFormState: {"fields": {...}, "risk_assessment": {...}}
    form_state: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    # Serialised list[ChatMessage]: [{"role": ..., "content": ..., "timestamp": ...}]
    # This is what makes the conversation stateful across requests - the
    # LangGraph run reads it back in as prior context on every turn.
    messages: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )

    # Flipped by the commit endpoint. Guards against committing twice and
    # writing a duplicate complaint into the ledger.
    committed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:  # helpful in the shell during the demo
        return (
            f"<ConversationSession {self.session_id} "
            f"msgs={len(self.messages or [])} committed={self.committed}>"
        )


class Complaint(Base):
    """
    A committed complaint - the permanent record.

    Columns are flat (not JSON) so the ledger is queryable: "every Critical
    complaint for batch BN-2024-0783", "all complaints this month". That is
    what a QMS actually needs and what JSON columns would make painful.

    Date fields are String, not Date, on purpose. schemas.py normalises dates
    to ISO where it can but deliberately KEEPS unparseable values rather than
    dropping them - a Date column would force us to throw that data away. In
    a regulated context, showing the reviewer a malformed date from the source
    document beats silently storing NULL.
    """

    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Section 1: Origin & Customer Details ---
    complaint_source: Mapped[str | None] = mapped_column(String(64))
    customer_name: Mapped[str | None] = mapped_column(String(255))

    # --- Section 2: Product & Batch Identification ---
    product_name: Mapped[str | None] = mapped_column(String(255))
    product_strength: Mapped[str | None] = mapped_column(String(128))
    batch_number: Mapped[str | None] = mapped_column(String(128))
    affected_quantity: Mapped[str | None] = mapped_column(String(128))
    manufacturing_date: Mapped[str | None] = mapped_column(String(64))
    expiry_date: Mapped[str | None] = mapped_column(String(64))

    # --- Section 3: Complaint Details ---
    complaint_type: Mapped[str | None] = mapped_column(String(128))
    complaint_date: Mapped[str | None] = mapped_column(String(64))
    complaint_description: Mapped[str | None] = mapped_column(Text)

    # --- Section 4: AI Copilot Risk Assessment ---
    # One-sentence ticket title, so GET /complaints is scannable without
    # reading each full description.
    complaint_summary: Mapped[str | None] = mapped_column(String(512))

    # Stored as written by the AI at commit time. Severity is free text, not
    # an enum column: pharma QA vocabulary varies by company SOP, and pinning
    # it to three values would make the model's judgement lossy.
    severity_suggested: Mapped[str | None] = mapped_column(String(64))
    suggested_next_action: Mapped[str | None] = mapped_column(String(512))
    initial_risk_assessment: Mapped[str | None] = mapped_column(Text)

    # AI suggestions for the investigation, not conclusions. Stored so the
    # ledger records what the copilot proposed at intake time, which is what
    # a QA reviewer would later audit against what was actually done.
    root_cause_suggestion: Mapped[str | None] = mapped_column(Text)
    capa_recommendation: Mapped[str | None] = mapped_column(Text)

    # --- Provenance ---
    # Which conversation produced this record. Kept for traceability, which
    # a real QMS audit trail would require. Not a ForeignKey: sessions are
    # transient working data and may be cleaned up, but the ledger entry
    # must survive that.
    source_session_id: Mapped[str | None] = mapped_column(String(36))

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        # Newest-first listing on GET /complaints.
        Index("ix_complaints_created_at", "created_at"),
        # Supports "have we seen this batch before?" - the lookup a duplicate
        # detection or trending feature would need.
        Index("ix_complaints_batch_number", "batch_number"),
    )

    def __repr__(self) -> str:
        return (
            f"<Complaint id={self.id} product={self.product_name!r} "
            f"batch={self.batch_number!r} severity={self.severity_suggested!r}>"
        )
