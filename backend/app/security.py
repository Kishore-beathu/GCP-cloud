"""Single-user authentication.

The platform is a personal tool, so it needs one thing from auth: prove the
caller knows the configured password, then let them act for a bounded window.
That is a signed-token problem, not an identity-provider problem, so this is
built on the standard library — HMAC-SHA256 over ``subject|expiry``, with the
signing key from settings. No new dependencies, nothing to rotate but a secret.

Two rules keep it honest:

* **Auth turns itself on.** Setting ``AUTH_PASSWORD`` enables enforcement.
  There is no separate flag to forget.
* **Production refuses to run open.** ``require_secure_configuration`` raises at
  startup if ``ENVIRONMENT=production`` without a password, so a public deploy
  cannot silently expose the API.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

TOKEN_VERSION = "v1"
_bearer = HTTPBearer(auto_error=False)


class ConfigurationError(RuntimeError):
    """Raised when the deployment is configured in an unsafe way."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return _b64(digest)


def auth_enabled(settings: Settings | None = None) -> bool:
    """Auth is on exactly when a password is configured."""
    settings = settings or get_settings()
    return bool(settings.auth_password)


def require_secure_configuration(settings: Settings | None = None) -> None:
    """Fail fast on a production deployment that would be publicly writable."""
    settings = settings or get_settings()
    if settings.environment.lower() != "production":
        return

    problems = []
    if not settings.auth_password:
        problems.append(
            "AUTH_PASSWORD is not set — the API would accept unauthenticated "
            "writes, admin ingestion triggers, and portfolio changes"
        )
    if not settings.secret_key:
        problems.append("SECRET_KEY is not set — session tokens could be forged")

    if problems:
        raise ConfigurationError(
            "Refusing to start in production with an insecure configuration:\n  - "
            + "\n  - ".join(problems)
        )


def verify_password(candidate: str, settings: Settings | None = None) -> bool:
    """Constant-time password check."""
    settings = settings or get_settings()
    if not settings.auth_password:
        return False
    return hmac.compare_digest(candidate, settings.auth_password)


def create_token(subject: str = "local", settings: Settings | None = None) -> tuple[str, int]:
    """Issue a signed token. Returns ``(token, expires_at_unix)``."""
    settings = settings or get_settings()
    if not settings.secret_key:
        raise ConfigurationError("SECRET_KEY must be set to issue tokens")

    expires_at = int(time.time()) + settings.auth_token_ttl_seconds
    payload = f"{TOKEN_VERSION}.{_b64(subject.encode())}.{expires_at}"
    return f"{payload}.{_sign(payload, settings.secret_key)}", expires_at


def verify_token(token: str, settings: Settings | None = None) -> str:
    """Return the token's subject, or raise ValueError if it is not usable."""
    settings = settings or get_settings()
    if not settings.secret_key:
        raise ValueError("SECRET_KEY is not configured")

    parts = token.split(".")
    if len(parts) != 4:
        raise ValueError("Malformed token")

    version, subject_b64, expiry_raw, signature = parts
    if version != TOKEN_VERSION:
        raise ValueError("Unsupported token version")

    payload = f"{version}.{subject_b64}.{expiry_raw}"
    # Compare before parsing the expiry so an attacker learns nothing from
    # the ordering of our checks.
    if not hmac.compare_digest(signature, _sign(payload, settings.secret_key)):
        raise ValueError("Bad signature")

    try:
        expires_at = int(expiry_raw)
    except ValueError as exc:
        raise ValueError("Bad expiry") from exc
    if expires_at < time.time():
        raise ValueError("Token expired")

    try:
        return _unb64(subject_b64).decode()
    except Exception as exc:  # noqa: BLE001 - any decode failure is a bad token
        raise ValueError("Bad subject") from exc


def generate_secret_key() -> str:
    """A fresh signing key, for operators bootstrapping a deployment."""
    return secrets.token_urlsafe(48)


# --- FastAPI wiring ---------------------------------------------------------


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Dependency guarding every route that is not public.

    When no password is configured (local development) this is a pass-through
    that reports the caller as ``local``.
    """
    settings = get_settings()
    if not auth_enabled(settings):
        return "local"

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to use this API",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return verify_token(credentials.credentials, settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Session is not valid: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def authorize_websocket(token: str | None) -> str | None:
    """Authorise a WebSocket handshake.

    Browsers cannot set headers on a WebSocket, so the token arrives as a query
    parameter. Returns the subject, or None when the connection should be
    rejected.
    """
    settings = get_settings()
    if not auth_enabled(settings):
        return "local"
    if not token:
        return None
    try:
        return verify_token(token, settings)
    except ValueError:
        return None


def rate_limit_key(request: Request) -> str:
    """Best-effort client identity for throttling, honouring proxy headers."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
