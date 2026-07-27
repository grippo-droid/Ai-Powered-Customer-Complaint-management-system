"""
Duplicate complaint detection.

When a new complaint names a batch that already appears in the committed
ledger, the reviewer should know: a second complaint against the same batch
is a trending signal, and in a real QMS it can escalate the investigation
from a one-off to a batch-level issue.

Deliberately NOT an LLM call and NOT a graph node:

  * It is an exact-match lookup on an indexed column. An LLM would be slower,
    cost money, and could be wrong about something a WHERE clause is right
    about every time.
  * The LangGraph pipeline has no database session by design - it takes state
    in and returns state out, which is what lets the API layer keep the
    conversation in MySQL. Handing it a DB session to run one query would
    give that up for very little.

So this lives in the API layer, alongside the other persistence concerns, and
runs after the graph has produced a batch number to look up.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Complaint

# How many prior complaints to name individually before summarising. Three
# keeps the chat nudge readable; a batch with fifteen complaints does not need
# fifteen ids in a sentence.
MAX_LISTED = 3


def find_prior_complaints(db: Session, batch_number: Optional[str]) -> List[Complaint]:
    """
    Committed complaints already on record for this batch, oldest first.

    Uses ix_complaints_batch_number, the index added in models.py for exactly
    this lookup.

    On MySQL the comparison is case-insensitive because the column collation
    is utf8mb4_unicode_ci, so 'zpl-2426-0783' matches 'ZPL-2426-0783'. We also
    strip whitespace here, since a batch number transcribed from a document
    often arrives with a stray trailing space.
    """
    if not batch_number or not batch_number.strip():
        return []

    statement = (
        select(Complaint)
        .where(Complaint.batch_number == batch_number.strip())
        .order_by(Complaint.created_at.asc())
    )
    return list(db.execute(statement).scalars().all())


def format_duplicate_notice(batch_number: str, matches: List[Complaint]) -> Optional[str]:
    """
    Turn the matches into one sentence for the chat, or None if there are none.

    Phrased as information, never as a block: the reviewer decides whether a
    second complaint against a batch is a duplicate report of the same event
    or a genuine second occurrence. Only they have the context to tell.
    """
    if not matches:
        return None

    shown = matches[:MAX_LISTED]
    references = "; ".join(
        f"Complaint #{c.id}, filed {c.created_at.date().isoformat()}" for c in shown
    )
    if len(matches) > MAX_LISTED:
        references += f"; and {len(matches) - MAX_LISTED} more"

    count = len(matches)
    plural = "complaint" if count == 1 else "complaints"
    return (
        f"Note: batch {batch_number} already has {count} prior {plural} "
        f"on record ({references})."
    )


def check_for_duplicates(db: Session, batch_number: Optional[str]) -> Optional[str]:
    """Convenience wrapper: look up the batch and return the notice, if any."""
    matches = find_prior_complaints(db, batch_number)
    if not matches:
        return None
    return format_duplicate_notice(batch_number.strip(), matches)
