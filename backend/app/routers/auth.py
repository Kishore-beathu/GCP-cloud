"""Sign-in endpoints.

Login is the one unauthenticated write in the system, so it carries its own
throttle: a fixed number of failures per client per window. The counter lives
in memory, which matches the single-instance deployment the scheduler already
requires (see docs/DEPLOYMENT.md).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.security import (
    auth_enabled,
    create_token,
    rate_limit_key,
    require_auth,
    verify_password,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# client key -> timestamps of recent failed attempts
_failures: dict[str, list[float]] = defaultdict(list)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    token: str
    expires_at: int
    token_type: str = "bearer"


class SessionInfo(BaseModel):
    authenticated: bool
    auth_required: bool
    subject: str | None = None


def _record_failure(key: str, window: int) -> None:
    now = time.time()
    _failures[key] = [t for t in _failures[key] if now - t < window]
    _failures[key].append(now)


def _too_many_failures(key: str, limit: int, window: int) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < window]
    _failures[key] = recent
    return len(recent) >= limit


@router.post("/login", response_model=LoginResponse, summary="Exchange the password for a token")
async def login(payload: LoginRequest, request: Request) -> LoginResponse:
    settings = get_settings()
    if not auth_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This deployment has no password configured, so sign-in is not used",
        )

    client = rate_limit_key(request)
    if _too_many_failures(client, settings.login_max_attempts, settings.login_window_seconds):
        logger.warning("Login throttled for %s", client)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Wait a few minutes and try again.",
        )

    if not verify_password(payload.password, settings):
        _record_failure(client, settings.login_window_seconds)
        logger.warning("Failed login from %s", client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="That password is not correct"
        )

    _failures.pop(client, None)
    token, expires_at = create_token("local", settings)
    logger.info("Successful login from %s", client)
    return LoginResponse(token=token, expires_at=expires_at)


@router.get("/session", response_model=SessionInfo, summary="Who am I, and is auth required?")
async def session(request: Request) -> SessionInfo:
    """Unauthenticated on purpose: the UI needs this to decide whether to
    show a sign-in screen before it has a token."""
    settings = get_settings()
    if not auth_enabled(settings):
        return SessionInfo(authenticated=True, auth_required=False, subject="local")

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        from app.security import verify_token

        try:
            subject = verify_token(header.split(" ", 1)[1], settings)
            return SessionInfo(authenticated=True, auth_required=True, subject=subject)
        except ValueError:
            pass
    return SessionInfo(authenticated=False, auth_required=True)


@router.get("/verify", summary="Confirm the current token is still valid")
async def verify(subject: str = Depends(require_auth)) -> dict:
    return {"subject": subject}
