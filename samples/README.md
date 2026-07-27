# Sample complaint documents

Fictional pharmaceutical complaints for demonstrating and testing the system.
All companies, people, batch numbers and contact details are invented.

Each file deliberately exercises a **different extractor** in
`backend/app/services/file_parser.py`, so uploading all four proves every
parsing path works:

| File | Format | Parser exercised | Severity produced |
|------|--------|------------------|-------------------|
| `complaint-01-strength-mixup.eml` | EML | `email` stdlib + header extraction | **Critical** |
| `complaint-02-particulate-call-note.txt` | TXT | encoding fallback chain | **Major** |
| `complaint-03-seal-integrity.pdf` | PDF | **fitz / PyMuPDF** | **Major** |
| `complaint-04-labelling-defect.docx` | DOCX | **table-cell** extraction | **Minor** |

The severity spread is not hand-tuned - the model reaches those verdicts on
its own, and they are the ones a QA reviewer would expect: a strength mix-up
with dosing-error potential outranks a smudged carton print.

---

## What each file demonstrates

### 1. `complaint-01-strength-mixup.eml` - Critical

Hospital pharmacy reports blister foils printed 10 mg inside cartons labelled
5 mg. Extracts 11/11 fields.

**Why this one matters for the demo:** the `From:` and `Date:` headers supply
`customer_name` and `complaint_date`, which the email body never states
outright. This is the reason `_extract_eml` keeps the headers and puts them
first. Severity comes back **Critical** with *"Initiate Immediate Recall and
QA Investigation"* - the model correctly weighs patient dosing risk.

### 2. `complaint-02-particulate-call-note.txt` - Major

An internal telephone-intake note, not a customer document. Extracts 11/11.

**Why this one matters:** it shows extraction works on messy internal notes,
not just formal correspondence. `complaint_source` is correctly inferred as
`Pharmacy` rather than `Email`.

### 3. `complaint-03-seal-integrity.pdf` - Major

Formal distributor letter about blister seal integrity failure. Extracts
**10/11** fields.

**Why the missing field is a feature, not a bug:** a formal letter never
states how the complaint was received, so `complaint_source` comes back
`null`. The model is instructed never to invent a value - a null is correct
and useful, a guess is a data integrity failure. The chat reply then nudges
*"Still missing: Complaint Source"*, which is the completeness checker doing
its job. The form still reaches **Ready to Commit**, because source is not a
required field.

Use this file to demo the correction flow:

> `the complaint source is Email`

...and watch only that one field flash green, with the risk assessment
**not** regenerated (source is not defect-relevant).

### 4. `complaint-04-labelling-defect.docx` - Minor

A QMS complaint registration form laid out as a **two-column Word table**.
Extracts 11/11.

**Why this one matters:** paragraph-only DOCX extraction would return almost
nothing here, because every value lives in a table cell. `_extract_docx`
reads the tables too and joins each row as `Label | Value`, which reads
cleanly to the LLM. Severity comes back **Minor** - correctly, since the
tablets themselves are fine and only the carton printing is smudged.

---

## Suggested demo sequence

1. Upload `complaint-01-strength-mixup.eml` -> the whole form fills, badge
   turns green, severity **Critical**.
2. Type `actually the batch number is ZPL-2506-0999 and 6 cartons were
   affected` -> only those two fields flash green, and the risk assessment is
   regenerated because the defect picture changed.
3. Type `the customer is Apollo Hospitals` -> only that field flashes, and the
   severity is **unchanged** - the risk node was skipped entirely.
4. Refresh the browser -> the conversation and form are restored from MySQL.
5. Click **Commit to QMS Ledger** -> the record is written and the badge
   shows `Committed #1`.
6. Click **Reset Form**, then upload `complaint-03-seal-integrity.pdf` to show
   the PDF path and the "Still missing" completeness nudge.

To demonstrate error handling, try uploading any `.exe` or `.zip` file: the
assistant replies with a readable explanation in the chat and the form is left
untouched, rather than the request failing with a server error.
