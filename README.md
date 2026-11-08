# Series 76 Technical Assistant

A technical assistant that helps plant engineers look up and select **Series 76 electric
actuators**. Users ask in natural language (for example, "I need a weatherproof modulating
actuator at 220 V") and get answers backed by the published catalog tables.

> **Status:** the ingestion, the agent, the REST API and the evaluation suite run locally
> (`docker compose up`, `make eval-fast`). The Android app has its first layer: the
> conversation list and the chat with product cards. Comparison tables and the debug screen come next.

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
| `DAILY_BUDGET_USD` | none (no cap) | Daily LLM spend cap (UTC day, from the cost the provider reports); over it a turn answers 503 `budget_exceeded` without calling the model |
| `DATABASE_URL` | local Postgres | Async SQLAlchemy URL (compose points it at the `db` service) |

### Development

```bash
docker compose up -d db   # tests create throwaway databases on this server
make test                 # backend tests, always with the fake LLM
make lint                 # ruff check + format check
```

### Android app

Open `android/` in Android Studio (or use Gradle from that folder) and run it on an emulator
while the backend is up.

```bash
cd android
./gradlew test            # unit tests: API contract, repository errors, ViewModels
./gradlew assembleDebug   # build the app
./gradlew connectedAndroidTest   # Compose UI tests (needs a device or emulator, unlocked)
```

- **Backend URL:** the app calls `http://10.0.2.2:8000`, which is the host machine as seen from
  the emulator. To use another address, set `API_BASE_URL=http://<host>:8000` in
  `android/local.properties`. Plain HTTP is allowed only for `10.0.2.2`, `localhost` and
  `127.0.0.1` (`res/xml/network_security_config.xml`).
- **Fixtures:** the JSON payloads in `android/app/src/test/resources/` feed the tests and the
  `@Preview`s. They are generated from the real API with the fake LLM, so they hold catalog
  values only from the database. Regenerate them with `make android-fixtures` (needs
  `docker compose up -d db`) whenever a response changes; a backend test fails while they are
  stale.
- **Errors:** each failure gets its own message with "Retry": no connection, server not
  answering (timeout), server down, server error (5xx), assistant unavailable (503
  `llm_unavailable`) and daily limit reached (503 `budget_exceeded`).
- **Local runs use the fake LLM:** compose takes `LLM_PROVIDER` from `.env`. To be sure no paid
  provider answers while you try the app, start the API with `LLM_PROVIDER=fake docker compose up`.

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
make eval-full    # every case with the paid provider; loads .env, passes --confirm-paid: human only
```

`eval/run_eval.py` scores each turn on tool selection, argument accuracy, expected part numbers,
`response_type`, numeric faithfulness (every number in the answer is in a tool result) and
coreference (follow-ups target the active product), and reports p50/p95 latency and cost per
turn. It prints a table per category and writes `eval/results/<date>-<model>.json`. It refuses
to run against `openrouter` (checked locally and through `/health`) without `--confirm-paid`.
With `LANGSMITH_TRACING=true` it also uploads the dataset and logs a LangSmith experiment from
the scores it already computed.

Latest full run: 2026-11-07, `anthropic/claude-sonnet-5.5` via OpenRouter (fallback
`z-ai/glm-5.3`), 36 cases / 40 turns
([`backend/eval/results/2026-11-07-anthropic-claude-sonnet-5.5-11.json`](backend/eval/results/2026-11-07-anthropic-claude-sonnet-5.5-11.json)).
Every metric scored 100 % in every category.

| Category | Cases passed | p95 latency |
|---|---|---|
| detail | 9/9 | 4057 ms |
| search | 6/6 | 9799 ms |
| comparison | 4/4 | 11118 ms |
| multi_turn | 4/4 | 10020 ms |
| invalid_part | 2/2 | 6472 ms |
| missing_data | 2/2 | 3431 ms |
| ambiguous | 2/2 | 5870 ms |
| no_match | 3/3 | 6841 ms |
| certification_tolerance | 3/3 | 4844 ms |
| out_of_scope | 1/1 | 1832 ms |
| **Total** | **36/36** | **9799 ms** (p50 4232 ms) |

Cost: $0.0202 per turn on average, $0.81 for the whole run, about 7,840 tokens per turn. The
first full run on the same day passed 27/36 (`…-6.json`); the fixes are in ADR 0005 and the
commits that followed it.

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
