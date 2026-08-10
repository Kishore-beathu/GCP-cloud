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

    monkeypatch.setattr(diagnostics, "probe_sec", ok)
    monkeypatch.setattr(diagnostics, "probe_finnhub", broken)
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
