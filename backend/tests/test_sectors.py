"""Industry grouping: the taxonomy, and the filters built on it."""

from __future__ import annotations

import pytest

from app.services import sectors
from app.services.tickers import SEED_TICKERS


def test_every_seeded_sector_belongs_to_a_group():
    """An unmapped sector silently lands in "Other" and looks like a bug."""
    unmapped = {
        seed.sector for seed in SEED_TICKERS if sectors.group_for(seed.sector) == "other"
    }

    assert unmapped == set()


def test_no_sector_is_claimed_by_two_groups():
    seen: set[str] = set()
    for group in sectors.GROUPS:
        assert not (seen & set(group.sectors)), f"{group.key} overlaps an earlier group"
        seen |= set(group.sectors)


def test_group_for_is_case_and_whitespace_insensitive():
    assert sectors.group_for("  Pharma ") == "pharma_life_sciences"
    assert sectors.group_for("AI_TECH") == "ai"


def test_group_for_handles_missing_sectors():
    assert sectors.group_for(None) == "other"
    assert sectors.group_for("") == "other"
    assert sectors.group_for("crypto_mining") == "other"


def test_sectors_in_returns_members_and_nothing_for_a_bad_key():
    assert "biotech" in sectors.sectors_in("pharma_life_sciences")
    assert sectors.sectors_in("nonsense") == ()


def test_the_three_requested_groups_all_have_symbols():
    """Pharma, AI and data storage each need real coverage, not just a heading."""
    counts: dict[str, int] = {}
    for seed in SEED_TICKERS:
        key = sectors.group_for(seed.sector)
        counts[key] = counts.get(key, 0) + 1

    assert counts["pharma_life_sciences"] >= 50
    assert counts["ai"] >= 45
    assert counts["data_storage"] >= 30


def test_ai_group_spans_platforms_semiconductors_and_drug_discovery():
    """A group of only chipmakers would miss most of what moves on AI."""
    by_ticker = {seed.ticker: seed.sector for seed in SEED_TICKERS}

    assert by_ticker["MSFT"] == "ai_tech"
    assert by_ticker["NVDA"] == "ai_semiconductor"
    assert by_ticker["RXRX"] == "ai_health"
    assert sectors.group_for("ai_health") == "ai"


def test_data_storage_group_spans_hardware_platforms_and_buildings():
    by_ticker = {seed.ticker: seed.sector for seed in SEED_TICKERS}

    assert by_ticker["WDC"] == "storage_hardware"
    assert by_ticker["MU"] == "memory"
    assert by_ticker["SNOW"] == "data_platform"
    assert by_ticker["EQIX"] == "data_center"


def test_seed_universe_has_no_duplicate_tickers():
    tickers = [seed.ticker for seed in SEED_TICKERS]

    assert len(tickers) == len(set(tickers))


def test_every_suffixed_symbol_resolves_to_a_venue():
    """An unresolved suffix means no region, currency or session for that row."""
    from app.services import markets

    unresolved = [
        seed.ticker
        for seed in SEED_TICKERS
        if "." in seed.ticker and markets.resolve(seed.ticker) is None
    ]

    assert unresolved == []


def test_ai_group_covers_the_whole_supply_chain():
    """Owning only the model companies misses most of what AI demand moves."""
    by_ticker = {seed.ticker: seed.sector for seed in SEED_TICKERS}

    assert by_ticker["AMAT"] == "ai_equipment"      # makes the fabs' machines
    assert by_ticker["ANET"] == "ai_networking"     # connects the racks
    assert by_ticker["TSM"] == "ai_semiconductor"   # fabricates the silicon
    assert by_ticker["MSFT"] == "ai_tech"           # sells the capacity


def test_data_storage_group_covers_boxes_services_and_buildings():
    by_ticker = {seed.ticker: seed.sector for seed in SEED_TICKERS}

    assert by_ticker["STX"] == "storage_hardware"
    assert by_ticker["005930.KS"] == "memory"
    assert by_ticker["DBX"] == "cloud_storage"
    assert by_ticker["DDOG"] == "data_platform"
    assert by_ticker["VRT"] == "data_center"


# --- API --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sectors_endpoint_reports_groups_with_counts(client, seeded_stocks):
    body = (await client.get("/stocks/sectors")).json()

    keys = [group["key"] for group in body["groups"]]
    assert "pharma_life_sciences" in keys
    assert "ai" in keys
    assert "data_storage" in keys

    pharma = next(g for g in body["groups"] if g["key"] == "pharma_life_sciences")
    # The two seeded fixtures are both pharma.
    assert pharma["tracked_symbols"] == 2
    assert "biotech" in pharma["sectors"]


@pytest.mark.asyncio
async def test_stock_list_carries_the_group(client, seeded_stocks):
    rows = (await client.get("/stocks")).json()

    assert {row["sector_group"] for row in rows} == {"pharma_life_sciences"}


@pytest.mark.asyncio
async def test_stock_list_filters_by_group(client, seeded_stocks):
    matching = (await client.get("/stocks?group=pharma_life_sciences")).json()
    other = (await client.get("/stocks?group=data_storage")).json()

    assert len(matching) == 2
    assert other == []


@pytest.mark.asyncio
async def test_unknown_group_is_rejected_rather_than_ignored(client, seeded_stocks):
    """Silently returning everything would read as "the filter did nothing"."""
    response = await client.get("/stocks?group=nonsense")

    assert response.status_code == 422
    assert "/stocks/sectors" in response.json()["detail"]
