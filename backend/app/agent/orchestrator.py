"""
The LangGraph pipeline - the whole agent, in one file.

    START
      |
      v
 classify_intent ................ new complaint, or a correction?
      |
      |---- new_complaint ---> extract_all ....... fill ALL fields
      |---- correction ------> patch_fields ...... fill ONLY what was mentioned
      |---- error -----------> format_output
      |                             ^
      v                             |
  (fields updated)                  |
      |                             |
      |---- defect-relevant ---> assess_risk -----+
      |     field changed          (severity, action, justification)
      |                             |
      |---- otherwise --------------+
                                    v
                              format_output ...... message + changed_fields + status
                                    |
                                    v
                                   END

Read it in this order: helpers, then the five nodes, then the two routers,
then build_graph() where the picture above is actually wired up.

Three LLM calls exist here - classify, extract-or-patch, and risk. Every one
goes through llm.call_structured(), so all three are Pydantic-validated with
a retry. format_output makes no LLM call at all.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph

from app.agent import prompts
from app.agent.llm import LLMError, call_structured
from app.schemas import (
    DEFECT_RELEVANT_FIELDS,
    RISK_FIELD_NAMES,
    AgentState,
    ChatMessage,
    ComplaintFields,
    ComplaintFormState,
    IntentClassification,
    RiskAssessment,
)

logger = logging.getLogger(__name__)

# The form can be committed to the ledger once these are known and the AI has
# produced an assessment. Deliberately short: a QA reviewer can fill the rest
# by hand, but a complaint with no product or batch is not a usable record.
REQUIRED_FOR_COMMIT = ("product_name", "batch_number", "complaint_description")

# Nudged for in the chat when missing, in priority order.
IMPORTANT_FIELDS = (
    "product_name",
    "batch_number",
    "complaint_description",
    "customer_name",
    "expiry_date",
    "affected_quantity",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_empty(fields: ComplaintFields) -> bool:
    return not any(v not in (None, "") for v in fields.model_dump().values())


def _diff(before: ComplaintFields, after: ComplaintFields) -> List[str]:
    """Field names whose value actually changed. This is what the UI highlights."""
    before_values = before.model_dump()
    after_values = after.model_dump()
    return [name for name, value in after_values.items() if value != before_values[name]]


def _merge(current: ComplaintFields, patch: Dict[str, Any]) -> ComplaintFields:
    """
    Apply a correction patch on top of the current fields.

    Re-validates the merged result rather than using model_copy(update=...),
    because model_copy SKIPS validators - the date normaliser in schemas.py
    would never run on a corrected date.
    """
    return ComplaintFields.model_validate({**current.model_dump(), **patch})


def _compute_status(fields: ComplaintFields, risk: Optional[RiskAssessment]) -> str:
    values = fields.model_dump()
    has_required = all(values.get(name) for name in REQUIRED_FOR_COMMIT)
    return "ready_to_commit" if has_required and risk is not None else "pending_triage"


def _quote(value: Any) -> str:
    text = str(value)
    return f"'{text[:60]}...'" if len(text) > 60 else f"'{text}'"


def _join(items: List[str]) -> str:
    """'A', 'A and B', 'A, B and C' - this text is read by a human."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _missing_important(fields: ComplaintFields) -> List[str]:
    values = fields.model_dump()
    return [name for name in IMPORTANT_FIELDS if not values.get(name)]


# ---------------------------------------------------------------------------
# NODE 1 - classify_intent
# ---------------------------------------------------------------------------


def classify_intent(state: AgentState) -> Dict[str, Any]:
    """
    Decide whether this message is a new complaint or a correction.

    Short-circuit: if the form is empty and nothing has been said yet, there
    is nothing to correct. That is a free, always-correct answer, so we skip
    the LLM call entirely - it makes the very first turn measurably faster.
    """
    fields: ComplaintFields = state["fields"]

    if _is_empty(fields) and not state.get("history"):
        return {"intent": "new_complaint", "intent_reason": "The form is empty; nothing to correct."}

    try:
        result = call_structured(
            prompts.CLASSIFY_SYSTEM,
            prompts.build_classify_prompt(state["user_message"], fields),
            IntentClassification,
            max_tokens=200,
        )
    except LLMError as exc:
        # Routed straight to format_output; form state is left untouched.
        return {"error": str(exc)}

    logger.info("Intent=%s (%s)", result.intent, result.reasoning)
    return {"intent": result.intent, "intent_reason": result.reasoning}


# ---------------------------------------------------------------------------
# NODE 2a - extract_all  (new complaint)
# ---------------------------------------------------------------------------


def extract_all(state: AgentState) -> Dict[str, Any]:
    """
    Extract ALL fields from a fresh complaint, replacing whatever was there.

    Replace rather than merge: a new complaint describes a different product
    and batch, so keeping stale values from a previous one would silently
    produce a record that mixes two complaints together.
    """
    before: ComplaintFields = state["fields"]

    try:
        extracted = call_structured(
            prompts.EXTRACT_SYSTEM,
            prompts.build_extract_prompt(state["user_message"], state.get("source_label")),
            ComplaintFields,
            # 11 fields, of which only complaint_description is long. Measured
            # output is 250-350 tokens; 700 is roughly double that. See the
            # note on assess_risk for why this is not set higher.
            max_tokens=700,
        )
    except LLMError as exc:
        return {"error": str(exc)}

    return {
        "fields": extracted,
        "changed_fields": _diff(before, extracted),
        "needs_risk": True,  # a new complaint always gets a fresh assessment
    }


