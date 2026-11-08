.PHONY: up down test lint ingest eval-cases eval-fast eval-full android-fixtures

up:
	docker compose up --build

down:
	docker compose down

# Backend tests need the db service running (docker compose up db). Always the fake LLM.
# Exported by make itself, so it works whether recipes run in sh or cmd.exe (Windows).
test: export LLM_PROVIDER = fake
test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

# Deterministic ingest (build + load); never calls an LLM.
ingest:
	cd backend && uv run python -m ingest.ingest build && uv run python -m ingest.ingest load

# Regenerate eval/cases.jsonl from catalog.json + eval/cases_manual.jsonl.
eval-cases:
	cd backend && uv run python -m eval.generate_cases

# Regenerate the Android app's JSON fixtures from the real API (fake LLM, throwaway database).
# Needs the db service running (docker compose up db).
android-fixtures: export LLM_PROVIDER = fake
android-fixtures:
	cd backend && uv run python -m scripts.export_android_fixtures

# The 20 fast cases against the running API, logged in as the developer demo user (the trace
# needs that role). EVAL_PASSWORD defaults to the docker compose development password.
# Refuses a paid provider.
eval-fast: export EVAL_PASSWORD ?= developer-dev-password
eval-fast:
	cd backend && uv run python -m eval.run_eval --fast

# Full suite with the paid provider: HUMAN ONLY. Loads .env (LANGSMITH_*, EVAL_*); fails if missing.
eval-full:
	cd backend && uv run --env-file ../.env python -m eval.run_eval --confirm-paid
