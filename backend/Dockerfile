FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Dependencies first (cached layer); no dev or extract groups in the image.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY alembic.ini ./
COPY app ./app
COPY ingest ./ingest
COPY data/processed ./data/processed

EXPOSE 8000

# Migrate, load the catalog (idempotent upsert of catalog.json; never calls an LLM), serve.
CMD ["sh", "-c", "alembic upgrade head && python -m ingest.ingest load && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