# ---------------------------------------------------------------------------
# NODE 2b - patch_fields  (correction)
# ---------------------------------------------------------------------------


def patch_fields(state: AgentState) -> Dict[str, Any]:
    """
    Update ONLY the fields the user mentioned, and diff-merge them in.

    The mechanism is Pydantic's model_fields_set: because every field on
    ComplaintFields is Optional, model_dump(exclude_unset=True) returns
    exactly the keys the LLM chose to emit - which is exactly the set of
    fields the user talked about. No second model, no hand-maintained
    key list, no re-extraction of anything else.
    """
    before: ComplaintFields = state["fields"]

    try:
        patch = call_structured(
            prompts.PATCH_SYSTEM,
            prompts.build_patch_prompt(state["user_message"], before),
            ComplaintFields,
            max_tokens=600,
        )
    except LLMError as exc:
        return {"error": str(exc)}

    mentioned = patch.model_dump(exclude_unset=True)

    # Drop explicit nulls. The prompt tells the model to omit unmentioned
    # keys entirely, so a null here is far more likely to be model noise than
    # a deliberate "clear this field" - and wrongly erasing verified data is
    # the more damaging mistake of the two.
    mentioned = {name: value for name, value in mentioned.items() if value is not None}

    after = _merge(before, mentioned)

    changed = _diff(before, after)
    return {
        "fields": after,
        "changed_fields": changed,
        # Re-assess only when the defect picture moved. Fixing a customer's
        # name must not churn a severity the reviewer has already read.
        "needs_risk": bool(set(changed) & DEFECT_RELEVANT_FIELDS),
    }


# ---------------------------------------------------------------------------
# NODE 3 - assess_risk
#
# Note the name: LangGraph forbids a node name that collides with a state
# key, and `risk_assessment` is already a channel on AgentState. The node is
# the verb, the state key is the noun.
# ---------------------------------------------------------------------------


def assess_risk(state: AgentState) -> Dict[str, Any]:
    """
    Generate severity, suggested next action and the justification paragraph.

    A failure here is NOT fatal: the extracted fields are already good and
    stay on the form. We record the error so format_output can say the
    assessment is unavailable, and the user can still commit or retry.
    """
    fields: ComplaintFields = state["fields"]

    try:
        assessment = call_structured(
            prompts.RISK_SYSTEM,
            prompts.build_risk_prompt(fields),
            RiskAssessment,
            temperature=0.2,  # a touch of warmth: this text is prose, not data
            # Six fields: summary, severity, action, justification, root cause,
            # CAPA. Measured output is ~310 tokens, ~450 when verbose.
            #
            # HEADROOM IS NOT FREE. Groq bills the max_tokens you RESERVE, not
            # the tokens generated - a 42-token prompt sent with max_tokens=4000
            # is billed as 4042 against the quota. So every unused token of
            # headroom is spent, and on the free tier's 100k tokens/day that is
            # the difference between ~23 and ~31 complaint turns.
            #
            # 800 is ~2x the verbose case: enough that truncation is unlikely,
            # small enough not to waste the budget. If output ever is truncated
            # the JSON fails the brace-balance check and the repair retry fires,
            # so the failure mode is a slower turn, not a broken one.
            max_tokens=800,
        )
    except LLMError as exc:
        logger.warning("Risk assessment failed; keeping extracted fields. %s", exc)
        return {"error": f"I couldn't generate the risk assessment: {exc}"}

    # No reducer on this channel, so append explicitly to what the previous
    # node put there. These three names let the UI highlight section 4 too.
    changed = list(state.get("changed_fields", [])) + list(RISK_FIELD_NAMES)

    return {"risk_assessment": assessment, "changed_fields": changed}


# ---------------------------------------------------------------------------
# NODE 4 - format_output
# ---------------------------------------------------------------------------


