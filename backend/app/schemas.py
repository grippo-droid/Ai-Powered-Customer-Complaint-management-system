"""
All Pydantic models for the application, in one file.

Grouped into four blocks:

  1. DOMAIN      - the complaint form itself and the AI risk assessment.
  2. LLM OUTPUT  - the exact shapes we force the LLM to return.
  3. AGENT STATE - the LangGraph StateGraph channel schema.
  4. API         - request / response bodies for FastAPI.

The DOMAIN models do double duty: their JSON schema (generated from the
Field descriptions below) is injected into the LLM prompt, and the LLM's
reply is validated back through the same model. That is what makes the
extraction "structured output" rather than string parsing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# 1. DOMAIN MODELS
# ---------------------------------------------------------------------------

# Date strings arrive from the LLM in whatever format the source document
# used. We normalise to ISO (YYYY-MM-DD) so <input type="date"> can bind to
# them, but we never throw away a value we cannot parse - a wrong-looking
# date shown to the QA reviewer is better than a silently dropped one.
_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y",
    "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d",
    "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
    "%d-%b-%Y", "%d-%B-%Y",
)


def normalize_date(value: Optional[str]) -> Optional[str]:
    """Best-effort conversion of a free-text date to YYYY-MM-DD."""
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if not raw or raw.lower() in {"n/a", "na", "none", "null", "unknown", "-"}:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    # Month-precision dates are common on pharma packaging ("MFG 03/2024").
    for fmt in ("%m/%Y", "%m-%Y", "%b %Y", "%B %Y", "%Y-%m"):
        try:
            return datetime.strptime(raw, fmt).date().replace(day=1).isoformat()
        except ValueError:
            continue
    return raw  # unparseable - hand it to the reviewer as-is


class ComplaintFields(BaseModel):
    """
    The 11 data fields of the complaint form (sections 1-3 of the left panel).

    Every field is Optional. This single model is used for three different
    jobs, and optionality is what allows that:

      * a full extraction  -> the LLM fills in everything it can find
      * a correction patch -> the LLM fills in ONLY the mentioned fields, and
                              `model_dump(exclude_unset=True)` gives us exactly
                              those keys back for the diff-merge
      * the live form state stored on the session
    """

    model_config = ConfigDict(extra="ignore")  # never crash on a stray LLM key

    # --- Section 1: Origin & Customer Details ---
    complaint_source: Optional[str] = Field(
        None, description="Where the complaint came in from. Exactly 'Pharmacy' or 'Email'."
    )
    customer_name: Optional[str] = Field(
        None, description="Name of the complaining customer, pharmacy, hospital or distributor."
    )

    # --- Section 2: Product & Batch Identification ---
    product_name: Optional[str] = Field(
        None, description="Commercial or generic name of the product complained about."
    )
    product_strength: Optional[str] = Field(
        None, description="Strength or grade, e.g. '500 mg', '10 mg/mL', 'USP Grade'."
    )
    batch_number: Optional[str] = Field(
        None, description="Batch or lot number exactly as printed, e.g. 'BN-2024-0783'."
    )
    affected_quantity: Optional[str] = Field(
        None, description="Quantity affected including its unit, e.g. '12 vials', '3 strips', '5 kg'."
    )
    manufacturing_date: Optional[str] = Field(
        None, description="Manufacturing date. Return as YYYY-MM-DD if possible."
    )
    expiry_date: Optional[str] = Field(
        None, description="Expiry date. Return as YYYY-MM-DD if possible."
    )

    # --- Section 3: Complaint Details ---
    complaint_type: Optional[str] = Field(
        None,
        description=(
            "Short pharma-QA category for the defect, e.g. 'Particulate Matter', "
            "'Packaging Defect', 'Label Mix-up', 'Discolouration', 'Short Shipment', "
            "'Efficacy Concern', 'Adverse Event'."
        ),
    )
    complaint_date: Optional[str] = Field(
        None, description="Date the complaint was raised. Return as YYYY-MM-DD if possible."
    )
    complaint_description: Optional[str] = Field(
        None,
        description=(
            "Factual 2-4 sentence description of what the customer observed. "
            "Use the customer's own details; do not speculate about root cause."
        ),
    )

    @field_validator("manufacturing_date", "expiry_date", "complaint_date", mode="before")
    @classmethod
    def _coerce_dates(cls, v: Any) -> Optional[str]:
        return normalize_date(v if v is None else str(v))

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        """LLMs love returning "" / "N/A" / "not specified" instead of null."""
        if isinstance(v, str):
            cleaned = v.strip()
            if cleaned.lower() in {
                "", "n/a", "na", "none", "null", "unknown",
                "not specified", "not provided", "not mentioned",
            }:
                return None
            return cleaned
        return v


class RiskAssessment(BaseModel):
    """Section 4 of the form - written by the AI Copilot, never typed by hand."""

    model_config = ConfigDict(extra="ignore")

    severity_suggested: str = Field(
        ...,
        description=(
            "Suggested severity as a pharma-QA practitioner would phrase it, "
            "e.g. 'Minor', 'Major', 'Critical'. Free text, not a fixed enum."
        ),
    )
    suggested_next_action: str = Field(
        ...,
        description=(
            "One short actionable phrase, e.g. "
            "'Route to QA Investigation & Issue Replacement'."
        ),
    )
    initial_risk_assessment: str = Field(
        ...,
        description=(
            "1-3 sentence justification referencing the specific defect, product "
            "and batch. Explains WHY this severity was suggested."
        ),
    )


class ComplaintFormState(BaseModel):
    """Everything the left panel renders: the 11 fields plus the AI assessment."""

    fields: ComplaintFields = Field(default_factory=ComplaintFields)
    risk_assessment: Optional[RiskAssessment] = None


class ChatMessage(BaseModel):
    """One turn of the conversation, persisted on the session row."""

    role: Literal["user", "assistant"]
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# 2. LLM STRUCTURED-OUTPUT MODELS
# ---------------------------------------------------------------------------


class IntentClassification(BaseModel):
    """Output of the classify_intent node."""

    model_config = ConfigDict(extra="ignore")

    intent: Literal["new_complaint", "correction"] = Field(
        ...,
        description=(
            "'new_complaint' if the message is a fresh complaint document, email "
            "or narrative to extract from. 'correction' if it amends, adds to or "
            "asks a question about the complaint already on the form."
        ),
    )
    reasoning: str = Field(..., description="One short sentence explaining the choice.")


# A correction patch is structurally identical to ComplaintFields; the
# difference is purely in how we read it (exclude_unset). Aliasing it keeps
# the orchestrator's intent obvious at the call site.
ComplaintFieldPatch = ComplaintFields


# ---------------------------------------------------------------------------
# 3. LANGGRAPH STATE SCHEMA
# ---------------------------------------------------------------------------


class AgentState(TypedDict, total=False):
    """
    The channel schema for the StateGraph in agent/orchestrator.py.

    Flow of ownership - which node writes which key:

      (caller)          user_message, fields, risk_assessment, history
      classify_intent   intent, intent_reason
      extract_all       fields, changed_fields, needs_risk
      patch_fields      fields, changed_fields, needs_risk
      risk_assessment   risk_assessment, changed_fields (appends the 3 risk keys)
      format_output     assistant_message, status

    `error` is written by any node that fails unrecoverably; format_output
    turns it into a user-facing chat message instead of a 500.
    """

    # --- input ---
    user_message: str            # pasted text, or text extracted from the upload
    source_label: Optional[str]  # e.g. "complaint_letter.pdf", shown in the chat
    fields: ComplaintFields      # form state BEFORE this turn
    risk_assessment: Optional[RiskAssessment]
    history: List[ChatMessage]

    # --- routing ---
    intent: Literal["new_complaint", "correction"]
    intent_reason: str
    needs_risk: bool             # gate for the risk_assessment node

    # --- output ---
    changed_fields: List[str]    # flat field names the UI should highlight
    assistant_message: str
    status: Literal["pending_triage", "ready_to_commit"]
    error: Optional[str]


# Corrections to these fields change the *defect picture*, so the risk
# assessment must be regenerated. Changing a customer's name or the intake
# channel does not - re-running the LLM there would burn a call and, worse,
# could churn a severity the reviewer has already read.
DEFECT_RELEVANT_FIELDS: frozenset[str] = frozenset(
    {
        "product_name",
        "product_strength",
        "batch_number",
        "affected_quantity",
        "manufacturing_date",
        "expiry_date",
        "complaint_type",
        "complaint_description",
    }
)

RISK_FIELD_NAMES: tuple[str, ...] = (
    "severity_suggested",
    "suggested_next_action",
    "initial_risk_assessment",
)


# ---------------------------------------------------------------------------
# 4. API REQUEST / RESPONSE MODELS
# ---------------------------------------------------------------------------


class SessionCreateResponse(BaseModel):
    session_id: str
    form_state: ComplaintFormState
    status: Literal["pending_triage", "ready_to_commit"] = "pending_triage"
    greeting: str


class MessageResponse(BaseModel):
    """Returned by POST /complaints/session/{id}/message."""

    session_id: str
    form_state: ComplaintFormState
    changed_fields: List[str]        # <- drives the green highlight in the UI
    assistant_message: str
    status: Literal["pending_triage", "ready_to_commit"]
    intent: Optional[str] = None     # surfaced for the demo / debugging
    error: Optional[str] = None


class SessionStateResponse(BaseModel):
    session_id: str
    form_state: ComplaintFormState
    messages: List[ChatMessage]
    status: Literal["pending_triage", "ready_to_commit"]
    committed: bool


class CommitRequest(BaseModel):
    """The user may hand-edit the form before committing; the edits win."""

    form_state: Optional[ComplaintFormState] = None


class ComplaintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    complaint_source: Optional[str] = None
    customer_name: Optional[str] = None
    product_name: Optional[str] = None
    product_strength: Optional[str] = None
    batch_number: Optional[str] = None
    affected_quantity: Optional[str] = None
    manufacturing_date: Optional[str] = None
    expiry_date: Optional[str] = None
    complaint_type: Optional[str] = None
    complaint_date: Optional[str] = None
    complaint_description: Optional[str] = None
    severity_suggested: Optional[str] = None
    suggested_next_action: Optional[str] = None
    initial_risk_assessment: Optional[str] = None
    source_session_id: Optional[str] = None
    created_at: datetime


class ErrorResponse(BaseModel):
    detail: str


def form_state_to_dict(state: ComplaintFormState) -> Dict[str, Any]:
    """Helper used when persisting the session's form state to the JSON column."""
    return state.model_dump(mode="json")
