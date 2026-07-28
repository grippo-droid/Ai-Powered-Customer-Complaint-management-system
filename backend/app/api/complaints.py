"""
The HTTP layer - four endpoints plus a session-restore helper.

    POST /complaints/session               start (or resume) a conversation
    GET  /complaints/session/{id}          reload a conversation after refresh
    POST /complaints/session/{id}/message  send text or a file, run the graph
    POST /complaints/session/{id}/commit   write the record to the ledger
    GET  /complaints                       list committed complaints

This layer is deliberately thin. Its whole job on a message turn is:

    load state from MySQL  ->  run the graph  ->  save state to MySQL  ->  reply

No conversation state is held in a module variable, so the process can be
restarted mid-conversation and the chat still remembers. That is what the
"stateful conversation" requirement actually means in practice.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.orchestrator import run_turn
from app.agent.prompts import label_for
from app.config import settings
from app.database import get_db
from app.models import Complaint, ConversationSession
from app.schemas import (
    ChatMessage,
    CommitRequest,
    ComplaintFormState,
    ComplaintOut,
    MessageResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionStateResponse,
)
from app.services.duplicates import check_for_duplicates
from app.services.file_parser import FileParseError, extract_text_from_upload, validate_upload
from app.services.rate_limit import RateLimitExceeded, check_rate_limit, client_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/complaints", tags=["complaints"])

GREETING = (
    "Upload a complaint document (PDF, DOCX, TXT or EML) or paste the complaint "
    "text, and I'll extract the details and populate the form for you."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_session(db: Session, session_id: str) -> ConversationSession:
    record = db.get(ConversationSession, session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That session no longer exists. Please start a new complaint.",
        )
    return record


def _form_state_of(record: ConversationSession) -> ComplaintFormState:
    """Parse the JSON column back into the typed model the graph expects."""
    return ComplaintFormState.model_validate(record.form_state or {})


def _history_of(record: ConversationSession) -> List[ChatMessage]:
    return [ChatMessage.model_validate(m) for m in (record.messages or [])]


def _save(
    db: Session,
    record: ConversationSession,
    form_state: ComplaintFormState,
    history: List[ChatMessage],
) -> None:
    """
    Persist the turn.

    Both attributes are REASSIGNED, never mutated in place: SQLAlchemy does
    not track in-place edits to a JSON column, so `record.messages.append(...)`
    would be silently lost on commit. See the note in models.py.
    """
    record.form_state = form_state.model_dump(mode="json")
    record.messages = [m.model_dump(mode="json") for m in history]
    db.commit()


# ---------------------------------------------------------------------------
# POST /complaints/session
# ---------------------------------------------------------------------------


@router.post("/session", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: Optional[SessionCreateRequest] = None,
    db: Session = Depends(get_db),
) -> SessionCreateResponse:
    """
    Start a conversation, or resume the one the client already has.

    Idempotent by client-supplied id: React's StrictMode double-invokes
    effects in development, and a user may refresh at any time. Without this
    check each of those would strand a fresh empty session row in MySQL.
    """
    if payload and payload.session_id:
        existing = db.get(ConversationSession, payload.session_id)
        if existing is not None and not existing.committed:
            return SessionCreateResponse(
                session_id=existing.session_id,
                form_state=_form_state_of(existing),
                status="pending_triage",
                greeting=GREETING,
            )

    record = ConversationSession(
        session_id=str(uuid.uuid4()),
        form_state=ComplaintFormState().model_dump(mode="json"),
        messages=[],
    )
    db.add(record)
    db.commit()

    logger.info("Created session %s", record.session_id)
    return SessionCreateResponse(
        session_id=record.session_id,
        form_state=ComplaintFormState(),
        status="pending_triage",
        greeting=GREETING,
    )


# ---------------------------------------------------------------------------
# GET /complaints/session/{id}
# ---------------------------------------------------------------------------


@router.get("/session/{session_id}", response_model=SessionStateResponse)
def get_session(
    session_id: str = Path(..., description="Session UUID"),
    db: Session = Depends(get_db),
) -> SessionStateResponse:
    """Rehydrate the whole UI after a page refresh - form state and full chat."""
    record = _load_session(db, session_id)
    form_state = _form_state_of(record)

    return SessionStateResponse(
        session_id=record.session_id,
        form_state=form_state,
        messages=_history_of(record),
        status="ready_to_commit" if record.committed else _status_for(form_state),
        committed=record.committed,
    )


def _status_for(form_state: ComplaintFormState) -> str:
    """Same rule the graph applies, reused so a refresh can't disagree with it."""
    from app.agent.orchestrator import _compute_status

    return _compute_status(form_state.fields, form_state.risk_assessment)


# ---------------------------------------------------------------------------
# POST /complaints/session/{id}/message
# ---------------------------------------------------------------------------


