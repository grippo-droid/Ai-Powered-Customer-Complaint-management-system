"""
Authentication - three endpoints.

    POST /auth/signup   create an account, return a token
    POST /auth/login    exchange email + password for a token
    GET  /auth/me       who is this token for?

The dependency that other routers use to require a logged-in user lives here
too, at the bottom: `get_current_user`. Putting it beside the endpoints that
issue tokens keeps the whole authentication story in one readable file.

No password or signing key is handled directly in this module - both are
delegated to app/security.py. Nothing here logs a password, and no response
model in the project carries a hash.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserRole
from app.schemas import LoginRequest, SignupRequest, TokenResponse, UserOut
from app.security import (
    PasswordError,
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
    waste_password_time,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# auto_error=False so a missing header reaches our own handler rather than
# producing FastAPI's default 403. A request with no credentials deserves 401
# ("you are not authenticated"), not 403 ("you are, but you may not do this") -
# and the frontend routes on that difference: 401 means show the login page.
_bearer_scheme = HTTPBearer(auto_error=False)

# One message for "no such email" and for "wrong password", because saying
# which one was wrong turns the login form into a tool for discovering who
# has an account.
_INVALID_CREDENTIALS = "Incorrect email or password."


def _normalise_email(email: str) -> str:
    """
    Lowercase and trim.

    Email domains are case-insensitive in practice, and a user who signs up as
    `Ravi@Example.com` and later types `ravi@example.com` means the same
    account. Normalising on the way IN means the UNIQUE index does the right
    thing, rather than storing two rows that look identical to a human.
    """
    return email.strip().lower()


# ---------------------------------------------------------------------------
# POST /auth/signup
# ---------------------------------------------------------------------------


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """
    Create an account and return a token, so signup logs you straight in.

    Self-service role selection is a portfolio-demo affordance, not a pattern
    to copy: it lets a reviewer try both sides of the permission model without
    an admin tool. In a real QMS, a qa_lead account would be provisioned by an
    administrator and never self-claimed.
    """
    email = _normalise_email(payload.email)

    try:
        hashed = hash_password(payload.password)
    except PasswordError as exc:
        # The message describes the RULE that was broken, never the password.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    user = User(email=email, hashed_password=hashed, role=UserRole(payload.role))
    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        # The UNIQUE index is what actually decides this, not a prior SELECT.
        # Two simultaneous signups for one address would both pass a
        # "does this email exist?" check and then both insert; only the
        # constraint makes that impossible, so the constraint is what we
        # handle. This does reveal that an address is registered - unavoidable
        # for any signup form, and an acceptable trade for a usable error.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    db.refresh(user)
    logger.info("Created account %s with role %s", user.email, user.role.value)

    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        user=UserOut.model_validate(user),
    )


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """
    Exchange credentials for a token.

    Both failure modes - unknown email, wrong password - return the same 401
    with the same message and take the same amount of time. See the call to
    waste_password_time below for why the timing matters.
    """
    email = _normalise_email(payload.email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if user is None:
        # Burn the ~250ms a bcrypt verify would have cost. Returning
        # immediately here would make an unknown address answer in
        # microseconds while a real one takes a quarter of a second, which
        # tells anyone with a stopwatch exactly which addresses are registered.
        waste_password_time()
        logger.info("Failed login for an unregistered address.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(payload.password, user.hashed_password):
        # Log the email, never the attempted password - a failed login is very
        # often a correct password for a different account, and writing those
        # into a log file is how log files become a credential store.
        logger.info("Failed login for %s.", user.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info("Successful login for %s (%s)", user.email, user.role.value)
    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        user=UserOut.model_validate(user),
    )


# ---------------------------------------------------------------------------
# The dependency every protected route uses
# ---------------------------------------------------------------------------


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolve `Authorization: Bearer <token>` to a live User row, or raise 401.

    The row is fetched every request rather than trusting claims in the token.
    The token proves WHO is calling; the database decides what they currently
    are. That is what makes a role change or a deleted account take effect on
    the next request instead of whenever a 24-hour token happens to expire.

    Returns the ORM object, not a schema, because callers need `user.role` to
    make authorisation decisions and `user.id` for audit fields.
    """
    unauthorised = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Please log in.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or not credentials.credentials:
        raise unauthorised

    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError:
        # Every token failure - expired, forged, malformed, wrong algorithm -
        # collapses to one 401 with one message. Which one it was is useful
        # only to someone probing the endpoint; a legitimate user's next step
        # is "log in again" regardless.
        raise unauthorised

    # `sub` was written as a string (RFC 7519). A token whose subject is not
    # an integer was not minted by create_access_token.
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        raise unauthorised

    user = db.get(User, user_id)
    if user is None:
        # A validly signed token for an account that no longer exists. The
        # signature is genuine, so this is not an attack - it is a deleted
        # user holding a token that has not expired yet. Still a 401.
        logger.info("Token referenced user id %s, which no longer exists.", user_id)
        raise unauthorised

    return user


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Who am I?

    The frontend calls this on load to decide whether a stored token is still
    good and, if so, what to render - including whether to enable the commit
    button. A 401 here is the signal to clear the token and show the login
    page.
    """
    return current_user
