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


# --- Feed sources ------------------------------------------------------------
# "No news from the new sources" has three very different causes, and the
# counts below separate them: the feed returned nothing (URL wrong, or host
# blocking us), it returned items but none named a tracked company (matching or
# universe problem), or it worked and everything was already stored.


@dataclass
class FeedProbe:
    """The result of reading one feed without storing anything."""

    source: str
    url: str
    ok: bool
    entries: int
    matched: int
    detail: str
    latency_ms: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


async def probe_feed(
    client: httpx.AsyncClient,
    source: str,
    url: str,
    index,
    *,
    user_agent: str | None = None,
    params: dict | None = None,
) -> FeedProbe:
    """Fetch one feed and report entries seen and entries naming a tracked name."""
    from app.services.feeds import DEFAULT_USER_AGENT, parse_feed_with_report
    from app.services.matching import match_tickers

    started = time.perf_counter()
    try:
        response = await client.get(
            url,
            params=params,
            headers={"User-Agent": user_agent or DEFAULT_USER_AGENT},
            timeout=20.0,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        # An httpx error can stringify to empty, leaving "Network error: " and
        # no way to tell a DNS failure from a TLS one.
        return FeedProbe(
            source, url, False, 0, 0, f"{type(exc).__name__}: {exc or 'no detail'}"
        )

    elapsed = int((time.perf_counter() - started) * 1000)

    if response.status_code != 200:
        return FeedProbe(
            source,
            url,
            False,
            0,
            0,
            f"HTTP {response.status_code}. {response.text[:160]}",
            elapsed,
        )

    entries, parse_report = parse_feed_with_report(response.text)
    if not entries:
        # Say what actually came back, and why nothing survived. "No entries"
        # is the same message for an empty channel, an HTML error page, and a
        # feed whose dates stopped being readable — and those need opposite
        # responses: wait, fix the URL, fix the parser.
        content_type = response.headers.get("content-type", "unknown")
        preview = " ".join(response.text[:160].split())
        return FeedProbe(
            source,
            url,
            False,
            parse_report.items_seen,
            0,
            f"{parse_report.summary()} Content-Type: {content_type}. "
            f"Body starts: {preview}",
            elapsed,
        )

    matched = sum(1 for entry in entries if match_tickers(entry.title, index, limit=1))
    detail = f"{len(entries)} entries, {matched} naming a tracked company."
    if matched == 0:
        detail += (
            " The feed works; nothing in this window is about your universe, which"
            " is normal for a narrow watchlist over a short window."
        )
    return FeedProbe(source, url, True, len(entries), matched, detail, elapsed)


async def probe_news_sources(settings: Settings, db) -> dict:
    """Read every feed-based source once and report what came back."""
    from app.integrations import clinical, fda, halts, newswire
    from app.integrations.edgar_firehose import FEED_URL as EDGAR_URL
    from app.services.matching import build_index

    index = await build_index(db)
    probes: list[FeedProbe] = []

    async with httpx.AsyncClient() as client:
        probes.append(
            await probe_feed(
                client,
                "sec_edgar_firehose",
                EDGAR_URL,
                index,
                user_agent=settings.sec_user_agent,
                params={
                    "action": "getcurrent",
                    "type": "8-K",
                    "company": "",
                    "dateb": "",
                    "owner": "include",
                    "count": "100",
                    "output": "atom",
                },
            )
        )
        probes.append(await probe_feed(client, "fda_press", settings.fda_press_feed, index))
        probes.append(await probe_feed(client, "halts", settings.halts_feed, index))
        probes.append(await probe_feed(client, "ema", settings.ema_feed, index))
        wires = newswire.WIRES
        if settings.newswire_feeds:
            wires = tuple(
                newswire.Wire(key=f"custom_{i}", name=url, url=url)
                for i, url in enumerate(settings.newswire_feeds)
            )
        for wire in wires:
            probes.append(await probe_feed(client, f"newswire:{wire.key}", wire.url, index))

    # The JSON sources were never probed, so "FDA is down" could mean only
    # that its press feed URL moved while the structured data was fine.
    json_probes = await _probe_json_sources(settings)

    return {
        "tracked_companies": len(index.names),
        "reachable": [p.source for p in probes if p.ok] + [
            p["source"] for p in json_probes if p["ok"]
        ],
        "unreachable": [p.source for p in probes if not p.ok] + [
            p["source"] for p in json_probes if not p["ok"]
        ],
        "feeds": [p.as_dict() for p in probes],
        "apis": json_probes,
    }


async def _probe_json_sources(settings: Settings) -> list[dict]:
    """Probe the two sources that speak JSON rather than XML."""
    from datetime import date as date_type

    results: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # openFDA drug enforcement — the structured half of the FDA source.
        try:
            since = date_type.today() - timedelta(days=7)
            response = await client.get(
                "https://api.fda.gov/drug/enforcement.json",
                params={
                    "search": f"report_date:[{since:%Y%m%d}+TO+{date_type.today():%Y%m%d}]",
                    "limit": 1,
                },
                timeout=20.0,
            )
            if response.status_code == 404:
                results.append(
                    {
                        "source": "openfda",
                        "ok": True,
                        "detail": "Reachable. No enforcement reports in the last 7 days.",
                    }
                )
            elif response.status_code == 200:
                total = (response.json().get("meta") or {}).get("results", {}).get("total")
                results.append(
                    {
                        "source": "openfda",
                        "ok": True,
                        "detail": f"Reachable. {total} enforcement reports in the last 7 days.",
                    }
                )
            else:
                results.append(
                    {
                        "source": "openfda",
                        "ok": False,
                        "detail": f"HTTP {response.status_code}: {response.text[:160]}",
                    }
                )
        except (httpx.HTTPError, ValueError) as exc:
            results.append(
                {"source": "openfda", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            )

        # ClinicalTrials.gov v2.
        try:
            response = await client.get(
                "https://clinicaltrials.gov/api/v2/studies",
                params={"pageSize": 1, "format": "json"},
                headers={"Accept": "application/json"},
                timeout=20.0,
            )
            if response.status_code == 200:
                count = len(response.json().get("studies") or [])
                results.append(
                    {
                        "source": "clinicaltrials",
                        "ok": True,
                        "detail": f"Reachable. Returned {count} study in a probe query.",
                    }
                )
            else:
                results.append(
                    {
                        "source": "clinicaltrials",
                        "ok": False,
                        "detail": f"HTTP {response.status_code}: {response.text[:160]}",
                    }
                )
        except (httpx.HTTPError, ValueError) as exc:
            results.append(
                {
                    "source": "clinicaltrials",
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )

    return results
