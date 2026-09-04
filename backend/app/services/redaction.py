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

import re
from collections.abc import Iterable

PLACEHOLDER = "[REDACTED]"

# Credentials also travel in query strings, and httpx logs every request URL at
# INFO — so a key reaches the log through a library that knows nothing about
# our settings. Matching the parameter name instead of the value catches keys
# this process was never told about: a token embedded in a configured feed URL,
# or a vendor that redirects with the credential attached.
#
# `\b` before each name stops `sortkey=` and `monkey=` matching `key=`.
_CREDENTIAL_PARAM_RE = re.compile(
    r"\b(apikey|api_key|apitoken|api_token|access_token|auth_token|token|key|"
    r"secret|client_secret|password|passwd|pwd|signature|sig)=([^&\s\"'<>]+)",
    re.IGNORECASE,
)

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


def mask_credential_params(text: str | None) -> str | None:
    """Blank the value of any query parameter whose name implies a credential.

    Value-based redaction can only remove secrets this process knows. This
    removes secrets it does not: it keys off the parameter name, so a token in
    a feed URL nobody registered as a secret is still masked. Masking a
    harmless parameter that happens to be called ``key`` is the acceptable
    direction to be wrong in.
    """
    if not text:
        return text
    return _CREDENTIAL_PARAM_RE.sub(lambda match: f"{match.group(1)}={PLACEHOLDER}", text)


def scrub(text: str | None, secrets: Iterable[str | None]) -> str | None:
    """Both defences: known secrets by value, unknown ones by parameter name."""
    return mask_credential_params(redact(text, secrets))


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
