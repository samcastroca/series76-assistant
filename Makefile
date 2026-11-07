.PHONY: up down test lint ingest

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
