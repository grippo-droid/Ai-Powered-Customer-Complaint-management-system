# AI-Powered Customer Complaint Management System

A pharmaceutical QMS complaint-intake tool. A LangGraph agent reads a complaint
document or email, populates the entire complaint form, generates an initial
risk assessment, and then applies **conversational corrections to individual
fields** without re-extracting everything else.

Built for the AIVOA Round 1 AI Product Engineer assignment.

## Demo & walkthrough

- **Demo video** — working demonstration of the AI tools and frontend features:
  <https://drive.google.com/file/d/1dsf_AghJ9b-juN8JlQ9xZFP8R3vQk4kz/view?usp=sharing>
- **Code walkthrough** — end-to-end explanation of the code, from the uploaded
  document through to the populated form:
  <https://drive.google.com/file/d/19AjY5JZysoPH-Co7zBd2wtkRrp1S3tVC/view?usp=sharing>

---

## Contents

- [Demo & walkthrough](#demo--walkthrough)
- [What it does](#what-it-does)
- [Tech stack](#tech-stack)
- [Setup](#setup)
- [Architecture](#architecture)
- [The correction mechanic](#the-correction-mechanic)
- [Design decisions](#design-decisions)
- [API reference](#api-reference)
- [Error handling](#error-handling)
- [Security](#security)
- [Bonus features](#bonus-features)
- [A note on the LLM model](#a-note-on-the-llm-model)
- [Project structure](#project-structure)

---

## What it does

A two-panel interface.

**Left — Log Customer Complaint.** Four sections: Origin & Customer Details,
Product & Batch Identification, Complaint Details, and an AI Copilot card that
produces six outputs — a one-line summary, suggested severity, suggested next
action, a risk justification, possible root causes, and a CAPA recommendation.
A status badge moves from *Pending Triage* to *Ready to Commit*, and the record
is written to a permanent ledger with **Commit to QMS Ledger**.

**Right — AI Complaint Intake Assistant.** Accepts a pasted complaint email or
an uploaded PDF / DOCX / TXT / EML. The first message fills in the whole form.
Every message after that is treated as a correction.

The behaviour worth seeing:

| You say | What happens |
|---------|--------------|
| *(paste a complaint email)* | All 11 fields populate, risk assessment generated |
| `the batch number is BN-9999 and 40 vials were affected` | **Only those two fields** flash green; risk regenerated |
| `the customer is MedPlus Pharmacy` | **Only that field** flashes; risk assessment **untouched** |
| *(refresh the browser)* | Conversation and form restore from MySQL |

Four ready-made complaint documents live in [`samples/`](samples/), one per
file format, with a suggested demo sequence in
[`samples/README.md`](samples/README.md).

### Screenshots

**Empty intake form.** Every field reads *"Awaiting AI extraction…"* and the
badge shows *Pending Triage*. Nothing is typed by hand to get started.

![The empty complaint form and AI intake assistant](docs/screenshots/01-empty-form.png)

**After uploading a complaint email.** One `.eml` upload fills all 11 fields —
including the customer name and complaint date, which are read from the message
headers rather than the body. The badge flips to *Ready to Commit*.

![The form fully populated from an uploaded email](docs/screenshots/02-extracted.png)

**The AI Copilot card.** Six generated outputs from a single LLM call: a
one-line summary, suggested severity, next action, and a risk justification —
then, below the divider, root-cause and CAPA suggestions explicitly framed as
hypotheses for QA to investigate rather than findings.

![The AI Copilot risk assessment card with all six outputs](docs/screenshots/03-ai-copilot-card.png)

**A conversational correction.** The user types *"actually the batch number is
XLP-8396-0635"*. Only **Batch/Lot Number** is highlighted; every other field is
left untouched, and the assistant confirms in plain language exactly what it
changed and that it re-ran the risk assessment as a result.

![A correction highlighting only the changed field](docs/screenshots/04-correction-highlight.png)

**Duplicate detection.** Filing a complaint against a batch already in the
ledger appends a notice naming the prior record — without blocking the commit,
because only a human can tell a duplicate report from a second occurrence.

![Duplicate batch notice appended to the chat reply](docs/screenshots/05-duplicate-detection.png)

---

## Tech stack

| Layer | Choice |
|-------|--------|
| Frontend | React 18 + Redux Toolkit, Vite |
| Backend | Python 3.11 + FastAPI |
| Agent framework | LangGraph |
| LLM | Groq (see [note on the model](#a-note-on-the-llm-model)) |
| Database | MySQL 8 via SQLAlchemy 2 ORM |
| PDF parsing | PyMuPDF (`fitz`) |
| Font | Google Inter |

---

## Setup

### Prerequisites

- Python **3.11**
- Node.js 18+
- MySQL 8 running locally
- A Groq API key — <https://console.groq.com/keys>

### 1. Database

```sql
CREATE DATABASE complaint_qms CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Tables are created automatically on first startup.

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env             # Windows: copy .env.example .env
```

Edit `.env` and set your real values:

```ini
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
DATABASE_URL=mysql+pymysql://root:your_password@localhost:3306/complaint_qms
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

> If your MySQL password contains `@ : / #`, URL-encode it — `@` becomes `%40`.

```bash
uvicorn app.main:app --reload
```

Expected: `Starting up - model=...` then `Database tables ready`.
Interactive API docs at <http://127.0.0.1:8000/docs>.

### 3. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

### Troubleshooting

| Symptom | Cause |
|---------|-------|
| `RuntimeError: Configuration error` | `.env` missing, or a required value is blank |
| `Access denied for user 'root'` | Wrong MySQL password in `DATABASE_URL` |
| `Unknown database 'complaint_qms'` | Step 1 not run |
| `400 model_decommissioned` | `GROQ_MODEL` unset or set to a retired model |
| Chat says *"rejected the API key"* | Bad `GROQ_API_KEY` — the form is left intact |
| CORS error in the browser console | Frontend not on port 5173, or `CORS_ORIGINS` mismatch |

---

## Architecture

### Request lifecycle

Every chat turn is the same four steps. **The API layer holds no state.**

```
browser ──▶ FastAPI ──▶ load session row from MySQL
                          │
                          ▼
                     LangGraph run  (up to 3 LLM calls)
                          │
                          ▼
                     save session row to MySQL
                          │
                          ▼
  form state + changed_fields + chat message ──▶ Redux ──▶ both panels
```

Because nothing lives in server memory, you can restart the backend
mid-conversation and the chat still remembers.

### The LangGraph pipeline

The entire graph is in one file — [`backend/app/agent/orchestrator.py`](backend/app/agent/orchestrator.py).

```
                        START
                          │
                  ┌───────▼────────┐
                  │ classify_intent│  new complaint, or a correction?
                  └───────┬────────┘
              new_complaint│correction              ← conditional edge
              ┌────────────┴────────────┐
    ┌─────────▼────────┐      ┌─────────▼────────┐
    │   extract_all    │      │   patch_fields   │
    │  fills ALL 11    │      │  fills ONLY the  │
    │     fields       │      │ mentioned fields │
    └─────────┬────────┘      └─────────┬────────┘
              └────────────┬────────────┘
              defect-relevant field changed?         ← conditional edge
              ┌────yes─────┴─────no──────┐
   ┌──────────▼─────────┐                │
   │    assess_risk     │                │
   │ severity · action  │                │
   │   · justification  │                │
   └──────────┬─────────┘                │
              └────────────┬─────────────┘
                  ┌────────▼────────┐
                  │  format_output  │  message + changed_fields + status
                  └────────┬────────┘
                          END
```

**Three LLM calls maximum per turn** — classify, extract-or-patch, risk.
`format_output` makes no LLM call at all.

The node is named `assess_risk` rather than `risk_assessment` because
LangGraph forbids a node name that collides with a state channel key.

---

## The correction mechanic

This is the core of the assignment, and it falls out of Pydantic rather than
being hand-built.

Every field on `ComplaintFields` is `Optional`. On a correction the LLM is
told to emit **only** the keys the user mentioned:

```python
patch = call_structured(PATCH_SYSTEM, prompt, ComplaintFields)
mentioned = patch.model_dump(exclude_unset=True)
#  -> {'batch_number': 'BN-9999', 'affected_quantity': '40 vials'}
```

`exclude_unset=True` returns exactly the keys the model chose to set — which
is exactly the set of fields the user talked about. No second model, no
hand-maintained key list.

The merge **re-validates** rather than using `model_copy(update=...)`:

```python
merged = ComplaintFields.model_validate({**current.model_dump(), **mentioned})
```

`model_copy` skips validators, so a corrected date would never be normalised.

Comparing merged against previous gives `changed_fields`, which is returned to
the browser. Redux holds that list, the form applies a CSS class to exactly
those inputs, and a timer clears it. **The frontend never diffs anything.**

---

## Design decisions

### The risk gate — `DEFECT_RELEVANT_FIELDS`

Regenerating the risk assessment on every correction would be wrong, not just
wasteful: a QA reviewer who has read a severity should not see it silently
change because someone fixed a customer's spelling.

Eight of the eleven fields are defect-relevant (product, strength, batch,
quantity, both dates, complaint type, description). `customer_name`,
`complaint_source` and `complaint_date` are **not** — correcting them skips
the risk node entirely, which is also roughly 3× faster on that turn.

### Six AI outputs, one LLM call

The AI Copilot card produces a summary, a severity, a next action, a
justification, root causes and a CAPA recommendation. Those could have been
four graph nodes. They are one, because they are **one act of reasoning**:

- They derive from the same evidence and are stale on the same trigger, so
  separate nodes would each need `DEFECT_RELEVANT_FIELDS` re-applied — with
  the failure mode that CAPA refreshes while severity does not, leaving an
  incoherent record.
- Asking separately invites contradiction: a `Critical` severity paired with a
  CAPA that reads like a paperwork tweak.
- It keeps a defect-relevant turn at **3 Groq calls and ~1.4s** instead of 5
  calls and ~4s, which is very visible in a live demo.

The tradeoff is a single prompt with six outputs rather than six focused ones.
Worth it here: the outputs are short, and coherence between them matters more
than independent optimisation of each.

### The raw `groq` SDK, not `langchain-groq`

LangGraph is used for orchestration, which is what it is good at. For the LLM
calls themselves a thin wrapper (`agent/llm.py`, one public function) is easier
to reason about and to explain than a chain abstraction, and it keeps retry
behaviour, JSON repair and error mapping in one visible place.

### Structured output via Pydantic, with a repair retry

The field list injected into each prompt is **generated from
`model_json_schema()`**, so a prompt can never drift out of sync with the model
that validates the reply. Rendering it compactly rather than dumping raw JSON
Schema cuts it from ~730 to ~315 tokens per prompt.

The reply is JSON-extracted with a brace-balanced scanner — it survives code
fences, chatty preamble, nested objects and braces inside string literals —
then validated by Pydantic. On malformed JSON or a `ValidationError` it
**retries once**, feeding the model its own bad output plus the exact
`exc.errors()` text. If that also fails it raises a clear error.

**A failed turn never modifies the form.**

### MySQL-backed session persistence

Two tables, doing deliberately different jobs:

| `conversation_sessions` | `complaints` |
|---|---|
| Live draft, one row per browser session | Permanent QMS ledger record |
| JSON columns mirroring the form state | Flat, queryable columns |
| Rewritten by the AI every turn | Written once, only on human commit |

The AI may rewrite the draft freely, but it can only ever *append* to the
ledger, and only when a person clicks the button — mirroring how a real QMS
separates an intake draft from a controlled record.

Note that SQLAlchemy does not track in-place mutation of a JSON column, so the
code always reassigns the whole attribute.

### Dates are stored as strings

Dates are normalised to ISO where possible, but **unparseable values are kept,
not dropped**. A `DATE` column would force discarding them. In a regulated
context, showing a reviewer a malformed date from the source document beats
silently storing `NULL`. The React form falls back to a text input when a value
is not ISO, so the odd date stays visible rather than rendering blank.

### The chat confirmation is templated, not generated

`format_output` builds its sentence from the `changed_fields` the graph
computed. An LLM asked to summarise its own edits will occasionally claim a
change it never made — and that message is the user's only confirmation of what
happened to their data.

---

## API reference

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/complaints/session` | Start a session, or resume one by client-supplied id |
| `GET` | `/complaints/session/{id}` | Rehydrate form + chat after a page refresh |
| `POST` | `/complaints/session/{id}/message` | Send text and/or a file; runs the graph |
| `POST` | `/complaints/session/{id}/commit` | Write the record to the ledger |
| `GET` | `/complaints` | List committed complaints, newest first, with their AI summaries |
| `GET` | `/health` | Liveness check |

Session creation is idempotent by client-supplied id, so a page refresh or
React StrictMode's double effect invocation cannot strand empty rows.

The message endpoint takes `multipart/form-data` with an optional `message`
field and an optional `file`, so one endpoint serves both input paths. Its
response carries `changed_fields` (which fields to highlight) and
`duplicate_notice` (set when the batch is already in the ledger) alongside the
full form state.

---

## Error handling

| Failure | Behaviour |
|---------|-----------|
| Malformed LLM JSON | Retry once with the error fed back, then a clear error state |
| Unsupported file type | HTTP **200** with a readable explanation in the chat |
| Scanned PDF (no text layer) | Detected and explained, not a silently blank form |
| Password-protected PDF | Explained in the chat |
| Risk assessment fails | Non-fatal — extracted fields stay on the form |
| Groq 401 / 429 | Mapped to actionable messages |
| Unexpected exception | Logged server-side, generic message to the client |

A bad upload is treated as a conversation event, not a server error — it is
recorded in the message history and survives a refresh.

---

## Security

- `GROQ_API_KEY` is read in one file, used in one file, and returned by no
  endpoint. The browser talks to this API; only this API talks to Groq.
- `.env` is git-ignored; only `.env.example` is committed.
- CORS is restricted to explicit origins — never `*` — with methods limited to
  `GET`, `POST`, `OPTIONS`.
- All database access goes through the SQLAlchemy ORM. There is no raw SQL
  string building anywhere.
- Uploads are validated for extension and size **before** parsing.
- Session ids are UUID4, not sequential integers, so in-progress complaints
  cannot be enumerated by guessing.

---

## Bonus features

Five of the brief's optional features are implemented. **None of them adds an
LLM call to the per-turn flow** — three ride in the existing risk call, one is
a database query, and one is pure formatting.

| Feature | Where it lives | Extra Groq calls |
|---------|----------------|------------------|
| AI Risk Classification | `assess_risk` node | — (core) |
| Complaint Completeness Checker | `format_output` node | **0** |
| Duplicate Complaint Detection | `services/duplicates.py` | **0** |
| Complaint Summary | `RiskAssessment` model | **0** |
| Root Cause Recommendation | `RiskAssessment` model | **0** |
| CAPA Recommendation | `RiskAssessment` model | **0** |

### Complaint completeness checker

`format_output` compares the form against a priority-ordered list of important
fields and appends a nudge:

> Still missing: Customer Name and Expiry Date. You can tell me, or type it
> into the form.

Built from data the graph already has, so it costs nothing. Visible when
uploading `samples/complaint-03-seal-integrity.pdf`: a formal letter never
states how the complaint arrived, so `complaint_source` is correctly left
`null` — the model is instructed never to invent a value — and the checker
points it out.

### Duplicate complaint detection

When a **new** complaint names a batch already in the committed ledger, the
reply ends with:

> Note: batch ZPL-2506-0412 already has 1 prior complaint on record
> (Complaint #1, filed 2026-07-27).

Deliberately **not** an LLM call and **not** a graph node. It is an exact-match
lookup on `ix_complaints_batch_number` — an LLM would be slower, cost money,
and could be wrong about something a `WHERE` clause is right about every time.
It also stays out of the graph because the pipeline has no database session by
design; that is what lets the API layer own persistence.

It fires only on new complaints — repeating it on every correction would train
reviewers to ignore it — and it **informs without blocking**. Only a human can
tell a duplicate report of one event from a genuine second occurrence.

### Complaint summary

A one-sentence ticket title, shown at the top of the AI Copilot card and
returned by `GET /complaints` so the ledger is scannable without opening each
record:

> Label mix-up in Amlodipine Besylate Tablets IP 5 mg batch ZPL-2506-0412,
> 3 cartons affected

The prompt supplies a worked example and forbids preamble, because a small
model's instinct is to write a sentence *about* the complaint rather than a
title *of* it.

### Root cause recommendation

One or two plausible causes for the investigation to test:

> Possible root causes: inadequate sealing process parameters or equipment
> malfunction at the blistering station; or insufficient quality control checks
> on packaging integrity.

These are **hypotheses, not findings** — the model has not seen the batch
record, the equipment logs or the retained sample. The prompt says so, and the
UI repeats it: the fields sit under a *"Suggestions for QA investigation"*
divider and the label reads *"Possible Root Causes (to investigate)"*. In a
regulated context a reviewer must never mistake an AI hypothesis for a
conclusion, and styling is part of how that is enforced.

### CAPA recommendation

One corrective action and one preventive action, capped at one to two sentences
each:

> **Corrective:** quarantine and re-inspect the remaining stock from batch
> ZPL-2491-0088. **Preventive:** review and enhance the packaging integrity
> testing protocol, including seal strength checks post-production.

---

## A note on the LLM model

The assignment mandates `gemma2-9b-it`, and that is the default in
`config.py`. **Groq decommissioned that model during development** — the API
now returns `400 model_decommissioned`.

The brief also names `llama-3.3-70b-versatile` as an acceptable alternative,
so that is the value shipped in `.env.example` and the one this project was
verified against. Nothing else changed: the same prompts, schemas and graph
run unmodified on either model, and `GROQ_MODEL` selects between them.

---

## Project structure

```
backend/
  .env.example            documents every required environment variable
  requirements.txt
  app/
    config.py             the only place environment variables are read
    database.py           engine, session factory, get_db dependency
    models.py             ORM: ConversationSession and Complaint
    schemas.py            all Pydantic models + the LangGraph state schema
    main.py               FastAPI app, CORS, startup
    api/complaints.py     the endpoints
    agent/
      orchestrator.py     the entire StateGraph: nodes, edges, run_turn
      llm.py              Groq client, JSON repair, validation, retry
      prompts.py          prompts, generated from the Pydantic schemas
    services/
      file_parser.py      PDF (fitz), DOCX, TXT, EML -> plain text
frontend/
  src/
    App.jsx               two-panel layout, highlight timer
    api/client.js         the only module that calls fetch
    store/complaintSlice.js   one slice; one thunk updates both panels
    components/           ComplaintForm, Field, FormSection,
                          ChatPanel, MessageList, FileDropzone
samples/                  four demo complaints, one per file format
```
