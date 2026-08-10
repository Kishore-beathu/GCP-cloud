"""One-shot probes of every upstream data source.

Ingestion runs in the background and, by design, degrades quietly: a source
that fails leaves the others working. That is right for a long-running server
and useless when you are sitting in front of an empty dashboard asking *which*
source is broken. These probes make one cheap call per vendor and report what
came back, including the vendor's own error wording.

Nothing here echoes a key. The probes send credentials upstream, as they must,
but report only status, latency and the vendor's message.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import httpx

from app.config import Settings
from app.services.redaction import redact, secrets_from

# A large, liquid US name that every plan covers, so a failure is about the
# account rather than the symbol.
PROBE_SYMBOL = "AAPL"

# Alpha Vantage's own documentation example, for the same reason.
ALPHA_VANTAGE_PROBE_SYMBOL = "IBM"


@dataclass
class Probe:
    """The result of one source check."""

    source: str
    configured: bool
    ok: bool
    detail: str
    items: int | None = None
    latency_ms: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _skip(source: str, variable: str) -> Probe:
    return Probe(
        source=source,
        configured=False,
        ok=False,
        detail=f"{variable} is not set, so no request was made.",
    )


async def probe_finnhub(client: httpx.AsyncClient, settings: Settings) -> Probe:
    """Ask Finnhub for a few days of AAPL news — the same call ingestion makes."""
    if not settings.finnhub_api_key:
        return _skip("finnhub_news", "FINNHUB_API_KEY")

    to_date = date.today()
    started = time.perf_counter()
    try:
        response = await client.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": PROBE_SYMBOL,
                "from": (to_date - timedelta(days=7)).isoformat(),
                "to": to_date.isoformat(),
                "token": settings.finnhub_api_key,
            },
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return Probe("finnhub_news", True, False, f"Network error: {exc}")

    elapsed = int((time.perf_counter() - started) * 1000)

    if response.status_code in (401, 403):
        return Probe(
            "finnhub_news",
            True,
            False,
            f"HTTP {response.status_code} - the key was rejected. Check it is "
            f"correct and active. Vendor said: {response.text[:200]}",
            latency_ms=elapsed,
        )
    if response.status_code == 429:
        return Probe(
            "finnhub_news",
            True,
            False,
            "HTTP 429 - rate limited. Wait a minute and try again.",
            latency_ms=elapsed,
        )
    if response.status_code != 200:
        return Probe(
            "finnhub_news",
            True,
            False,
            f"HTTP {response.status_code}: {response.text[:200]}",
            latency_ms=elapsed,
        )

    try:
        payload = response.json()
    except ValueError:
        return Probe("finnhub_news", True, False, "Response was not JSON.", latency_ms=elapsed)

    if not isinstance(payload, list):
        return Probe(
            "finnhub_news",
            True,
            False,
            f"Expected a list, got {type(payload).__name__}: {str(payload)[:200]}",
            latency_ms=elapsed,
        )

    if not payload:
        # A working key on a plan that returns nothing for a week of AAPL news
        # is a plan limitation, not a transient gap.
        return Probe(
            "finnhub_news",
            True,
            False,
            f"The key works but returned 0 articles for {PROBE_SYMBOL} over 7 "
            "days, which means your plan does not include company news.",
            items=0,
            latency_ms=elapsed,
        )

    return Probe(
        "finnhub_news",
        True,
        True,
        f"{len(payload)} articles for {PROBE_SYMBOL} in the last 7 days.",
        items=len(payload),
        latency_ms=elapsed,
    )


async def probe_alpha_vantage(client: httpx.AsyncClient, settings: Settings) -> Probe:
    """Ask Alpha Vantage for one quote, surfacing its Note/Information wording."""
    if not settings.alpha_vantage_api_key:
        return _skip("alpha_vantage", "ALPHA_VANTAGE_API_KEY")

    started = time.perf_counter()
    try:
        response = await client.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": ALPHA_VANTAGE_PROBE_SYMBOL,
                "apikey": settings.alpha_vantage_api_key,
            },
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return Probe("alpha_vantage", True, False, f"Network error: {exc}")

    elapsed = int((time.perf_counter() - started) * 1000)
    try:
        payload = response.json()
    except ValueError:
        return Probe("alpha_vantage", True, False, "Response was not JSON.", latency_ms=elapsed)

    # Alpha Vantage answers 200 for every failure and explains itself in the body.
    for key in ("Error Message", "Note", "Information"):
        if payload.get(key):
            return Probe("alpha_vantage", True, False, str(payload[key])[:300], latency_ms=elapsed)

    quote = payload.get("Global Quote") or {}
    if not quote.get("05. price"):
        return Probe(
            "alpha_vantage",
            True,
            False,
            f"No price in the response for {ALPHA_VANTAGE_PROBE_SYMBOL}.",
            latency_ms=elapsed,
        )

    return Probe(
        "alpha_vantage",
        True,
        True,
        f"{ALPHA_VANTAGE_PROBE_SYMBOL} quoted at {quote['05. price']}.",
        items=1,
        latency_ms=elapsed,
    )


async def probe_sec(client: httpx.AsyncClient, settings: Settings) -> Probe:
    """SEC needs no key, but blocks traffic without a descriptive User-Agent."""
    started = time.perf_counter()
    try:
        response = await client.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": settings.sec_user_agent},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return Probe("sec_edgar", True, False, f"Network error: {exc}")

    elapsed = int((time.perf_counter() - started) * 1000)
    if response.status_code == 403:
        return Probe(
            "sec_edgar",
            True,
            False,
            "HTTP 403 - the SEC blocks requests without a descriptive "
            "User-Agent. Set SEC_USER_AGENT to 'Company Name you@example.com'.",
            latency_ms=elapsed,
        )
    if response.status_code != 200:
        return Probe(
            "sec_edgar", True, False, f"HTTP {response.status_code}", latency_ms=elapsed
        )

    try:
        count = len(response.json())
    except ValueError:
        return Probe("sec_edgar", True, False, "Response was not JSON.", latency_ms=elapsed)

    return Probe(
        "sec_edgar", True, True, f"Reachable, {count} companies listed.", count, elapsed
    )


async def probe_sources(settings: Settings) -> dict:
    """Probe every source. Failures are reported, never raised."""
    async with httpx.AsyncClient() as client:
        probes = [
            await probe_sec(client, settings),
            await probe_finnhub(client, settings),
            await probe_alpha_vantage(client, settings),
        ]

    # Vendor messages can contain the key that was sent: Alpha Vantage's
    # rate-limit notice quotes it back verbatim. This report is written to be
    # pasted somewhere, so nothing leaves here without passing through here.
    secrets = secrets_from(settings)
    for probe in probes:
        probe.detail = redact(probe.detail, secrets) or ""

    return {
        "healthy": [probe.source for probe in probes if probe.ok],
        "failing": [probe.source for probe in probes if not probe.ok],
        "sources": [probe.as_dict() for probe in probes],
    }
