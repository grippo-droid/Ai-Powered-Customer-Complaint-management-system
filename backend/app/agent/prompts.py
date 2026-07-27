"""
Every prompt the system sends to the LLM, in one file.

Three LLM calls exist in the whole pipeline, one per thinking node:

    classify_intent   -> IntentClassification
    extract_all       -> ComplaintFields  (all fields)
    patch_fields      -> ComplaintFields  (only the mentioned fields)
    risk_assessment   -> RiskAssessment

(format_output makes no LLM call at all - see the note at the bottom.)

The field lists in these prompts are GENERATED from the Pydantic models via
model_json_schema(), never typed out by hand. Add a field to ComplaintFields
and every prompt here learns about it automatically. That is the whole reason
the Field(description=...) text in schemas.py is written the way it is - it
is prompt copy, not documentation.

Prompt style is tuned for gemma2-9b-it, which is a small 9B model: short
instructions, explicit negative rules, and a worked example for the two
tasks it finds hardest (intent classification and partial patching).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Type

from pydantic import BaseModel

from app.schemas import ComplaintFields, IntentClassification, RiskAssessment

# ---------------------------------------------------------------------------
# Human-readable labels, used in the chat confirmation ("Batch/Lot Number")
# and mirrored by the React form. Kept next to the prompts because both are
# user-facing copy.
# ---------------------------------------------------------------------------

FIELD_LABELS: Dict[str, str] = {
    "complaint_source": "Complaint Source",
    "customer_name": "Customer Name",
    "product_name": "Product Name",
    "product_strength": "Product Strength/Grade",
    "batch_number": "Batch/Lot Number",
    "affected_quantity": "Affected Quantity",
    "manufacturing_date": "Manufacturing Date",
    "expiry_date": "Expiry Date",
    "complaint_type": "Complaint Type",
    "complaint_date": "Complaint Date",
    "complaint_description": "Complaint Description",
    "severity_suggested": "Severity (Suggested)",
    "suggested_next_action": "Suggested Next Action",
    "initial_risk_assessment": "Initial Risk Assessment",
}


def label_for(field_name: str) -> str:
    """Pretty name for a field, falling back to a title-cased version."""
    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Schema rendering - the bridge between Pydantic and the prompt
# ---------------------------------------------------------------------------


def render_field_spec(model: Type[BaseModel]) -> str:
    """
    Render a Pydantic model's JSON schema as a compact field list.

    We read model_json_schema() rather than hand-writing the field list, so
    the prompt can never drift out of sync with the model that validates the
    reply. But we render it COMPACTLY instead of dumping the raw schema JSON:
    for ComplaintFields the raw schema is 2,927 characters (~730 tokens) of
    anyOf/null wrapping, versus 1,257 characters (~315 tokens) rendered like
    this - a 2.3x saving, repeated in every prompt, on an 8,192-token window.
    It also reads far better to a 9B model than nested JSON Schema does.

    Output looks like:
        - batch_number (string or null): Batch or lot number exactly as printed...
    """
    schema = model.model_json_schema()
    lines = []

    for name, spec in schema.get("properties", {}).items():
        description = spec.get("description", "")
        required = name in schema.get("required", [])
        type_hint = "string" if required else "string or null"

        # Literal fields (e.g. intent) publish their allowed values - pass
        # them through so the model sees the exact permitted strings.
        options = spec.get("enum") or next(
            (sub["enum"] for sub in spec.get("anyOf", []) if "enum" in sub), None
        )
        if options:
            type_hint = " or ".join(json.dumps(o) for o in options)

        lines.append(f"- {name} ({type_hint}): {description}".rstrip())

    return "\n".join(lines)


def _known_values(fields: ComplaintFields) -> str:
    """The fields currently filled in on the form, as a readable block."""
    filled = {k: v for k, v in fields.model_dump().items() if v not in (None, "")}
    if not filled:
        return "(the form is completely empty)"
    return "\n".join(f"- {label_for(k)}: {v}" for k, v in filled.items())


# ---------------------------------------------------------------------------
# 1. classify_intent
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = """You are the routing step of a pharmaceutical complaint intake system.

Decide whether the user's message is:
  "new_complaint" - a fresh complaint document, email, or narrative describing
                    a NEW product problem that should be extracted from scratch.
  "correction"    - a short amendment, addition, or clarification to the
                    complaint ALREADY on the form.

Rules:
- Long text describing a product problem in full is "new_complaint".
- Short messages naming one or two specific values are "correction".
- Phrases like "actually", "correction:", "it's not X it's Y", "also", "change
  the ... to ..." are strong signals of "correction".
- If the form is completely empty, it is almost always "new_complaint".

