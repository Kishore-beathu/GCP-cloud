"""Source probes: every failure mode must produce a usable explanation.

These exist because "the dashboard is empty" was, for a long stretch,
indistinguishable from a rejected key, a plan that excludes an endpoint, and a
symbol nobody covers. Each test pins one of those to a distinct message.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services.diagnostics import (
    probe_alpha_vantage,
    probe_finnhub,
    probe_sec,
    probe_sources,
)


def settings(**overrides) -> SimpleNamespace:
    base = {
        "finnhub_api_key": "test-key",
        "alpha_vantage_api_key": "test-key",
        "sec_user_agent": "Test Agent test@example.com",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def client_returning(response: httpx.Response) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))


def client_handling(handler) -> httpx.AsyncClient:
    """A client whose reply depends on the request, for multi-endpoint probes."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Finnhub ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_finnhub_probe_reports_success():
    articles = [{"headline": "h", "url": "u", "datetime": 1}] * 3
    async with client_returning(httpx.Response(200, json=articles)) as client:
        probe = await probe_finnhub(client, settings())

    assert probe.ok
    assert probe.items == 3
    assert probe.latency_ms is not None


@pytest.mark.asyncio
async def test_finnhub_probe_names_a_rejected_key():
    async with client_returning(httpx.Response(401, text="Invalid API key")) as client:
        probe = await probe_finnhub(client, settings())

    assert not probe.ok
    assert "401" in probe.detail
    assert "Invalid API key" in probe.detail


@pytest.mark.asyncio
async def test_finnhub_probe_distinguishes_a_plan_limit_from_no_news():
    """An empty list for a week of AAPL news is a plan limit, not a quiet week."""
    async with client_returning(httpx.Response(200, json=[])) as client:
        probe = await probe_finnhub(client, settings())

    assert not probe.ok
    assert probe.items == 0
    assert "plan does not include" in probe.detail


@pytest.mark.asyncio
async def test_finnhub_probe_reports_rate_limiting_separately():
    async with client_returning(httpx.Response(429, text="")) as client:
        probe = await probe_finnhub(client, settings())

    assert not probe.ok
    assert "429" in probe.detail


@pytest.mark.asyncio
async def test_finnhub_probe_skips_without_a_key():
    async with client_returning(httpx.Response(200, json=[])) as client:
        probe = await probe_finnhub(client, settings(finnhub_api_key=None))

    assert not probe.configured
    assert "FINNHUB_API_KEY" in probe.detail


# --- Alpha Vantage ----------------------------------------------------------


@pytest.mark.asyncio
async def test_alpha_vantage_probe_reports_success():
    payload = {"Global Quote": {"05. price": "212.44"}}
    async with client_returning(httpx.Response(200, json=payload)) as client:
        probe = await probe_alpha_vantage(client, settings())

    assert probe.ok
    assert "212.44" in probe.detail


@pytest.mark.asyncio
async def test_alpha_vantage_probe_surfaces_the_quota_message():
    payload = {"Information": "our standard API rate limit is 25 requests per day"}
    async with client_returning(httpx.Response(200, json=payload)) as client:
        probe = await probe_alpha_vantage(client, settings())

    assert not probe.ok
    assert "25 requests per day" in probe.detail


@pytest.mark.asyncio
async def test_alpha_vantage_probe_surfaces_an_error_message():
    async with client_returning(
        httpx.Response(200, json={"Error Message": "Invalid API call."})
    ) as client:
        probe = await probe_alpha_vantage(client, settings())

    assert not probe.ok
    assert "Invalid API call." in probe.detail


# --- SEC --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sec_probe_explains_a_user_agent_block():
    async with client_returning(httpx.Response(403, text="")) as client:
        probe = await probe_sec(client, settings())

    assert not probe.ok
    assert "SEC_USER_AGENT" in probe.detail


@pytest.mark.asyncio
async def test_sec_probe_reports_success():
    async with client_returning(httpx.Response(200, json={"0": {}, "1": {}})) as client:
        probe = await probe_sec(client, settings())

    assert probe.ok
    assert probe.items == 2


