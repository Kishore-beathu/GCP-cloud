"""Preflight check: why won't the server start?

Uvicorn reports a failed lifespan as `Application startup failed. Exiting.`
after a long traceback, which is easy to truncate and hard to read. This runs
the same startup steps one at a time and says which one failed and what to do
about it.

    python -m scripts.doctor          # from backend/, using the venv's python

Output is designed to be safe to paste into a chat or an issue: passwords,
tokens and API keys are reported as present/absent and never printed.
"""

from __future__ import annotations

import asyncio
import platform
import sys
from urllib.parse import urlsplit

# Checks are independent: a database failure should not hide a missing API key,
# so every check runs and the exit code reflects the worst result.
OK = "  ok  "
WARN = " warn "
FAIL = " FAIL "

_failed = False
_notes: list[str] = []


def report(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label}" + (f" - {detail}" if detail else ""))


def fail(label: str, detail: str, fix: str) -> None:
    global _failed
    _failed = True
    report(FAIL, label, detail)
    _notes.append(fix)


def redact_database_url(url: str) -> str:
    """Show host, port and database name; never the user or password."""
    if url.startswith("sqlite"):
        return url  # A local file path, with no credentials to hide.
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    if not parts.hostname:
        return f"{parts.scheme}://<no host>"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://<credentials>@{parts.hostname}{port}{parts.path}"


def check_python() -> None:
    report(OK, "python", f"{platform.python_version()} ({sys.executable})")


def check_imports() -> bool:
    try:
        import fastapi  # noqa: F401
        import sqlalchemy  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:
        fail(
            "dependencies",
            str(exc),
            "Install them into THIS interpreter:\n"
            "    .\\.venv\\Scripts\\python -m pip install -r requirements.txt",
        )
        return False
    report(OK, "dependencies", "fastapi, sqlalchemy and uvicorn import")
    return True


def load_settings():
    from pydantic import ValidationError

    try:
        from app.config import get_settings

        return get_settings()
    except ValidationError as exc:
        fail(
            "config",
            f"{exc.error_count()} invalid value(s) in .env",
            f"Fix the values pydantic rejected:\n{exc}",
        )
    except Exception as exc:  # SettingsError and friends
        fail(
            "config",
            f"{type(exc).__name__}: {exc}",
            "A value in backend/.env cannot be parsed. Comment out the line "
            "named in the error and start again.",
        )
    return None


def check_env_file() -> set[str]:
    """Report which settings the .env actually sets. Names only, never values.

    A .env copied from the example loads perfectly while leaving every real
    value at its placeholder, so "config loaded" is not the same as "config
    filled in". Listing the keys makes the gap obvious.
    """
    from pathlib import Path

    path = Path(".env")
    if not path.is_file():
        report(WARN, ".env", "not found - every setting is using its default")
        return set()

    keys = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            keys.add(stripped.split("=", 1)[0].strip().upper())
    report(WARN if not keys else OK, ".env", f"{len(keys)} setting(s): {_summary(keys)}")
    return keys


def _summary(keys: set[str]) -> str:
    shown = sorted(keys)
    if len(shown) > 8:
        return ", ".join(shown[:8]) + f", +{len(shown) - 8} more"
    return ", ".join(shown)


def check_driver(settings, env_keys: set[str]) -> None:
    """The most common .env mistake: a sync driver URL in an async app."""
    url = settings.database_url
    scheme = url.split("://", 1)[0]
    if scheme in {"postgresql", "postgres"}:
        fail(
            "database url",
            f"'{scheme}://' is the synchronous driver",
            "This app needs an async driver. Change the DATABASE_URL scheme to "
            "postgresql+asyncpg://",
        )
    elif scheme == "sqlite":
        fail(
            "database url",
            "'sqlite://' is the synchronous driver",
            "Use sqlite+aiosqlite:/// instead.",
        )
    elif "DATABASE_URL" not in env_keys:
        report(
            WARN,
            "database url",
            f"not set in .env, using the built-in default {redact_database_url(url)}",
        )
    else:
        report(OK, "database url", redact_database_url(url))


async def check_connection(settings, env_keys: set[str]) -> None:
    from sqlalchemy import text

    from app.database import dispose_engine, get_engine

    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        report(OK, "database", "connected, SELECT 1 succeeded")
    except Exception as exc:
        fail(
            "database",
            f"{type(exc).__name__}: {exc}",
            _database_fix(exc, settings, env_keys),
        )
    finally:
        await dispose_engine()


