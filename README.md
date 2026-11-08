# Series 76 Technical Assistant

A technical assistant that helps plant engineers look up and select **Series 76 electric
actuators**. Users ask in natural language (for example, "I need a weatherproof modulating
actuator at 220 V") and get answers backed by the published catalog tables.

> **Status:** the ingestion, the agent, the REST API and the evaluation suite run locally
> (`docker compose up`, `make eval-fast`). The Android app is not implemented yet.

## How it works

The catalog tables in the source PDF are treated as structured data in PostgreSQL, not as text
for retrieval. A LangChain agent reads that data only through typed tools, and the app renders
every spec straight from the tool output. No number the user sees is written by the LLM.

- **Ingestion** turns the 7 tables (68 part numbers) into validated rows in Postgres. The data
  PDF is a scan, so the tables are read once with a vision model and the raw output is committed;
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

Requirements: Docker with Compose. For local development and tests also
[uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv) and `make`.

```bash
cp .env.example .env        # optional: the defaults run the fake LLM, no key needed
docker compose up --build   # Postgres + migrations + catalog load + API on :8000
```

On every start the `api` container runs `alembic upgrade head`, loads
`backend/data/processed/catalog.json` (an idempotent upsert; no LLM involved) and starts uvicorn.

- API: `http://localhost:8000` (interactive docs at `/docs`)
- From the Android emulator: `http://10.0.2.2:8000`

```bash
# New conversation
curl -s -X POST localhost:8000/api/conversation \
  -H 'Content-Type: application/json' \
  -d '{"query": "datos del 761A00-11300000/A"}'

# Follow-up: reuse the session_id from the previous response
curl -s -X POST localhost:8000/api/conversation \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "<session_id>", "query": "and how much power does it draw?"}'
```

The response always has `session_id`, `message_id`, `answer` and `response_type`, plus
`products` (the specs, built from the database), `highlight_fields`, `clarification`,
`suggestions` and `warnings`. Errors use `{"error": {"code": ..., "message": ...}}`.

| Method | Path | Use |
| --- | --- | --- |
| POST | `/api/conversation` | One chat turn |
| GET | `/api/sessions` | Conversations, newest first |
| GET | `/api/sessions/{id}/messages` | History with the products of each answer |
| DELETE | `/api/sessions/{id}` | Delete a conversation |
| GET | `/api/messages/{id}/trace` | Tool calls, model, tokens and cost (only with `DEBUG_API=true`) |
| GET | `/health` | Database status and configured provider/models |

### Configuration

Settings come from environment variables (`.env` for Docker Compose). Model IDs are never
hardcoded.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LLM_PROVIDER` | `fake` | `fake` (scripted model, real tools, no cost), `google` (free manual checks, uses `GOOGLE_API_KEY`) or `openrouter` (final validation, uses `OPENROUTER_API_KEY`) |
| `AGENT_MODEL` / `FALLBACK_MODEL` | none | Model IDs as the provider names them; required for `google`/`openrouter` |
| `LLM_MAX_TOKENS` | `1500` | Output cap per model call (temperature is always 0) |
| `LLM_TIMEOUT_S` | `60` | Provider request timeout |
| `MODEL_CALL_LIMIT` / `TOOL_CALL_LIMIT` | `6` / `6` | Calls allowed per chat turn |
| `HISTORY_TURNS` | `10` | Earlier turns sent to the model |
| `MAX_QUERY_CHARS` | `1000` | Longest accepted query |
| `DEBUG_API` | `false` | Mounts the trace endpoint for the app's Debug screen |
| `DATABASE_URL` | local Postgres | Async SQLAlchemy URL (compose points it at the `db` service) |

### Development

```bash
docker compose up -d db   # tests create throwaway databases on this server
make test                 # backend tests, always with the fake LLM
make lint                 # ruff check + format check
```

### Ingestion

```bash
cd backend
uv run python -m ingest.ingest build   # data/extracted + corrections.yaml -> data/processed (offline)
uv run python -m ingest.ingest load    # data/processed/catalog.json -> Postgres (idempotent upsert)

# Only when re-reading the PDF (human-run). INGEST_LLM_PROVIDER=google needs GOOGLE_API_KEY;
# INGEST_LLM_PROVIDER=openrouter needs INGEST_OPENROUTER_API_KEY (not the agent's key).
uv run --group extract --env-file ../.env python -m ingest.ingest extract
```

`extract` runs the vision model twice per table and writes the cells where the runs disagree to
`data/extracted/discrepancies.md`. Each one is checked against the PDF and fixed in
`data/corrections.yaml`; `build` refuses to run until every disagreement is covered.

### Evaluation

The agent is evaluated end to end through the API. `backend/eval/generate_cases.py` builds cases
from `data/processed/catalog.json` (lookups, searches, comparisons, multi-turn chains; expected
part numbers and thresholds are computed from the catalog) and merges the hand-written edge cases
in `eval/cases_manual.jsonl` into `eval/cases.jsonl`. Twenty of them are marked `fast`.

```bash
make eval-cases   # regenerate eval/cases.jsonl
make eval-fast    # 20 fast cases against http://localhost:8000 (needs DEBUG_API=true)
make eval-full    # every case with the paid provider; passes --confirm-paid: human only
```

`eval/run_eval.py` scores each turn on tool selection, argument accuracy, expected part numbers,
`response_type`, numeric faithfulness (every number in the answer is in a tool result) and
coreference (follow-ups target the active product), and reports p50/p95 latency and cost per
turn. It prints a table per category and writes `eval/results/<date>-<model>.json`. It refuses
to run against `openrouter` (checked locally and through `/health`) without `--confirm-paid`.
With `LANGSMITH_TRACING=true` it also uploads the dataset and logs a LangSmith experiment from
the scores it already computed.

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
