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
