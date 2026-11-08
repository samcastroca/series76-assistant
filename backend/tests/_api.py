"""Helpers for API tests: the error-shape assertion, the test users and logging in."""

import httpx

from app.security import MIN_SECRET_LENGTH

INTERNALS = ("get_actuator", "search_actuators", "list_catalog_options", "Traceback", "SELECT")
JWT_SECRET = "t" * MIN_SECRET_LENGTH
PASSWORD = "test-password"
# Created by the `client` fixture for every test: name -> (email, role).
USERS = {
    "engineer1": ("engineer1@example.com", "engineer"),
    "engineer2": ("engineer2@example.com", "engineer"),
    "developer": ("developer@example.com", "developer"),
}


def assert_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert not any(word in body["error"]["message"] for word in INTERNALS)


async def bearer(c: httpx.AsyncClient, user: str) -> dict[str, str]:
    """Log in as one of USERS and return its Authorization header."""
    r = await c.post("/api/auth/login", json={"email": USERS[user][0], "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
