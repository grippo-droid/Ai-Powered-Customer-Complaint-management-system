"""
Password hashing and JWT issuing/verification.

This module and `config.py` are the only two places in the project that touch
a credential. Nothing else imports passlib or jose, nothing else sees a
plaintext password or the signing key, and neither is ever logged, returned by
an endpoint, or written to the database.

Two halves:

    hash_password / verify_password   bcrypt, for what is stored in users
    create_access_token / decode_access_token   HS256 JWTs, for who is calling
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

logger = logging.getLogger(__name__)

# bcrypt, with a work factor left at passlib's default (12 rounds). That is
# deliberately slow - roughly 250ms per hash - which is the entire point of a
# password hash: it makes an offline guessing attack expensive. It also means
# login is measurably slower than the other endpoints, which is correct.
#
# `deprecated="auto"` marks any non-default scheme as needing a rehash, so if
# this list ever gains a stronger algorithm, existing hashes are recognised as
# legacy rather than rejected.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt hashes at most 72 BYTES and silently ignores everything after that.
# Silently is the problem: two different long passwords sharing a 72-byte
# prefix would verify against each other's hash. We reject instead, so the
# limit is visible rather than a quiet weakness.
#
# Bytes, not characters - a password of emoji or Devanagari hits this far
# sooner than 72 keystrokes would suggest.
BCRYPT_MAX_BYTES = 72

# A floor, checked here rather than only in the Pydantic schema so that no
# code path can create an account with a trivial password.
MIN_PASSWORD_LENGTH = 8


class PasswordError(ValueError):
    """A password that cannot be hashed. The message is safe to show a user."""


def hash_password(plain_password: str) -> str:
    """
    Hash a password for storage.

    Returns the full passlib string - algorithm, cost, salt and digest - which
    is what goes in users.hashed_password. The salt is generated per call, so
    two accounts with the same password get different hashes.

    Raises PasswordError if the password is too short or too long to be hashed
    safely. Note what is NOT in the exception message: the password.
    """
    if len(plain_password) < MIN_PASSWORD_LENGTH:
        raise PasswordError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )

    encoded_length = len(plain_password.encode("utf-8"))
    if encoded_length > BCRYPT_MAX_BYTES:
        raise PasswordError(
            f"Password is too long: bcrypt hashes at most {BCRYPT_MAX_BYTES} bytes "
            f"and this one is {encoded_length}. Please choose a shorter password."
        )

    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Check a password against a stored hash. Returns a bool, never raises.

    passlib compares in constant time, so this does not leak how much of the
    password was correct through how long the comparison took.

    A malformed or empty stored hash is treated as "does not match" rather
    than allowed to raise: a corrupt row should fail the login, not return a
    500 that tells an attacker that this particular account exists and is
    interestingly broken.
    """
    if not hashed_password:
        return False

    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        # Logged without the password and without the hash - the fact that a
        # row is unreadable is useful; its contents are not.
        logger.warning("Stored password hash could not be parsed; treating as a failed login.")
        return False


# A hash of a value nobody can log in with, used to burn the same ~250ms
# bcrypt costs when the email does not exist. Without it, a missing account
# returns in microseconds and a real one takes a quarter of a second, which
# tells an attacker which addresses are registered just by timing the replies.
#
# Computed once at import so the cost lands on startup, not on a request.
_DUMMY_HASH = _pwd_context.hash("not-a-real-password-timing-equaliser")


def waste_password_time() -> None:
    """
    Spend the time a real password check would have taken.

    Called by the login endpoint when no user matches the email, so a failed
    login costs the same whether the account exists or not.
    """
    _pwd_context.verify("timing-equaliser", _DUMMY_HASH)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class TokenError(Exception):
    """A token that is missing, malformed, expired, or not correctly signed."""


def create_access_token(user_id: int, email: str) -> str:
    """
    Issue a signed token identifying one user.

    Claims, and one deliberate omission:

        sub   the user id, as a STRING. RFC 7519 requires sub to be a string;
              some libraries reject an integer outright, and there is nothing
              to gain by being the odd one out.
        email carried for convenience in logs and debugging. Not authority -
              nothing authorises off it.
        iat   issued-at, so a token's age is visible.
        exp   expiry. jose enforces this on decode; we do not check it by hand.

    The ROLE IS NOT IN THE TOKEN, on purpose. A token lives 24 hours, so a
    role baked into it would keep working for 24 hours after an admin revoked
    it - a demoted user would keep sign-off rights for a day. Instead the role
    is read from the database on every request, which makes a change take
    effect on the very next call.

    The usual argument against that is the extra query. It does not apply
    here: every protected endpoint already opens a database session and reads
    at least one row, so the user lookup is one more indexed primary-key hit
    on a connection that is already open.
    """
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Verify a token's signature and expiry, and return its claims.

    Raises TokenError for every failure mode - bad signature, expired, wrong
    algorithm, malformed, missing `sub`. The caller turns that into a single
    401 with one generic message: which of those went wrong is useful to an
    attacker probing the endpoint and useless to a legitimate user, whose only
    available action is to log in again.

    `algorithms=` is a list of exactly what we accept. This is the defence
    against the classic JWT algorithm-confusion attack, where a token is
    re-signed with "alg": "none" or with HMAC using the public key as the
    secret. jose only tries the algorithms named here.
    """
    if not token:
        raise TokenError("No token supplied.")

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        # The reason is logged for the operator but never returned to the
        # caller. No token contents in the log line either.
        logger.info("Rejected a token: %s", exc)
        raise TokenError("Could not validate credentials.") from exc

    if not claims.get("sub"):
        raise TokenError("Token is missing its subject claim.")

    return claims
