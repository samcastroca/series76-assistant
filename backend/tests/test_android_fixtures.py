"""The Android app's JSON fixtures match the API contract and are what the API returns today.

The first tests validate each committed fixture against its Pydantic response model (no
database). The last one regenerates them from the real API and compares, so a contract or
behavior change that is not re-exported fails here (`make android-fixtures`)."""

import json
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from app.schemas.conversation import ConversationResponse, MessageOut, SessionSummary
from scripts.export_android_fixtures import OUT_DIR, generate, render


class _ErrorDetail(BaseModel):
    code: str
    message: str


class _ErrorBody(BaseModel):
    error: _ErrorDetail


RESPONSE_TYPES = [
    "detail",
    "recommendation",
    "comparison",
    "no_match",
    "not_found",
    "no_data",
    "clarification",
]
ERRORS = {
    "empty_query": "empty_query",
    "session_not_found": "session_not_found",
    "llm_unavailable": "llm_unavailable",
    "budget_exceeded": "budget_exceeded",
}


def _load(name: str) -> Any:
    return json.loads((OUT_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("response_type", RESPONSE_TYPES)
def test_conversation_fixtures_match_the_response_model(response_type) -> None:
    body = _load(f"conversation_{response_type}")
    parsed = ConversationResponse.model_validate(body)
    assert parsed.response_type == response_type
    # Every field is present: the app decodes strictly in its contract tests.
    assert set(body) == set(ConversationResponse.model_fields)


def test_session_and_message_fixtures_match_their_models() -> None:
    sessions = TypeAdapter(list[SessionSummary]).validate_python(_load("sessions"))
    messages = TypeAdapter(list[MessageOut]).validate_python(_load("messages"))
    assert sessions and messages
    assert {m.status for m in messages} == {"ok", "error"}  # the app's retry path is covered
    assert any(m.products for m in messages)  # history rebuilds cards from products


@pytest.mark.parametrize(("name", "code"), ERRORS.items())
def test_error_fixtures_use_the_single_error_shape(name, code) -> None:
    body = _load(f"error_{name}")
    assert set(body) == {"error"}
    assert _ErrorBody.model_validate(body).error.code == code


@pytest.mark.db
def test_fixtures_are_up_to_date() -> None:
    for name, body in generate().items():
        committed = (OUT_DIR / f"{name}.json").read_text(encoding="utf-8")
        assert committed == render(body), f"{name}.json is stale: run `make android-fixtures`"
