"""Credential scrubbing for anything shown to a human."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.redaction import PLACEHOLDER, redact, secrets_from


def test_replaces_every_occurrence():
    text = "key ABCD12345678 used; ABCD12345678 again"
    assert redact(text, ["ABCD12345678"]) == f"key {PLACEHOLDER} used; {PLACEHOLDER} again"


def test_is_case_insensitive():
    """Vendors do not always echo a key back in the case it was sent."""
    assert redact("saw abcd12345678", ["ABCD12345678"]) == f"saw {PLACEHOLDER}"


def test_leaves_surrounding_text_intact():
    """The message is the useful part; only the credential goes."""
    text = "We have detected your API key as ABCD12345678 and the limit is 25/day."
    result = redact(text, ["ABCD12345678"])
    assert "the limit is 25/day." in result
    assert "ABCD12345678" not in result


def test_ignores_short_values():
    """A 3-character 'secret' would redact ordinary prose."""
    assert redact("the cat sat", ["cat"]) == "the cat sat"


def test_handles_empty_and_missing_inputs():
    assert redact(None, ["ABCD12345678"]) is None
    assert redact("", ["ABCD12345678"]) == ""
    assert redact("untouched", [None, ""]) == "untouched"


def test_secrets_from_collects_every_credential():
    settings = SimpleNamespace(
        finnhub_api_key="finnhub-key-value",
        alpha_vantage_api_key="alpha-key-value",
        auth_password="password-value",
        secret_key="signing-key-value",
        smtp_password="smtp-value",
        slack_webhook_url="https://hooks.slack.com/services/XXX",
    )
    assert len(secrets_from(settings)) == 6


def test_secrets_from_skips_unset_values():
    settings = SimpleNamespace(finnhub_api_key="only-this-one", alpha_vantage_api_key=None)
    assert secrets_from(settings) == ["only-this-one"]


# --- Log output ---------------------------------------------------------------
# The leak these cover reached a chat window through a *library* log line:
# httpx logs every request URL at INFO, so "?apikey=..." was written by code
# that has never heard of our settings. Care at our own call sites cannot fix
# that, and would not fix the next dependency either.

import logging

from app.logging_config import LOG_FORMAT, RedactingFormatter


def _formatted(message: str, *args, secrets=("XHS833JZWR5LZD0Z",)) -> str:
    formatter = RedactingFormatter(LOG_FORMAT, tuple(secrets))
    record = logging.LogRecord(
        name="test", level=logging.WARNING, pathname=__file__, lineno=1,
        msg=message, args=args, exc_info=None,
    )
    return formatter.format(record)


def test_a_key_in_a_logged_request_url_is_removed():
    """Verbatim reproduction of the httpx line that leaked the key."""
    out = _formatted(
        "HTTP Request: GET "
        "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=1093.HK"
        "&apikey=XHS833JZWR5LZD0Z \"HTTP/1.1 200 OK\""
    )

    assert "XHS833JZWR5LZD0Z" not in out
    assert "apikey=[REDACTED]" in out
    # The rest of the line has to survive or the log stops being useful.
    assert "GLOBAL_QUOTE" in out and "1093.HK" in out


def test_a_key_echoed_back_by_the_vendor_is_removed():
    """The other half of the same leak: Alpha Vantage quotes the key at you."""
    out = _formatted(
        "Alpha Vantage throttled at %s: %s",
        "1093.HK",
        "We have detected your API key as XHS833JZWR5LZD0Z and our standard "
        "API rate limit is 25 requests per day.",
    )

    assert "XHS833JZWR5LZD0Z" not in out
    assert "rate limit is 25 requests per day" in out


def test_a_finnhub_token_in_a_url_is_removed():
    out = _formatted(
        "HTTP Request: GET https://finnhub.io/api/v1/quote?symbol=BHC.TO"
        "&token=d9soje9r01qopv48g0cgd9soje9r01qopv48g0d0 \"HTTP/1.1 403 Forbidden\"",
        secrets=(),
    )

    assert "d9soje9r01qopv48g0cg" not in out
    assert "token=[REDACTED]" in out
    assert "403 Forbidden" in out


def test_an_unknown_credential_is_masked_by_parameter_name():
    """The formatter cannot know every secret; the parameter name is the clue.

    A token embedded in a configured feed URL was never registered as a secret,
    so value-based redaction alone would print it in full.
    """
    out = _formatted(
        "Fetching https://wire.example.com/rss?client_secret=never-seen-before",
        secrets=(),
    )

    assert "never-seen-before" not in out


def test_a_credential_inside_a_traceback_is_removed():
    """Formatting first is what reaches the traceback, several frames deep."""
    formatter = RedactingFormatter(LOG_FORMAT, ("XHS833JZWR5LZD0Z",))
    try:
        raise ValueError("connect failed for ?apikey=XHS833JZWR5LZD0Z")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="request failed", args=(), exc_info=sys.exc_info(),
        )

    out = formatter.format(record)

    assert "XHS833JZWR5LZD0Z" not in out
    assert "ValueError" in out


def test_an_ordinary_parameter_ending_in_key_is_not_mangled():
    """`\\b` guards against sortkey= and monkey= being read as key=."""
    out = _formatted("GET /items?sortkey=name&count=10", secrets=())

    assert "sortkey=name" in out
