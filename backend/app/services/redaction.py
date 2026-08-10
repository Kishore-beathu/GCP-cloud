"""Strip credentials out of text that is about to be shown to someone.

Diagnostics exist to be pasted into a chat or an issue, which makes any
credential inside them a disclosure. Our own code never interpolates a key
into a message — but upstream vendors do. Alpha Vantage's rate-limit
notice reads:

    We have detected your API key as ABCD1234EFGH5678 and our standard API
    rate limit is 25 requests per day.

Passing that through verbatim leaks the key to whoever reads the output. Every
vendor-supplied string must go through :func:`redact` before it is returned.
"""

from __future__ import annotations

from collections.abc import Iterable

PLACEHOLDER = "[REDACTED]"

# Shorter values produce false positives against ordinary prose; real API keys
# from every vendor here are comfortably longer.
MIN_SECRET_LENGTH = 8


def redact(text: str | None, secrets: Iterable[str | None]) -> str | None:
    """Replace every occurrence of each secret in ``text``.

    Case-insensitive, because vendors do not always echo a key back in the
    case it was sent.
    """
    if not text:
        return text

    for secret in secrets:
        if not secret or len(secret) < MIN_SECRET_LENGTH:
            continue
        text = _replace_case_insensitive(text, secret, PLACEHOLDER)
    return text


def _replace_case_insensitive(haystack: str, needle: str, replacement: str) -> str:
    lowered_haystack = haystack.lower()
    lowered_needle = needle.lower()
    if lowered_needle not in lowered_haystack:
        return haystack

    out: list[str] = []
    start = 0
    while True:
        index = lowered_haystack.find(lowered_needle, start)
        if index == -1:
            out.append(haystack[start:])
            return "".join(out)
        out.append(haystack[start:index])
        out.append(replacement)
        start = index + len(needle)


def secrets_from(settings) -> list[str]:
    """Every credential the settings carry, for use as a redaction list."""
    candidates = (
        getattr(settings, "finnhub_api_key", None),
        getattr(settings, "alpha_vantage_api_key", None),
        getattr(settings, "auth_password", None),
        getattr(settings, "secret_key", None),
        getattr(settings, "smtp_password", None),
        getattr(settings, "slack_webhook_url", None),
    )
    return [value for value in candidates if value]