Return ONLY a JSON object. No markdown, no code fences, no commentary."""


def build_classify_prompt(user_message: str, current_fields: ComplaintFields) -> str:
    return f"""COMPLAINT CURRENTLY ON THE FORM:
{_known_values(current_fields)}

USER'S NEW MESSAGE:
\"\"\"{user_message}\"\"\"

Return JSON with exactly these keys:
{render_field_spec(IntentClassification)}

Example for a correction:
{{"intent": "correction", "reasoning": "User is amending only the batch number."}}

JSON:"""


# ---------------------------------------------------------------------------
# 2. extract_all  (new complaint -> every field)
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = """You are a pharmaceutical Quality Assurance intake specialist.
You read customer complaint documents (emails, letters, pharmacy reports) and
extract structured data for a QMS complaint record.

Rules:
- Extract ONLY what the document actually states. Never invent a value.
- If a field is not stated, return null for it. A null is correct and useful;
  a guess is a data integrity failure.
- Copy identifiers such as batch/lot numbers EXACTLY, including case and hyphens.
- Dates: return YYYY-MM-DD when the date is unambiguous.
- complaint_description: summarise what the customer observed in 2-4 factual
  sentences. Do not speculate about root cause.

Return ONLY a JSON object with every key present. No markdown, no code fences."""


def build_extract_prompt(document_text: str, source_label: str | None = None) -> str:
    origin = f"\nSOURCE: {source_label}" if source_label else ""
    return f"""COMPLAINT DOCUMENT:{origin}
\"\"\"{document_text}\"\"\"

Extract into JSON with exactly these keys (use null where not stated):
{render_field_spec(ComplaintFields)}

JSON:"""


# ---------------------------------------------------------------------------
# 3. patch_fields  (correction -> ONLY the mentioned fields)
# ---------------------------------------------------------------------------

PATCH_SYSTEM = """You are updating an existing pharmaceutical complaint record.

The user is correcting or adding SPECIFIC details. Your job is to return ONLY
the fields they actually mentioned.

Critical rules:
- Include a key ONLY if the user's message explicitly gives a new value for it.
- OMIT every other key entirely. Do not repeat unchanged values. Do not include
  a key with null just to acknowledge it.
- Returning a field the user did not mention will overwrite verified data and
  is the worst failure mode of this system.
- Copy values exactly as the user wrote them.

Return ONLY a JSON object. No markdown, no code fences."""


def build_patch_prompt(user_message: str, current_fields: ComplaintFields) -> str:
    return f"""COMPLAINT CURRENTLY ON THE FORM:
{_known_values(current_fields)}

AVAILABLE FIELD NAMES:
{render_field_spec(ComplaintFields)}

USER'S CORRECTION:
\"\"\"{user_message}\"\"\"

Example - if the user said "actually the batch is BN-99 and 20 vials were affected",
you would return exactly:
{{"batch_number": "BN-99", "affected_quantity": "20 vials"}}
...and nothing else.

JSON:"""


# ---------------------------------------------------------------------------
# 4. risk_assessment
# ---------------------------------------------------------------------------

RISK_SYSTEM = """You are a senior pharmaceutical Quality Assurance reviewer performing
an INITIAL risk triage on a customer complaint, for both API and FDF products.

Judge severity by patient impact and GMP significance:
- Critical: potential patient harm, sterility breach, contamination, wrong
            product or wrong strength, label mix-up, suspected falsification.
- Major:    product quality clearly out of specification, but limited direct
            patient risk. Packaging integrity failures, discolouration.
- Minor:    cosmetic or administrative issues with no product quality impact.

Use your own judgement and pharma-QA vocabulary; you are not restricted to
those three words.

The justification must reference the SPECIFIC product, batch and defect. Never
write a generic sentence that would fit any complaint.

Return ONLY a JSON object. No markdown, no code fences."""


def build_risk_prompt(fields: ComplaintFields) -> str:
    return f"""COMPLAINT RECORD:
{_known_values(fields)}

Assess this complaint and return JSON with exactly these keys:
{render_field_spec(RiskAssessment)}

JSON:"""


# ---------------------------------------------------------------------------
# Note: there is deliberately NO prompt for the chat confirmation message.
#
# format_output builds that sentence with a template in orchestrator.py, from
# the changed_fields list the graph already computed. Two reasons:
#   1. It cannot lie. An LLM asked to summarise its own edits will sometimes
#      claim it changed a field it did not touch - and this message is the
#      user's only confirmation of what happened to their data.
#   2. It saves a full LLM round trip per turn, which is very noticeable on a
#      correction that skips the risk node.
# ---------------------------------------------------------------------------
