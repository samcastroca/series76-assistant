# ADR 0002: Vision extraction for a scanned data PDF

Date: 2026-11-04 · Status: accepted

## Context

`series_76_electric_data.pdf` is a scan. Each page is an embedded raster image with 1–15 text
characters, no lines and no rectangles, so `pdfplumber` finds no tables and no cells. A local OCR
(RapidOCR on a 300 dpi render of page 1) misread about 20 cells on one page: wrong digits,
letters in numeric cells, `.` as a thousands separator, and values from merged cells landing on
the wrong row. Fixing that by position-based parsing plus manual fixes would be fragile and still
need a human review of every table.

## Decision

Ingestion is split into three stages (`python -m ingest.ingest {extract|build|load}`):

1. **`extract`** (offline, network, run by a human only). `pdfplumber` renders each page at
   300 dpi and a vision model (`INGEST_LLM_MODEL`, `temperature=0`) extracts each table into a
   strict per-table JSON schema generated from `tables.yaml`. Cells are copied as printed
   (strings). Merged Motor Power cells are returned as explicit row ranges.
   Each table is extracted twice; both runs are committed in `data/extracted/<table>.json` and a
   cell-by-cell diff goes to `data/extracted/discrepancies.md`.
2. **`build`** (deterministic, no network). Reads `data/extracted/` (first run) plus the
   human-owned `data/corrections.yaml`, cleans, fills Motor Power down, validates with Pydantic,
   runs the cross-checks and writes `data/processed/catalog.json` and `actuators.csv`.
3. **`load`** (deterministic). Upserts `catalog.json` into Postgres.

## How determinism is kept

- The LLM output is data under version control, not a runtime dependency. Docker and the tests
  run only `build`/`load` on committed files and never import the Gemini client.
- Two runs plus a diff expose unstable reads. `build` refuses to run while a cell that differs
  between runs has no entry in `corrections.yaml`.
- Corrections are explicit, reviewed by a human against the PDF, and carry a reason.
- Cross-checks catch the typical misreads: 68 unique part numbers, row counts per table, N/A rows,
  table caption, part-number suffix per table, in-lb vs Nm (factor 8.85, 5 % tolerance: some
  printed pairs deviate 2–4 %), FLA ≤ LRA (some rows print FLA = LRA).
- Hand-verified spot values in `tests/test_ingest.py` are typed by the human from the PDF.
- `build` is byte-for-byte reproducible; a test compares its output with the committed catalog.

## Consequences

- `pdfplumber`, `google-genai` and `httpx` live in an optional `extract` dependency group; the
  API image does not need them.
- Re-extracting (new model, new PDF) is a reviewed change: new JSON, new diff, new corrections.
- The previous "optional cross-validation with Gemini" step disappears: the vision model is
  now the extractor, and the cross-checks plus the human review are the validation.
- Provider is chosen with `INGEST_LLM_PROVIDER`: `google` (Gemini through Google AI Studio,
  `GOOGLE_API_KEY`) or `openrouter` (any vision model with structured outputs,
  `INGEST_OPENROUTER_API_KEY`). OpenRouter was added because the AI Studio free tier allows 20
  requests per model per day and was returning 503 under load; a full extraction is 14 requests.
  Its key is separate from `OPENROUTER_API_KEY`, which belongs to the agent and the paid
  evaluation and is never read by ingestion. The stored `model` field records `provider:model`.