def format_output(state: AgentState) -> Dict[str, Any]:
    """
    Build the chat reply, and decide whether the form can be committed.

    Deliberately template-based, with no LLM call. This sentence is the
    user's only confirmation of what happened to their data, so it is built
    from the changed_fields the graph actually computed. An LLM asked to
    summarise its own edits will occasionally claim a change it never made.
    """
    fields: ComplaintFields = state["fields"]
    risk: Optional[RiskAssessment] = state.get("risk_assessment")
    changed: List[str] = state.get("changed_fields", [])
    error: Optional[str] = state.get("error")
    intent = state.get("intent")

    status = _compute_status(fields, risk)

    # --- total failure: nothing was updated ---
    if error and not changed:
        return {"assistant_message": error, "status": status, "changed_fields": []}

    values = fields.model_dump()
    parts: List[str] = []

    if intent == "correction":
        field_changes = [name for name in changed if name not in RISK_FIELD_NAMES]
        if field_changes:
            updates = _join(
                [
                    f"**{prompts.label_for(name)}** to {_quote(values[name])}"
                    for name in field_changes
                ]
            )
            parts.append(f"Got it - I've updated {updates}.")
        else:
            parts.append(
                "I couldn't identify a specific field to update from that message. "
                "Could you tell me the field and its new value? For example: "
                "\"the batch number is BN-2024-0783\"."
            )
        if risk and any(name in changed for name in RISK_FIELD_NAMES):
            parts.append(
                f"Because that changes the defect picture, I've also refreshed the "
                f"risk assessment - severity is now **{risk.severity_suggested}**."
            )
    else:
        filled = sum(1 for value in values.values() if value not in (None, ""))
        source = state.get("source_label")
        origin = f" from **{source}**" if source else ""
        parts.append(
            f"I've extracted the complaint details{origin} and filled in "
            f"{filled} of {len(values)} fields."
        )
        if risk:
            parts.append(
                f"My initial assessment is **{risk.severity_suggested}** severity - "
                f"{risk.suggested_next_action}."
            )

    # Completeness nudge: tells the user what to supply next, and doubles as
    # the "Complaint Completeness Checker" bonus feature from the brief.
    missing = _missing_important(fields)
    if missing:
        names = _join([prompts.label_for(name) for name in missing[:3]])
        parts.append(f"Still missing: {names}. You can tell me, or type it into the form.")
    elif status == "ready_to_commit":
        parts.append("The record looks complete - please review it, then commit to the QMS ledger.")

    # --- partial failure: fields updated but the risk step failed ---
    if error:
        parts.append(f"Note: {error}")

    return {"assistant_message": " ".join(parts), "status": status}


# ---------------------------------------------------------------------------
# ROUTERS - the conditional edges
# ---------------------------------------------------------------------------


def route_after_classify(state: AgentState) -> str:
    """new complaint -> extract everything; correction -> patch; failure -> reply."""
    if state.get("error"):
        return "format_output"
    return "extract_all" if state.get("intent") == "new_complaint" else "patch_fields"


def route_after_fields(state: AgentState) -> str:
    """
    Run the risk node only when it is worth running.

    Three ways to skip it: the extraction failed, nothing defect-relevant
    changed, or there is not yet enough on the form to assess. That last one
    matters - assessing an almost-empty complaint produces a confident,
    meaningless severity.
    """
    if state.get("error"):
        return "format_output"
    if not state.get("needs_risk"):
        return "format_output"

    values = state["fields"].model_dump()
    if not (values.get("complaint_description") or values.get("complaint_type")):
        return "format_output"

    return "assess_risk"


# ---------------------------------------------------------------------------
# GRAPH ASSEMBLY
# ---------------------------------------------------------------------------


def build_graph():
    """Wire up the diagram at the top of this file and compile it."""
    graph = StateGraph(AgentState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("extract_all", extract_all)
    graph.add_node("patch_fields", patch_fields)
    graph.add_node("assess_risk", assess_risk)
    graph.add_node("format_output", format_output)

    graph.add_edge(START, "classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        route_after_classify,
        {
            "extract_all": "extract_all",
            "patch_fields": "patch_fields",
            "format_output": "format_output",
        },
    )

    # Both field nodes face the same question: is a re-assessment warranted?
    for node in ("extract_all", "patch_fields"):
        graph.add_conditional_edges(
            node,
            route_after_fields,
            {"assess_risk": "assess_risk", "format_output": "format_output"},
        )

    graph.add_edge("assess_risk", "format_output")
    graph.add_edge("format_output", END)

    return graph.compile()


# Compiled once at import. The graph is stateless - all conversation state
# arrives as an argument and leaves as a return value, which is exactly what
# lets the API layer keep it in MySQL instead of in server memory.
complaint_graph = build_graph()


# ---------------------------------------------------------------------------
# PUBLIC ENTRY POINT - what the API layer calls
# ---------------------------------------------------------------------------


def run_turn(
    user_message: str,
    form_state: ComplaintFormState,
    history: List[ChatMessage],
    source_label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run one conversational turn through the graph.

    Takes the session's current form state and history (loaded from MySQL),
    returns the new state plus everything the frontend needs. The caller
    persists the result; this function stores nothing.
    """
    initial: AgentState = {
        "user_message": user_message,
        "source_label": source_label,
        "fields": form_state.fields,
        "risk_assessment": form_state.risk_assessment,
        "history": history,
        "changed_fields": [],
        "needs_risk": False,
        "error": None,
    }

    final: AgentState = complaint_graph.invoke(initial)

    return {
        "form_state": ComplaintFormState(
            fields=final["fields"],
            risk_assessment=final.get("risk_assessment"),
        ),
        "changed_fields": final.get("changed_fields", []),
        "assistant_message": final.get("assistant_message", ""),
        "status": final.get("status", "pending_triage"),
        "intent": final.get("intent"),
        "error": final.get("error"),
    }