# --- Aggregate --------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_sources_partitions_healthy_and_failing(monkeypatch):
    """One dead source must not hide the others' results."""
    from app.services import diagnostics

    async def ok(client, settings_obj):
        return diagnostics.Probe("sec_edgar", True, True, "fine")

    async def broken(client, settings_obj):
        return diagnostics.Probe("finnhub_news", True, False, "HTTP 401")

    async def no_endpoints(client, settings_obj):
        return []

    monkeypatch.setattr(diagnostics, "probe_sec", ok)
    monkeypatch.setattr(diagnostics, "probe_finnhub", broken)
    monkeypatch.setattr(diagnostics, "probe_finnhub_endpoints", no_endpoints)
    monkeypatch.setattr(
        diagnostics,
        "probe_alpha_vantage",
        lambda *_: _returns(diagnostics.Probe("alpha_vantage", False, False, "not set")),
    )

    report = await probe_sources(settings())

    assert report["healthy"] == ["sec_edgar"]
    assert set(report["failing"]) == {"finnhub_news", "alpha_vantage"}
    assert len(report["sources"]) == 3


async def _returns(value):
    return value


@pytest.mark.asyncio
async def test_probe_never_echoes_a_key(monkeypatch):
    """The report is meant to be pasteable; a key must not ride along in it."""
    secret = "super-secret-key-value"
    async with client_returning(httpx.Response(401, text="denied")) as client:
        probe = await probe_finnhub(client, settings(finnhub_api_key=secret))

    assert secret not in probe.detail
    assert secret not in str(probe.as_dict())


# --- Credential redaction ---------------------------------------------------
# Alpha Vantage quotes the API key back inside its own rate-limit notice. The
# report is written to be pasted into a chat or an issue, so a vendor echoing
# a key is a disclosure even though our code never interpolates one.

ALPHA_VANTAGE_ECHOES_THE_KEY = (
    "We have detected your API key as {key} and our standard API rate limit is "
    "25 requests per day. Please subscribe to any of the premium plans."
)


@pytest.mark.asyncio
async def test_probe_sources_redacts_a_key_the_vendor_echoed(monkeypatch):
    from app.services import diagnostics

    secret = "XHS833JZWR5LZD0Z"

    async def echoing(client, settings_obj):
        return diagnostics.Probe(
            "alpha_vantage",
            True,
            False,
            ALPHA_VANTAGE_ECHOES_THE_KEY.format(key=secret),
        )

    async def fine(client, settings_obj):
        return diagnostics.Probe("sec_edgar", True, True, "ok")

    monkeypatch.setattr(diagnostics, "probe_alpha_vantage", echoing)
    monkeypatch.setattr(diagnostics, "probe_sec", fine)
    monkeypatch.setattr(diagnostics, "probe_finnhub", fine)

    report = await probe_sources(settings(alpha_vantage_api_key=secret))

    rendered = str(report)
    assert secret not in rendered
    assert "[REDACTED]" in rendered
    # The rest of the message survives: it is what tells you to upgrade.
    assert "25 requests per day" in rendered


@pytest.mark.asyncio
async def test_redaction_covers_every_configured_credential(monkeypatch):
    """A vendor could echo any of them; do not special-case one key."""
    from app.services import diagnostics

    finnhub_key = "d9s6rjpr01qoo7o6tf50"

    async def leaky(client, settings_obj):
        return diagnostics.Probe("finnhub_news", True, False, f"bad token {finnhub_key}")

    async def fine(client, settings_obj):
        return diagnostics.Probe("sec_edgar", True, True, "ok")

    monkeypatch.setattr(diagnostics, "probe_finnhub", leaky)
    monkeypatch.setattr(diagnostics, "probe_sec", fine)
    monkeypatch.setattr(diagnostics, "probe_alpha_vantage", fine)

    report = await probe_sources(settings(finnhub_api_key=finnhub_key))

    assert finnhub_key not in str(report)


# --- Feed diagnostics -------------------------------------------------------
# "No news from the new sources" has three causes that look identical from an
# empty dashboard. These separate them.


@pytest.mark.asyncio
async def test_feed_probe_reports_entries_and_matches():
    from app.services.diagnostics import probe_feed
    from app.services.matching import CompanyIndex

    rss = """<rss version="2.0"><channel>
      <item><title>Pfizer wins approval</title><link>https://e.com/1</link>
        <pubDate>Mon, 10 Aug 2026 09:00:00 GMT</pubDate></item>
      <item><title>An unrelated company raises money</title><link>https://e.com/2</link>
        <pubDate>Mon, 10 Aug 2026 09:00:00 GMT</pubDate></item>
    </channel></rss>"""
    index = CompanyIndex(names={"pfizer": ("PFE",)}, tickers=frozenset({"PFE"}))

    async with client_returning(httpx.Response(200, text=rss)) as client:
        probe = await probe_feed(client, "wire", "https://e.com/rss", index)

    assert probe.ok
    assert probe.entries == 2
    assert probe.matched == 1


