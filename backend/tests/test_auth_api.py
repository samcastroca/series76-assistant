"""Authentication and authorization through the API: login, tokens, the failed-login limit, the
developer-only trace and per-user session isolation. LLM_PROVIDER=fake against Postgres."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from langchain_core.messages import AIMessage

from app.agent.fake import ScriptedFakeModel, calls, tool_call
from app.config import Settings
from app.main import create_app
from app.security import create_token
from ingest.load import read_catalog
from tests._api import JWT_SECRET, PASSWORD, USERS, assert_error, bearer

pytestmark = pytest.mark.db

PN = next(r for r in read_catalog() if r.data_available).part_number
PUBLIC_API_ROUTES = {("POST", "/api/auth/login")}
ZERO = "00000000-0000-4000-8000-000000000000"


def lookup() -> AIMessage:
    return calls(tool_call("get_actuator", part_number=PN))


def user_id_of(headers: dict[str, str]) -> uuid.UUID:
    token = headers["Authorization"].removeprefix("Bearer ")
    return uuid.UUID(jwt.decode(token, options={"verify_signature": False})["sub"])


# --- Login and tokens ---------------------------------------------------------------------------


async def test_login_returns_a_bearer_token_and_the_user(client) -> None:
    async with client([], as_user=None) as c:
        r = await c.post(
            "/api/auth/login", json={"email": " Developer@Example.com ", "password": PASSWORD}
        )
        body = r.json()
        me = await c.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
        )

    assert r.status_code == 200
    assert set(body) == {"access_token", "token_type", "user"}
    assert body["token_type"] == "bearer"
    assert body["user"] == {"email": "developer@example.com", "role": "developer"}
    claims = jwt.decode(body["access_token"], JWT_SECRET, algorithms=["HS256"])
    assert set(claims) == {"sub", "role", "exp"} and claims["role"] == "developer"
    assert me.json() == {"email": "developer@example.com", "role": "developer"}


async def test_token_lifetime_follows_jwt_expire_minutes(client) -> None:
    async with client([], as_user=None, jwt_expire_minutes=5) as c:
        before = datetime.now(UTC)
        r = await c.post(
            "/api/auth/login", json={"email": USERS["engineer1"][0], "password": PASSWORD}
        )
    exp = jwt.decode(r.json()["access_token"], JWT_SECRET, algorithms=["HS256"])["exp"]
    assert abs(exp - (before + timedelta(minutes=5)).timestamp()) < 5


async def test_wrong_password_and_unknown_email_get_the_same_401(client) -> None:
    async with client([], as_user=None) as c:
        wrong = await c.post(
            "/api/auth/login", json={"email": USERS["engineer1"][0], "password": "nope"}
        )
        unknown = await c.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
        )
    for r in (wrong, unknown):
        assert_error(r, 401, "invalid_credentials")
        assert r.headers["WWW-Authenticate"] == "Bearer"
    assert wrong.json() == unknown.json()


async def test_missing_bad_and_expired_tokens_get_401(client) -> None:
    async with client([], as_user=None) as c:
        headers = await bearer(c, "engineer1")
        uid = user_id_of(headers)
        expired = create_token(
            uid, "engineer", JWT_SECRET, 30, now=datetime.now(UTC) - timedelta(minutes=31)
        )
        forged = create_token(uid, "developer", "f" * len(JWT_SECRET), 30)
        ghost = create_token(uuid.uuid4(), "developer", JWT_SECRET, 30)  # no such user
        cases = [
            ({}, "not_authenticated"),
            ({"Authorization": "Basic abc"}, "not_authenticated"),
            ({"Authorization": f"Bearer {expired}"}, "token_expired"),
            ({"Authorization": f"Bearer {forged}"}, "invalid_token"),
            ({"Authorization": "Bearer not-a-token"}, "invalid_token"),
            ({"Authorization": f"Bearer {ghost}"}, "invalid_token"),
        ]
        for h, code in cases:
            r = await c.get("/api/auth/me", headers=h)
            assert_error(r, 401, code)
            assert r.headers["WWW-Authenticate"] == "Bearer"
        assert (await c.get("/api/auth/me", headers=headers)).status_code == 200


async def test_role_comes_from_the_database_not_the_token(client) -> None:
    async with client([], as_user=None) as c:
        uid = user_id_of(await bearer(c, "engineer1"))
        claims_developer = create_token(uid, "developer", JWT_SECRET, 30)
        r = await c.get(
            f"/api/messages/{ZERO}/trace", headers={"Authorization": f"Bearer {claims_developer}"}
        )
    assert_error(r, 403, "forbidden")


# --- Failed-login limit -------------------------------------------------------------------------


async def test_failed_logins_are_limited_per_email(client) -> None:
    email = USERS["engineer1"][0]
    async with client([], as_user=None, login_max_attempts=3, login_window_minutes=1) as c:
        for _ in range(3):
            r = await c.post("/api/auth/login", json={"email": email, "password": "nope"})
            assert_error(r, 401, "invalid_credentials")
        blocked = await c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        other = await c.post(
            "/api/auth/login", json={"email": USERS["engineer2"][0], "password": PASSWORD}
        )
    assert_error(blocked, 429, "too_many_attempts")  # even with the right password
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert other.status_code == 200


async def test_concurrent_failed_logins_cannot_exceed_the_limit(client) -> None:
    """Regression: the check and the count were separate steps, so a burst of parallel requests
    all passed the check before any failure was recorded."""
    wrong = {"email": USERS["engineer1"][0], "password": "nope"}
    async with client([], as_user=None, login_max_attempts=3) as c:
        burst = await asyncio.gather(*(c.post("/api/auth/login", json=wrong) for _ in range(8)))
    statuses = sorted(r.status_code for r in burst)
    assert statuses == [401] * 3 + [429] * 5


async def test_successful_login_resets_the_failure_count(client) -> None:
    email = USERS["engineer1"][0]
    async with client([], as_user=None, login_max_attempts=3) as c:
        for attempt in ("nope", "nope", PASSWORD, "nope", "nope"):
            await c.post("/api/auth/login", json={"email": email, "password": attempt})
        r = await c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200


# --- Roles and ownership ------------------------------------------------------------------------


async def test_developer_reads_the_trace_of_an_engineers_message(client) -> None:
    async with client([lookup(), AIMessage("Listo.")]) as c:  # engineer1
        mid = (await c.post("/api/conversation", json={"query": "q"})).json()["message_id"]
        r = await c.get(f"/api/messages/{mid}/trace", headers=await bearer(c, "developer"))
    assert r.status_code == 200
    assert [t["tool_name"] for t in r.json()["tool_calls"]] == ["get_actuator"]


async def test_another_users_session_is_invisible(client) -> None:
    async with client([lookup(), AIMessage("Listo.")]) as c:  # engineer1
        sid = (await c.post("/api/conversation", json={"query": f"datos del {PN}"})).json()[
            "session_id"
        ]
        other = await bearer(c, "engineer2")
        listed = (await c.get("/api/sessions", headers=other)).json()
        read = await c.get(f"/api/sessions/{sid}/messages", headers=other)
        deleted = await c.delete(f"/api/sessions/{sid}", headers=other)
        posted = await c.post(
            "/api/conversation", json={"session_id": sid, "query": "y?"}, headers=other
        )
        mine = (await c.get("/api/sessions")).json()

    assert listed == []
    assert_error(read, 404, "session_not_found")
    assert_error(deleted, 404, "session_not_found")
    assert_error(posted, 404, "session_not_found")
    assert [s["id"] for s in mine] == [sid] and mine[0]["message_count"] == 2


# --- Every /api route but login requires a token ------------------------------------------------


async def test_every_api_route_except_login_requires_a_token(catalog_db_url: str) -> None:
    """A sweep over every route in the OpenAPI schema, so a new route cannot ship unprotected."""
    settings = Settings(
        _env_file=None, database_url=catalog_db_url, llm_provider="fake", jwt_secret=JWT_SECRET
    )
    app = create_app(settings, ScriptedFakeModel(steps=[]))
    routes = [
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for method in ops
    ]
    assert len(routes) == 7  # login, me, conversation, sessions, messages, delete, trace

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    results = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            for method, path in routes:
                if (method, path) in PUBLIC_API_ROUTES:
                    continue
                url = path.replace("{session_id}", ZERO).replace("{message_id}", ZERO)
                r = await c.request(method, url, json={"query": "q"})
                results.append((method, path, r.status_code, r.json()["error"]["code"]))
    assert all((status, code) == (401, "not_authenticated") for *_, status, code in results), (
        results
    )