def _database_fix(exc: Exception, settings, env_keys: set[str]) -> str:
    """Map the common connection failures onto their actual cause."""
    text = f"{type(exc).__name__}: {exc}".lower()
    host = urlsplit(settings.database_url).hostname

    # Check this before the generic network advice: a refused connection to
    # localhost is not a firewall, it is nothing listening on that port.
    if "refused" in text and host in {"localhost", "127.0.0.1", "::1"}:
        unset = "DATABASE_URL" not in env_keys
        return (
            (
                "DATABASE_URL is not set in backend/.env, so the app fell back to "
                "a local PostgreSQL that is not running.\n   "
                if unset
                else "Nothing is listening on that port - no local PostgreSQL "
                "server is running.\n   "
            )
            + "Set DATABASE_URL in backend/.env to one of:\n"
            "     sqlite+aiosqlite:///./dev.db      (a local file, no server needed)\n"
            "     postgresql+asyncpg://...pooler.supabase.com:5432/postgres"
        )
    if "asyncpg" in text and "no module" in text:
        return "asyncpg is not installed: python -m pip install -r requirements.txt"
    if "password authentication failed" in text:
        return (
            "The database password in .env is wrong. On Supabase, reset it under "
            "Project Settings > Database and update DATABASE_URL."
        )
    if "does not exist" in text and "database" in text:
        return "The database named at the end of DATABASE_URL does not exist."
    if any(word in text for word in ("timeout", "timed out", "unreachable", "network")):
        return (
            "The host is not reachable. Check the hostname, and whether a "
            "corporate network or firewall blocks outbound port "
            f"{urlsplit(settings.database_url).port or 5432}."
        )
    if any(
        word in text
        for word in (
            "gaierror",
            "name or service not known",
            "no address associated",
            "getaddrinfo",
        )
    ):
        return "The hostname in DATABASE_URL does not resolve. Check it for typos."
    if "prepared statement" in text:
        return (
            "Supabase transaction-pooler symptom. Set DB_STATEMENT_CACHE_SIZE=0 "
            "in .env, or use the session pooler on port 5432."
        )
    return (
        "Check DATABASE_URL in backend/.env. For Supabase use the Session pooler "
        "URL (port 5432) with the postgresql+asyncpg:// scheme."
    )


async def check_schema() -> None:
    """Compare the models against the live database.

    A database created before a column was added keeps working until the first
    query names that column, and then every endpoint touching that table
    returns 500 with the reason only in the server log. This is the check that
    turns "Internal server error" into a command to run.
    """
    from app.database import dispose_engine, missing_columns

    try:
        drift = await missing_columns()
    except Exception as exc:
        report(WARN, "schema", f"could not be inspected: {type(exc).__name__}: {exc}")
        return
    finally:
        await dispose_engine()

    if not drift:
        report(OK, "schema", "matches the models")
        return

    described = "; ".join(f"{table}: {', '.join(cols)}" for table, cols in drift.items())
    fail(
        "schema",
        f"missing column(s) - {described}",
        "The database predates a schema change, and CREATE_TABLES_ON_STARTUP "
        "only creates missing tables, never alters existing ones. Apply the "
        "migrations:\n"
        "     python -m alembic stamp 3e9022270db3   # only if never used before\n"
        "     python -m alembic upgrade head",
    )


def check_secrets(settings) -> None:
    """Report presence only. Never print a key."""
    for label, value, consequence in (
        ("FINNHUB_API_KEY", settings.finnhub_api_key, "no company news, no live prices"),
        (
            "ALPHA_VANTAGE_API_KEY",
            settings.alpha_vantage_api_key,
            "no quotes or price history",
        ),
    ):
        if value:
            report(OK, label, f"set ({len(value)} chars)")
        else:
            report(WARN, label, f"not set - {consequence}")

    if settings.auth_password:
        report(OK, "AUTH_PASSWORD", "set - requests need a bearer token")
    else:
        report(WARN, "AUTH_PASSWORD", "not set - the API is open (local dev only)")

    if settings.environment == "production" and not (
        settings.auth_password and settings.secret_key
    ):
        fail(
            "production config",
            "ENVIRONMENT=production without AUTH_PASSWORD and SECRET_KEY",
            "Set both, or set ENVIRONMENT=development for local work.",
        )


async def main() -> int:
    print("Trading agent preflight\n")
    check_python()
    if not check_imports():
        _summarise()
        return 1

    settings = load_settings()
    if settings is None:
        _summarise()
        return 1
    report(OK, "config", f"loaded, environment={settings.environment}")
    env_keys = check_env_file()
    report(OK, "cors origins", f"{len(settings.cors_origins)} allowed")

    check_driver(settings, env_keys)
    if not _failed:
        await check_connection(settings, env_keys)
        await check_schema()
    check_secrets(settings)

    _summarise()
    return 1 if _failed else 0


def _summarise() -> None:
    print()
    if not _failed:
        print("All checks passed. Start the server with:")
        print("    .\\.venv\\Scripts\\python -m uvicorn app.main:app --port 8000")
        return
    print("What to fix:\n")
    for index, note in enumerate(_notes, start=1):
        print(f"{index}. {note}\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