@pytest.mark.asyncio
async def test_feed_probe_separates_unreachable_from_unmatched():
    """A 404 and a working feed about other companies are different problems."""
    from app.services.diagnostics import probe_feed
    from app.services.matching import CompanyIndex

    index = CompanyIndex(names={"pfizer": ("PFE",)}, tickers=frozenset({"PFE"}))

    async with client_returning(httpx.Response(404, text="Not found")) as client:
        unreachable = await probe_feed(client, "wire", "https://e.com/rss", index)

    rss = """<rss version="2.0"><channel>
      <item><title>Some other company did something</title><link>https://e.com/3</link>
        <pubDate>Mon, 10 Aug 2026 09:00:00 GMT</pubDate></item>
    </channel></rss>"""
    async with client_returning(httpx.Response(200, text=rss)) as client:
        unmatched = await probe_feed(client, "wire", "https://e.com/rss", index)

    assert not unreachable.ok and unreachable.entries == 0
    assert "404" in unreachable.detail

    assert unmatched.ok and unmatched.entries == 1 and unmatched.matched == 0
    assert "nothing in this window is about your universe" in unmatched.detail


@pytest.mark.asyncio
async def test_feed_probe_distinguishes_an_empty_channel_from_dropped_items():
    """A quiet feed and an unparseable one look identical without this."""
    from app.services.diagnostics import probe_feed
    from app.services.matching import CompanyIndex

    index = CompanyIndex(names={}, tickers=frozenset())

    empty = '<rss version="2.0"><channel><title>Quiet</title></channel></rss>'
    async with client_returning(httpx.Response(200, text=empty)) as client:
        quiet = await probe_feed(client, "wire", "https://e.com/rss", index)

    # Items present, but every date is in a format nothing recognises.
    unreadable = """<rss version="2.0"><channel>
      <item><title>A</title><link>https://e.com/a</link><pubDate>10/08/2026 09:00</pubDate></item>
    </channel></rss>"""
    async with client_returning(httpx.Response(200, text=unreadable)) as client:
        dropped = await probe_feed(client, "wire", "https://e.com/rss", index)

    assert "contains no items right now" in quiet.detail
    assert "dropped for unreadable date" in dropped.detail
    assert dropped.entries == 1  # seen, then discarded


@pytest.mark.asyncio
async def test_feed_probe_flags_a_changed_feed_shape():
    """Reachable but unparseable is a third distinct failure."""
    from app.services.diagnostics import probe_feed
    from app.services.matching import CompanyIndex

    index = CompanyIndex(names={}, tickers=frozenset())
    async with client_returning(httpx.Response(200, text="<html>not a feed</html>")) as client:
        probe = await probe_feed(client, "wire", "https://e.com/rss", index)

    assert not probe.ok
    # Naming what came back separates an HTML error page from a moved element.
    assert "root element is <html>" in probe.detail
    assert "not a feed" in probe.detail


@pytest.mark.asyncio
async def test_each_finnhub_endpoint_is_probed_separately():
    """One key does not mean one level of access.

    Finnhub answers an endpoint the plan excludes with 401 and the words
    "Invalid API key" — the same response as a genuinely bad credential. A
    fundamentals ingest failing while news ingest succeeds is unreadable from
    either message alone, so each path is asked on its own.
    """
    from app.config import Settings
    from app.services.diagnostics import probe_finnhub_endpoints

    def handler(request: httpx.Request) -> httpx.Response:
        if "company-news" in str(request.url):
            return httpx.Response(200, json=[{"headline": "a"}, {"headline": "b"}])
        return httpx.Response(401, json={"error": "Invalid API key"})

    settings = Settings(finnhub_api_key="test-key")
    async with client_handling(handler) as client:
        probes = await probe_finnhub_endpoints(client, settings)

    by_name = {probe.source: probe for probe in probes}
    assert by_name["finnhub:company-news"].ok is True
    assert by_name["finnhub:profile2"].ok is False
    # The message has to name the ambiguity rather than asserting one cause.
    assert "plan" in by_name["finnhub:profile2"].detail


@pytest.mark.asyncio
async def test_a_probe_never_echoes_the_key_back():
    """Vendors quote the key at you; this report exists to be pasted."""
    from app.config import Settings
    from app.services.diagnostics import probe_finnhub_endpoints

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error":"Invalid API key sk-secret-value-123"}')

    settings = Settings(finnhub_api_key="sk-secret-value-123")
    async with client_handling(handler) as client:
        probes = await probe_finnhub_endpoints(client, settings)

    assert all("sk-secret-value-123" not in probe.detail for probe in probes)
