"""Generate the Android app's JSON fixtures from the real API.

Runs the FastAPI app in-process against a throwaway database (migrated and loaded with
catalog.json) with the rule-based fake LLM, so every catalog value in a fixture comes from the
database through the real tools. Requests run as a test engineer that logs in first. IDs,
timestamps and the access token are replaced by stable placeholders, so regenerating gives the
same files.

    uv run python -m scripts.export_android_fixtures      (needs `docker compose up db`)
"""

import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from alembic import command
from langchain_core.language_models import BaseChatModel

from app.agent.fake import ScriptedFakeModel
from app.config import Settings
from app.db.engine import make_engine, make_sessionmaker
from app.main import create_app
from app.security import MIN_SECRET_LENGTH
from ingest.load import read_catalog, run_load
from tests._db import alembic_config, create_user, temp_database

OUT_DIR = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "test" / "resources"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
_BASE_TIME = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
_EMAIL, _PASSWORD = "engineer@example.com", "fixture-password"
_JWT_SECRET = "f" * MIN_SECRET_LENGTH
_TOKEN_PLACEHOLDER = "<access-token>"  # a real JWT changes with every run (exp)


def _queries() -> dict[str, str]:
    """User queries; part numbers are picked from catalog.json, never typed."""
    rows = read_catalog()
    found = [r.part_number for r in rows if r.data_available]
    missing = next(r.part_number for r in rows if not r.data_available)
    return {
        "detail": f"Dame los datos del {found[0]}",
        "follow_up": "¿Y cuánto consume ese?",
        "comparison": f"Compara el {found[0]} con el {found[1]}",
        "recommendation": "Necesito un actuador weatherproof modulante a 220V monofásico",
        "no_match": "Necesito 900000 Nm a 24V explosionproof",
        "not_found": "761A00-11380000/A",  # the summary PDF example; not in the tables
        "no_data": f"Datos del {missing}",
        "clarification": "¿Qué opciones hay?",
        "failed": "¿Y la velocidad?",
    }


async def _login(c: httpx.AsyncClient) -> Any:
    return await c.post("/api/auth/login", json={"email": _EMAIL, "password": _PASSWORD})


@asynccontextmanager
async def _client(
    db_url: str, model: BaseChatModel | None = None, signed_in: bool = True, **overrides: Any
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        _env_file=None,
        database_url=db_url,
        llm_provider="fake",
        agent_model=None,
        fallback_model=None,
        jwt_secret=_JWT_SECRET,
        **overrides,
    )
    app = create_app(settings, model)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            if signed_in:
                token = _expect(await _login(c), 200)["access_token"]
                c.headers["Authorization"] = f"Bearer {token}"
            yield c


def _expect(response: httpx.Response, status: int, response_type: str | None = None) -> Any:
    body = response.json()
    if response.status_code != status:
        raise RuntimeError(f"expected {status}, got {response.status_code}: {body}")
    if response_type and body["response_type"] != response_type:
        raise RuntimeError(f"expected {response_type}, got {body['response_type']}: {body}")
    return body


async def _collect(db_url: str) -> dict[str, Any]:
    q = _queries()
    out: dict[str, Any] = {}

    async def turn(c: httpx.AsyncClient, kind: str, session_id: str | None = None) -> Any:
        body = (
            {"query": q[kind]}
            if session_id is None
            else {"session_id": session_id, "query": q[kind]}
        )
        return await c.post("/api/conversation", json=body)

    async with _client(db_url) as c:
        # One multi-turn conversation: detail, a follow-up resolved from the session, a search.
        detail = _expect(await turn(c, "detail"), 200, "detail")
        session_id = detail["session_id"]
        out["conversation_detail"] = detail
        _expect(await turn(c, "follow_up", session_id), 200, "detail")
        out["conversation_recommendation"] = _expect(
            await turn(c, "recommendation", session_id), 200, "recommendation"
        )
        # Single-turn conversations, one per remaining response type.
        for kind in ("comparison", "no_match", "not_found", "no_data", "clarification"):
            out[f"conversation_{kind}"] = _expect(await turn(c, kind), 200, kind)

        out["error_empty_query"] = _expect(
            await c.post("/api/conversation", json={"query": "  "}), 400
        )
        unknown = "00000000-0000-4000-8000-000000000000"
        out["error_session_not_found"] = _expect(
            await c.get(f"/api/sessions/{unknown}/messages"), 404
        )

    # A provider failure in the existing session leaves a user message with status 'error'.
    async with _client(db_url, ScriptedFakeModel(steps=[ConnectionError("down")])) as c:
        out["error_llm_unavailable"] = _expect(await turn(c, "failed", session_id), 503)

    async with _client(db_url, daily_budget_usd=0) as c:
        out["error_budget_exceeded"] = _expect(await turn(c, "failed", session_id), 503)

    async with _client(db_url) as c:
        out["sessions"] = _expect(await c.get("/api/sessions"), 200)
        out["messages"] = _expect(await c.get(f"/api/sessions/{session_id}/messages"), 200)
        out["auth_me"] = _expect(await c.get("/api/auth/me"), 200)

    async with _client(db_url, signed_in=False) as c:
        login = _expect(await _login(c), 200)
        out["auth_login"] = {**login, "access_token": _TOKEN_PLACEHOLDER}
        wrong = {"email": _EMAIL, "password": "wrong-password"}
        out["error_invalid_credentials"] = _expect(await c.post("/api/auth/login", json=wrong), 401)
        out["error_not_authenticated"] = _expect(await c.get("/api/sessions"), 401)
    return out


def _normalize(fixtures: dict[str, Any]) -> dict[str, Any]:
    """Replace UUIDs (by first appearance) and timestamps (by chronological rank) with stable
    placeholders. Equal values stay equal and the order of events is kept."""
    text = json.dumps(fixtures, ensure_ascii=False, sort_keys=False)
    ids: dict[str, str] = {}
    for found in _UUID.findall(text):
        ids.setdefault(found, f"00000000-0000-4000-8000-{len(ids) + 1:012d}")
    stamps = sorted(set(_DATETIME.findall(text)), key=datetime.fromisoformat)
    times = {
        s: (_BASE_TIME + timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
        for i, s in enumerate(stamps)
    }
    text = _UUID.sub(lambda m: ids[m.group(0)], text)
    text = _DATETIME.sub(lambda m: times[m.group(0)], text)
    return json.loads(text)


def generate() -> dict[str, Any]:
    """Fixture name -> JSON body, normalized."""
    with temp_database() as url:
        command.upgrade(alembic_config(url), "head")
        rendered = url.render_as_string(hide_password=False)
        asyncio.run(run_load(rendered))
        asyncio.run(_add_user(rendered))
        return _normalize(asyncio.run(_collect(rendered)))


async def _add_user(db_url: str) -> None:
    engine = make_engine(db_url)
    try:
        async with make_sessionmaker(engine)() as db:
            await create_user(db, _EMAIL, "engineer", _PASSWORD)
    finally:
        await engine.dispose()


def render(body: Any) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in generate().items():
        (OUT_DIR / f"{name}.json").write_text(render(body), encoding="utf-8", newline="\n")
        print(f"wrote {name}.json")


if __name__ == "__main__":
    sys.exit(main())
