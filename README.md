# Series 76 Technical Assistant

A technical assistant that helps plant engineers look up and select **Series 76 electric
actuators**. Users ask in natural language (for example, "I need a weatherproof modulating
actuator at 220 V") and get answers backed by the published catalog tables.

> **Status: design phase.** The design and the agent rules are written; the backend, the
> ingestion and the Android app are not implemented yet. The commands under
> [Running it](#running-it) describe the target setup and will work once those parts land.

## How it works

The catalog tables in the source PDF are treated as structured data in PostgreSQL, not as text
for retrieval. A LangChain agent reads that data only through typed tools, and the app renders
every spec straight from the tool output. No number the user sees is written by the LLM.

- **Ingestion** turns the 7 tables (68 part numbers) into validated rows in Postgres. The data
  PDF is a scan, so the tables are read once with Gemini vision and the raw output is committed;
  everything after that is deterministic ([ADR 0002](docs/adr/0002-vision-extraction.md)).
- **The agent** (LangChain v1 `create_agent`) has three tools: look up a part number, search by
  requirements, and list the available catalog options. Filtering, ranking and voltage
  tolerance are deterministic Python.
- **The API** (FastAPI) returns the answer text plus a structured `products` field built by the
  backend, and keeps tool traces in a separate debug endpoint.
- **The Android app** (Kotlin + Jetpack Compose) shows conversations, product cards and
  comparison tables, plus a debug screen for the traces.

The full design, including the data model, API contract and edge cases, is in
[`docs/design.md`](docs/design.md).

## Why not RAG or embeddings

The data is a small set of rows with typed fields. Filtering on those fields is exact and
explainable, and it lets the backend guarantee where every number comes from. Embeddings
(pgvector) would only help with free text, and the summary PDF fits in the prompt.

## Stack

Python 3.12 · FastAPI · LangChain v1 · PostgreSQL 17 · Docker Compose · Android (Kotlin +
Jetpack Compose)

## Running it

The target setup is one command for the whole backend:

```bash
docker compose up --build   # Postgres + migrations + ingestion (if empty) + API on :8000
```

- API: `http://localhost:8000` (interactive docs at `/docs`)
- From the Android emulator: `http://10.0.2.2:8000`

Configuration goes in `.env`, starting from `.env.example`. The LLM is chosen with
`LLM_PROVIDER` (`openrouter`, `google` or `fake`) and the models with `AGENT_MODEL` and
`FALLBACK_MODEL`. `LLM_PROVIDER=fake` runs the full flow with real tools and no LLM cost.

### Ingestion

```bash
cd backend
uv run python -m ingest.ingest build   # data/extracted + corrections.yaml -> data/processed (offline)
uv run python -m ingest.ingest load    # data/processed/catalog.json -> Postgres (idempotent upsert)

# Only when re-reading the PDF (human-run, free Google AI Studio key; never the OpenRouter key):
uv sync --group extract
uv run --env-file ../.env python -m ingest.ingest extract   # needs GOOGLE_API_KEY, INGEST_LLM_MODEL
```

`extract` runs Gemini twice per table and writes the cells where the runs disagree to
`data/extracted/discrepancies.md`. Each one is checked against the PDF and fixed in
`data/corrections.yaml`; `build` refuses to run until every disagreement is covered.

## Repository layout

```
backend/    FastAPI app, agent, tools, ingestion, data, tests, eval
android/    Kotlin + Jetpack Compose app
docs/       design.md and architecture decision records (adr/)
.claude/    Claude Code setup used to build the project
```

## Built with Claude Code

The project is developed with Claude Code. [`CLAUDE.md`](CLAUDE.md) holds the rules the agent
follows, and `.claude/` holds its settings, a reviewer agent and project skills.
