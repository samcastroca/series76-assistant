"""Password hashing (Argon2 via pwdlib) and access tokens (HS256 JWT via PyJWT). Pure functions:
no database, no request. See ADR 0006."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32  # HS256 keys shorter than the 256-bit digest are weak (RFC 7518 3.2)

_hasher = PasswordHash.recommended()
# Verified when the email is unknown, so a login takes the same time either way.
_DECOY_HASH = _hasher.hash(uuid.uuid4().hex)


class TokenExpired(Exception):
    pass


class TokenInvalid(Exception):
    pass


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    role: str


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """False for a wrong password and for a missing user (None), at the same cost. A stored
    hash in an unknown format is logged and rejected (401, not 500)."""
    if password_hash is None:
        _hasher.verify(password, _DECOY_HASH)
        return False
    try:
        return _hasher.verify(password, password_hash)
    except UnknownHashError:
        logger.error("stored password hash has an unknown format")
        return False


def create_token(
    user_id: uuid.UUID,
    role: str,
    secret: str,
    expire_minutes: int,
    now: datetime | None = None,
) -> str:
    issued = now or datetime.now(UTC)
    claims = {"sub": str(user_id), "role": role, "exp": issued + timedelta(minutes=expire_minutes)}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> TokenClaims:
    try:
        claims = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": ["sub", "role", "exp"]}
        )
        return TokenClaims(uuid.UUID(claims["sub"]), str(claims["role"]))
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired from exc
    except (jwt.InvalidTokenError, ValueError) as exc:  # ValueError: sub is not a UUID
        raise TokenInvalid from exc
