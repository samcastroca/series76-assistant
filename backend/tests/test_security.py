"""Password hashing and access tokens, without a database."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.security import (
    MIN_SECRET_LENGTH,
    TokenExpired,
    TokenInvalid,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)

SECRET = "s" * MIN_SECRET_LENGTH
USER = uuid.uuid4()


def test_hash_verifies_only_the_same_password() -> None:
    h = hash_password("correct horse")
    assert h.startswith("$argon2")
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)


def test_missing_user_never_verifies() -> None:
    assert not verify_password("anything", None)


def test_malformed_stored_hash_does_not_verify() -> None:
    assert not verify_password("anything", "not-a-hash")


def test_token_round_trip_carries_user_and_role() -> None:
    claims = decode_token(create_token(USER, "developer", SECRET, 30), SECRET)
    assert (claims.user_id, claims.role) == (USER, "developer")


def test_token_expires_after_the_configured_minutes() -> None:
    issued = datetime.now(UTC) - timedelta(minutes=31)
    token = create_token(USER, "engineer", SECRET, 30, now=issued)
    with pytest.raises(TokenExpired):
        decode_token(token, SECRET)
    exp = jwt.decode(token, options={"verify_signature": False})["exp"]
    assert exp == int((issued + timedelta(minutes=30)).timestamp())


@pytest.mark.parametrize(
    "token",
    [
        create_token(USER, "engineer", "o" * MIN_SECRET_LENGTH, 30),  # another secret
        create_token(USER, "engineer", SECRET, 30)[:-2] + "xx",  # tampered signature
        jwt.encode({"sub": str(USER), "exp": 9999999999}, SECRET, algorithm="HS256"),  # no role
        jwt.encode({"sub": "1", "role": "engineer", "exp": 9999999999}, SECRET),  # sub not a UUID
        jwt.encode({"sub": str(USER), "role": "engineer"}, SECRET, algorithm="HS256"),  # no exp
        "not-a-token",
    ],
)
def test_bad_tokens_are_invalid(token: str) -> None:
    with pytest.raises(TokenInvalid):
        decode_token(token, SECRET)


def test_unsigned_token_is_rejected() -> None:
    token = jwt.encode({"sub": str(USER), "role": "developer", "exp": 9999999999}, None, "none")
    with pytest.raises(TokenInvalid):
        decode_token(token, SECRET)
