# Screenshots

Five images, referenced by the root `README.md`. **Filenames must match
exactly** or the links break.

| Filename | Shows |
|----------|-------|
| `01-empty-form.png` | Empty form, badge on *Pending Triage* |
| `02-extracted.png` | All 11 fields filled from an `.eml` upload, badge on *Ready to Commit* |
| `03-ai-copilot-card.png` | Section 4 with all six AI outputs and the suggestions divider |
| `04-correction-highlight.png` | A correction with only Batch/Lot Number highlighted |
| `05-duplicate-detection.png` | Chat showing the prior-complaint notice for a repeated batch |

## Reproducing them

**01** — fresh page load, before any input.

**02** — upload `samples/complaint-01-strength-mixup.eml` and wait for
extraction.

**03** — scroll the left panel so section 4 fills the frame.

**04** — the hard one: the highlight only lasts 2.4 seconds. Send
`actually the batch number is XLP-8396-0635` and capture immediately. If you
keep missing it, temporarily raise `HIGHLIGHT_MS` in `frontend/src/App.jsx`
**and** `--highlight-duration` in `frontend/src/index.css` to `10000`, take the
shot, then change both back - they must stay in sync.

**05** — commit a complaint, click **Reset Form**, then upload a document with
the same batch number again.

## Guidelines

- Use a **light OS theme** and a clean browser window - no bookmarks bar, no
  extension icons, no personal tabs.
- Browser zoom at 100%, window around 1600x1000 so the two-panel grid does not
  collapse to the stacked mobile layout (it stacks below 1024px).
- PNG, not JPEG - screenshots of text render badly as JPEG.
- Check each image before committing: no API keys, no local file paths, no
  personal information in the tab bar or the URL.
