---
name: update-docs
description: Check that README.md, docs/design.md, docs/adr/, CLAUDE.md, .env.example and the API contract still match the code, and fix the drift. Use at the end of every phase, after changing a tool, endpoint, response field, env var, command or model, and before submission.
---

# Keep the documentation in sync with the code

The evaluators will run the project using only the README. If a command, variable or path in the docs is wrong, the submission fails. Docs drift every time code changes, so this check runs at the end of each phase.

## Rules

- **Code is the source of truth.** When docs and code disagree, report it; do not silently change code to match the docs.
- **Never invent facts.** Costs, latencies and eval scores come only from `backend/eval/results/` and the `llm_usage` table. If a number is not available, write `TODO(human): <what is missing>`.
- **No catalog values in docs** beyond short, clearly labeled examples copied from `backend/data/processed/catalog.json`.
- **No secrets.** `.env.example` holds placeholders only.
- Write docs in **English**, short and concrete. Prefer commands the reader can copy over prose.
- Do not rewrite `docs/design.md` or an ADR wholesale. Edit only the parts that drifted, and list each change in your summary.

## Step 1: collect what the code actually exposes

Gather this before reading any doc:

| Item | Where to look |
| --- | --- |
| Env vars | `backend/app/core/settings.py`, `docker-compose.yml`, `android/local.properties` usage |
| Make targets | `Makefile` |
| Compose services, ports, volumes | `docker-compose.yml` |
| Endpoints and response fields | FastAPI routers and response schemas in `backend/app/api/` (or `GET /openapi.json` if the API is running) |
| `response_type` values and error codes | schemas and error handlers |
| Tools, arguments, result statuses | `backend/app/tools/` |
| Models and defaults | settings defaults and `.env.example` |
| Ingestion command and outputs | `backend/ingest/`, `backend/data/processed/` |
| App config and debug access | `android/` (`BuildConfig`, `local.properties`, the developer-mode code) |
| Latest eval results | newest file in `backend/eval/results/` |

## Step 2: compare each document

### README.md (must follow section 8 of the assessment)
- [ ] **Setup**: every required tool and version; every env var listed in `.env.example` explained.
- [ ] **Backend**: the exact commands to build and run with Docker; API URL; working `curl` examples for `POST /api/conversation` (new session and follow-up).
- [ ] **Ingestion**: command, input path, output paths, and that it runs automatically on first `docker compose up`.
- [ ] **App**: how to open/build/run in Android Studio or Gradle, how to set the backend URL (`10.0.2.2` on the emulator), and how to open the debug screen.
- [ ] **Design and architecture**: summary plus links to `docs/design.md` and the ADRs.
- [ ] **Model choices**: current model IDs, why, and measured cost per turn (from results, not estimates).
- [ ] **Evaluation**: table copied from the latest results file, with its date and model.
- [ ] **Optional features**: test users (if login exists) and how voice works (if implemented).
- [ ] **AI assistant setup**: what each item in `.claude/` and `.mcp.json` is for.
- [ ] Every command and path mentioned exists.

### docs/design.md
- [ ] Data model matches the SQLAlchemy models and migrations.
- [ ] Tool table matches the tools' names, arguments and statuses.
- [ ] API section matches the response schema and endpoint list.
- [ ] Edge-case table matches the behavior covered by tests and eval cases.
- [ ] Model and budget section matches the settings defaults.

### docs/adr/
- [ ] Every ADR's decision is still true. If the code changed the decision, mark the ADR `Superseded by ADR-XXXX` and propose a new one; never edit history.

### CLAUDE.md
- [ ] Commands, repository layout and response fields are current.
- [ ] Rules still describe how the code works (for example, the validator allowlist).

### .env.example
- [ ] Contains every variable the code reads, with safe placeholders and a one-line comment each; nothing the code no longer reads.

### Android contract
- [ ] Kotlin response models and JSON fixtures in `android/app/src/test/resources/` match the backend response schema.

## Step 3: verify what can be verified cheaply

Run only free, local checks:

```bash
make lint
make test
docker compose config --quiet        # compose file is valid
```

If the stack is already running, also check `curl -s localhost:8000/health` and that each README `curl` example returns 200. Never call the paid provider and never run `make eval-full`.

## Step 4: fix and report

1. Fix documentation drift directly.
2. Report code-side problems (for example, an env var read in code but missing from settings) without changing the code.
3. Finish with a summary in this shape:

```
Updated:  README.md (Backend curl example, Model choices), .env.example (+DAILY_BUDGET_USD)
Drift found in code (not changed): settings reads HISTORY_TURNS but design.md says HISTORY_LIMIT
TODO(human): measured cost per turn missing — run make eval-full and rerun this skill
Checks: lint ok, tests ok, compose config ok
```

## Done when

- [ ] Every checklist item above is satisfied or listed as a TODO.
- [ ] No invented numbers, no secrets, no hand-typed catalog values.
- [ ] Summary delivered to the human.