@router.post("/session/{session_id}/message", response_model=MessageResponse)
async def send_message(
    request: Request,
    session_id: str = Path(..., description="Session UUID"),
    message: Optional[str] = Form(None, description="Pasted complaint text or a correction"),
    file: Optional[UploadFile] = File(None, description="PDF, DOCX, TXT or EML"),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """
    One conversational turn: text or a file in, updated form state out.

    Sent as multipart/form-data so a single endpoint handles both inputs -
    the frontend always posts FormData and simply omits whichever part is
    unused.

    This is the only rate-limited endpoint, because it is the only one that
    spends Groq quota. The limit is off by default and switched on only for
    the public deployment - see app/services/rate_limit.py.
    """
    record = _load_session(db, session_id)

    if record.committed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This complaint has already been committed to the ledger. "
            "Please start a new complaint.",
        )

    # Checked here, before any parsing work, so a client that is over its
    # allowance is turned away cheaply. This does mean an upload rejected for
    # being the wrong type still costs the caller a slot; that is intentional,
    # otherwise repeated bad uploads would be an unmetered way to keep the
    # server busy.
    try:
        check_rate_limit(client_key(request), settings.rate_limit_per_hour)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    text = (message or "").strip()
    source_label: Optional[str] = None
    history = _history_of(record)

    # --- File upload path -------------------------------------------------
    if file is not None and file.filename:
        source_label = file.filename
        try:
            # Check extension and size BEFORE reading the body. Starlette has
            # already parsed the multipart request and populated .size, so an
            # oversized or unsupported upload is rejected without ever being
            # pulled into this process as a bytes object.
            validate_upload(file.filename, file.size or 0)

            raw = await file.read()
            text = extract_text_from_upload(file.filename, raw)
        except FileParseError as exc:
            # A parse failure is a conversation event, not a server error.
            # It is recorded in the history and shown in the chat, so the
            # user sees exactly why their upload did not work.
            history.append(ChatMessage(role="user", content=f"[Uploaded {file.filename}]"))
            history.append(ChatMessage(role="assistant", content=str(exc)))
            form_state = _form_state_of(record)
            _save(db, record, form_state, history)

            logger.info("Upload rejected for session %s: %s", session_id, exc)
            return MessageResponse(
                session_id=session_id,
                form_state=form_state,
                changed_fields=[],
                assistant_message=str(exc),
                status=_status_for(form_state),
                error=str(exc),
            )

    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send either a message or a file.",
        )

    # --- Run the graph ----------------------------------------------------
    form_state = _form_state_of(record)
    try:
        result = run_turn(
            user_message=text,
            form_state=form_state,
            history=history,
            source_label=source_label,
        )
    except Exception as exc:  # noqa: BLE001 - last line of defence
        # LLM failures are already handled inside the graph and arrive as a
        # normal reply. Anything reaching here is a genuine bug, so log the
        # detail and return something generic rather than leaking internals.
        logger.exception("Graph execution failed for session %s", session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Something went wrong while processing that message. Please try again.",
        ) from exc

    new_state: ComplaintFormState = result["form_state"]
    assistant_message = result["assistant_message"]

    # --- Duplicate complaint detection ------------------------------------
    # Only on a NEW complaint. On a correction the reviewer has already seen
    # this notice for the batch, and repeating it on every small edit would
    # train them to ignore it.
    duplicate_notice: Optional[str] = None
    if result.get("intent") == "new_complaint":
        duplicate_notice = check_for_duplicates(db, new_state.fields.batch_number)
        if duplicate_notice:
            assistant_message = f"{assistant_message} {duplicate_notice}"

    history.append(
        ChatMessage(role="user", content=f"[Uploaded {source_label}]" if source_label else text)
    )
    history.append(ChatMessage(role="assistant", content=assistant_message))
    _save(db, record, new_state, history)

    return MessageResponse(
        session_id=session_id,
        form_state=new_state,
        changed_fields=result["changed_fields"],
        assistant_message=assistant_message,
        status=result["status"],
        intent=result.get("intent"),
        error=result.get("error"),
        duplicate_notice=duplicate_notice,
    )


# ---------------------------------------------------------------------------
# POST /complaints/session/{id}/commit
# ---------------------------------------------------------------------------


@router.post("/session/{session_id}/commit", response_model=ComplaintOut, status_code=201)
def commit_session(
    session_id: str = Path(..., description="Session UUID"),
    payload: Optional[CommitRequest] = None,
    db: Session = Depends(get_db),
) -> Complaint:
    """
    Write the working session into the permanent complaints ledger.

    The client may send the form state it currently displays. If it does,
    that wins: the reviewer can hand-correct anything the AI produced before
    committing, and a human edit must never be silently discarded in favour
    of the AI's version.
    """
    record = _load_session(db, session_id)

    if record.committed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This complaint has already been committed to the ledger.",
        )

    form_state = (payload.form_state if payload and payload.form_state else None) or _form_state_of(
        record
    )
    fields = form_state.fields
    risk = form_state.risk_assessment

    missing = [name for name in ("product_name", "batch_number") if not getattr(fields, name)]
    if missing:
        names = " and ".join(label_for(name) for name in missing)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot commit: {names} {'is' if len(missing) == 1 else 'are'} required.",
        )

    complaint = Complaint(
        **fields.model_dump(),
        complaint_summary=risk.complaint_summary if risk else None,
        severity_suggested=risk.severity_suggested if risk else None,
        suggested_next_action=risk.suggested_next_action if risk else None,
        initial_risk_assessment=risk.initial_risk_assessment if risk else None,
        root_cause_suggestion=risk.root_cause_suggestion if risk else None,
        capa_recommendation=risk.capa_recommendation if risk else None,
        source_session_id=record.session_id,
    )
    db.add(complaint)

    # Mark the draft closed in the same transaction as the ledger insert, so
    # the two can never disagree about whether this complaint was filed.
    record.committed = True
    record.form_state = form_state.model_dump(mode="json")

    db.commit()
    db.refresh(complaint)

    logger.info("Committed complaint %s from session %s", complaint.id, session_id)
    return complaint


# ---------------------------------------------------------------------------
# GET /complaints
# ---------------------------------------------------------------------------


@router.get("", response_model=List[ComplaintOut])
def list_complaints(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> List[Complaint]:
    """List committed complaints, newest first. ORM query - no SQL strings."""
    statement = (
        select(Complaint).order_by(Complaint.created_at.desc()).limit(limit).offset(offset)
    )
    return list(db.execute(statement).scalars().all())
