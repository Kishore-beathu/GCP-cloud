"""Settings loading, exercised through a real .env file.

Every other test constructs `Settings(_env_file=None, ...)`, which bypasses the
dotenv source entirely. That gap let a startup-breaking bug ship: pydantic
JSON-decodes complex fields read from a .env *before* validators run, so a
comma-separated `CORS_ORIGINS=` line — exactly what .env.example shipped —
raised SettingsError and the app refused to boot. These tests write a real file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

# Paths inside .env.example are relative to backend/.
ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


def test_comma_separated_lists_load_from_dotenv(tmp_path: Path) -> None:
    env = write_env(
        tmp_path,
        "CORS_ORIGINS=http://localhost:3000,http://localhost:5173\n"
        "EMAIL_TO=you@example.com, desk@example.com\n",
    )

    settings = Settings(_env_file=env)

    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:5173"]
    # Surrounding whitespace is stripped, so " desk@…" is a usable address.
    assert settings.email_to == ["you@example.com", "desk@example.com"]


def test_json_list_syntax_still_loads(tmp_path: Path) -> None:
    """The JSON form was the only one that worked before; don't break it."""
    env = write_env(tmp_path, 'CORS_ORIGINS=["https://app.example.com"]\n')

    assert Settings(_env_file=env).cors_origins == ["https://app.example.com"]


def test_single_value_needs_no_comma(tmp_path: Path) -> None:
    env = write_env(tmp_path, "CORS_ORIGINS=https://app.example.com\n")

    assert Settings(_env_file=env).cors_origins == ["https://app.example.com"]


def test_empty_value_falls_back_to_an_empty_list(tmp_path: Path) -> None:
    """An `EMAIL_TO=` line left blank must not produce [''] and mail nobody."""
    env = write_env(tmp_path, "EMAIL_TO=\n")

    assert Settings(_env_file=env).email_to == []


def test_unset_lists_keep_their_defaults(tmp_path: Path) -> None:
    settings = Settings(_env_file=write_env(tmp_path, "LOG_LEVEL=DEBUG\n"))

    assert settings.log_level == "DEBUG"
    assert "http://localhost:5173" in settings.cors_origins
    assert "http://127.0.0.1:5173" in settings.cors_origins
    assert settings.email_to == []


def test_environment_variables_also_accept_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloud Run and friends pass plain env vars, not a .env file."""
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com,https://b.example.com")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["https://a.example.com", "https://b.example.com"]


def test_env_example_loads_as_written(tmp_path: Path) -> None:
    """`cp .env.example .env` must produce a bootable configuration.

    This is the regression that matters: the file the README tells people to
    copy is itself the test fixture.
    """
    env = write_env(tmp_path, ENV_EXAMPLE.read_text(encoding="utf-8"))

    settings = Settings(_env_file=env)

    assert settings.cors_origins  # parsed, non-empty
    assert all(origin.startswith("http") for origin in settings.cors_origins)
    # The example shipped a localhost PostgreSQL URL, so a fresh copy could not
    # reach a database on a machine that had never installed one. It now
    # defaults to a local file, which needs no server.
    assert settings.database_url.startswith("sqlite+aiosqlite:")


def test_env_example_uses_an_async_driver(tmp_path: Path) -> None:
    """A sync driver URL fails at connect time, long after the copy."""
    env = write_env(tmp_path, ENV_EXAMPLE.read_text(encoding="utf-8"))

    scheme = Settings(_env_file=env).database_url.split("://", 1)[0]

    assert scheme in {"sqlite+aiosqlite", "postgresql+asyncpg"}
