"""Authentication: token maths, route guards, and the production safety check."""

from __future__ import annotations

import time

import pytest

from app.config import Settings, get_settings
from app.security import (
    ConfigurationError,
    authorize_websocket,
    create_token,
    generate_secret_key,
    require_secure_configuration,
    verify_password,
    verify_token,
)

SECRET = "test-secret-key-for-signing-only"


def _settings(**overrides) -> Settings:
    base = {"secret_key": SECRET, "auth_password": "hunter2"}
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- Tokens -----------------------------------------------------------------


def test_round_trip():
    s = _settings()
    token, expires_at = create_token("local", s)
    assert verify_token(token, s) == "local"
    assert expires_at > time.time()


def test_tampered_payload_is_rejected():
    s = _settings()
    token, _ = create_token("local", s)
    version, subject, expiry, signature = token.split(".")

    # Extend the expiry without re-signing.
    forged = f"{version}.{subject}.{int(expiry) + 100_000}.{signature}"
    with pytest.raises(ValueError, match="Bad signature"):
        verify_token(forged, s)


def test_token_signed_with_another_key_is_rejected():
    token, _ = create_token("local", _settings())
    with pytest.raises(ValueError, match="Bad signature"):
        verify_token(token, _settings(secret_key="a-different-secret-entirely"))


def test_expired_token_is_rejected():
    s = _settings(auth_token_ttl_seconds=-1)
    token, _ = create_token("local", s)
    with pytest.raises(ValueError, match="expired"):
        verify_token(token, s)


@pytest.mark.parametrize("bad", ["", "nonsense", "a.b.c", "v1.x.y.z.extra", "v2.aaa.111.sig"])
def test_malformed_tokens_are_rejected(bad):
    with pytest.raises(ValueError):
        verify_token(bad, _settings())


def test_token_issuing_requires_a_secret():
    with pytest.raises(ConfigurationError):
        create_token("local", _settings(secret_key=None))


def test_generated_secret_is_long_and_unique():
    a, b = generate_secret_key(), generate_secret_key()
    assert a != b
    assert len(a) >= 40


# --- Passwords --------------------------------------------------------------


def test_password_check():
    s = _settings()
    assert verify_password("hunter2", s) is True
    assert verify_password("Hunter2", s) is False
    assert verify_password("", s) is False


def test_password_check_fails_closed_when_unset():
    assert verify_password("anything", _settings(auth_password=None)) is False


# --- Production safety ------------------------------------------------------


def test_production_without_password_refuses_to_start():
    with pytest.raises(ConfigurationError, match="AUTH_PASSWORD"):
        require_secure_configuration(
            Settings(_env_file=None, environment="production", auth_password=None)
        )


def test_production_without_secret_refuses_to_start():
    with pytest.raises(ConfigurationError, match="SECRET_KEY"):
        require_secure_configuration(
            Settings(
                _env_file=None,
                environment="production",
                auth_password="hunter2",
                secret_key=None,
            )
        )


def test_production_with_both_set_is_allowed():
    require_secure_configuration(_settings(environment="production"))


def test_development_without_password_is_allowed():
    require_secure_configuration(Settings(_env_file=None, environment="development"))


# --- WebSocket handshake ----------------------------------------------------


def test_websocket_open_when_auth_is_off(monkeypatch):
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    get_settings.cache_clear()
    assert authorize_websocket(None) == "local"


def test_websocket_requires_a_valid_token(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "hunter2")
    monkeypatch.setenv("SECRET_KEY", SECRET)
    get_settings.cache_clear()

    assert authorize_websocket(None) is None
    assert authorize_websocket("garbage") is None
    token, _ = create_token("local", get_settings())
    assert authorize_websocket(token) == "local"


# --- Route guards -----------------------------------------------------------

pytestmark_async = pytest.mark.asyncio


@pytest.fixture
def secured(monkeypatch):
    """Turn auth on for the app under test."""
    monkeypatch.setenv("AUTH_PASSWORD", "hunter2")
    monkeypatch.setenv("SECRET_KEY", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health_stays_public(client, secured, seeded_stocks):
    """Uptime checks must not need a credential."""
    assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/news"),
        ("get", "/stocks"),
        ("get", "/alerts"),
        ("get", "/portfolios"),
        ("get", "/jobs/status"),
        ("post", "/admin/ingest/sec"),
        ("post", "/admin/ingest/finnhub"),
        ("post", "/admin/seed"),
    ],
)
async def test_protected_routes_reject_anonymous_callers(client, secured, method, path):
    response = await getattr(client, method)(path)
    assert response.status_code == 401, f"{method.upper()} {path} was reachable anonymously"


@pytest.mark.asyncio
async def test_login_then_use_the_token(client, secured, seeded_stocks):
    bad = await client.post("/auth/login", json={"password": "wrong"})
    assert bad.status_code == 401

    good = await client.post("/auth/login", json={"password": "hunter2"})
    assert good.status_code == 200
    token = good.json()["token"]

    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/stocks", headers=headers)).status_code == 200
    assert (await client.get("/auth/verify", headers=headers)).json() == {"subject": "local"}


@pytest.mark.asyncio
async def test_garbage_token_is_rejected(client, secured):
    response = await client.get("/stocks", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_endpoint_reports_state(client, secured):
    anon = (await client.get("/auth/session")).json()
    assert anon == {"authenticated": False, "auth_required": True, "subject": None}

    token = (await client.post("/auth/login", json={"password": "hunter2"})).json()["token"]
    signed_in = (
        await client.get("/auth/session", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert signed_in["authenticated"] is True
    assert signed_in["subject"] == "local"


@pytest.mark.asyncio
async def test_login_is_throttled_after_repeated_failures(client, secured, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "3")
    get_settings.cache_clear()
    from app.routers import auth as auth_router

    auth_router._failures.clear()

    for _ in range(3):
        assert (await client.post("/auth/login", json={"password": "no"})).status_code == 401

    blocked = await client.post("/auth/login", json={"password": "no"})
    assert blocked.status_code == 429
    # The correct password is refused too while the throttle is active.
    assert (await client.post("/auth/login", json={"password": "hunter2"})).status_code == 429
    auth_router._failures.clear()


@pytest.mark.asyncio
async def test_everything_open_without_a_password(client, seeded_stocks):
    """Local development keeps working with no configuration at all."""
    get_settings.cache_clear()
    assert (await client.get("/stocks")).status_code == 200
    assert (await client.get("/auth/session")).json()["auth_required"] is False
    # Sign-in is meaningless without a password configured.
    assert (await client.post("/auth/login", json={"password": "x"})).status_code == 400
