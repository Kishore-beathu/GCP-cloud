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


# --- Group-targeted ingestion -----------------------------------------------
# "Get me news for the data storage stocks" should be one call, not a
# hand-typed symbol list that goes stale the moment the universe grows.


@pytest.mark.asyncio
async def test_tickers_in_group_returns_only_that_group(db, seeded_stocks):
    from app.models import Stock
    from app.services.tickers import tickers_in_group

    db.add_all(
        [
            Stock(ticker="WDC", company_name="Western Digital", sector="storage_hardware"),
            Stock(ticker="SNOW", company_name="Snowflake", sector="data_platform"),
            Stock(ticker="NVDA", company_name="NVIDIA", sector="ai_semiconductor"),
        ]
    )
    await db.commit()

    storage = await tickers_in_group(db, "data_storage")

    assert storage == ["SNOW", "WDC"]  # sorted, and no AI or pharma names


@pytest.mark.asyncio
async def test_tickers_in_group_ignores_inactive_symbols(db, seeded_stocks):
    from app.models import Stock
    from app.services.tickers import tickers_in_group

    db.add(
        Stock(
            ticker="QTM",
            company_name="Quantum Corp",
            sector="storage_hardware",
            is_active=False,
        )
    )
    await db.commit()

    assert await tickers_in_group(db, "data_storage") == []


@pytest.mark.asyncio
async def test_tickers_in_group_rejects_an_unknown_group(db, seeded_stocks):
    from app.services.tickers import tickers_in_group

    with pytest.raises(LookupError):
        await tickers_in_group(db, "data_storge")


@pytest.mark.asyncio
async def test_ingest_can_be_aimed_at_a_group(client, db, seeded_stocks):
    from app.models import Stock

    db.add(Stock(ticker="WDC", company_name="Western Digital", sector="storage_hardware"))
    await db.commit()

    response = await client.post("/admin/ingest/finnhub?group=data_storage")

    assert response.status_code == 202
    # One storage symbol, not the whole universe.
    assert response.json()["symbols"] == 1


@pytest.mark.asyncio
async def test_ingest_with_a_misspelled_group_is_rejected(client, seeded_stocks):
    """Silently running the full universe would look like the filter worked."""
    response = await client.post("/admin/ingest/finnhub?group=data_storge")

    assert response.status_code == 422
    assert "Unknown group" in response.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_without_a_group_still_means_everything(client, seeded_stocks):
    response = await client.post("/admin/ingest/finnhub")

    assert response.status_code == 202
    assert response.json()["symbols"] == "all active"


def test_no_ticker_is_seeded_twice():
    """A duplicate symbol would be inserted once and silently shadow the other."""
    seen: set[str] = set()
    duplicates = {seed.ticker for seed in SEED_TICKERS if seed.ticker in seen or seen.add(seed.ticker)}

    assert duplicates == set()


def test_the_cro_cohort_spans_the_regions_the_industry_actually_operates_in():
    """Most contract research capacity is in Asia, not the US.

    A CRO cohort of four US names would rank "the best CRO" against a sample
    that excludes most of the industry.
    """
    cros = [seed for seed in SEED_TICKERS if seed.sector == "cro"]
    suffixes = {seed.ticker.rpartition(".")[2] if "." in seed.ticker else "US" for seed in cros}

    assert len(cros) >= 15
    assert {"US", "T", "HK", "SZ", "SS", "NS"} <= suffixes
    assert all(sectors.group_for(seed.sector) == "pharma_life_sciences" for seed in cros)


def test_dual_listed_cros_share_one_company_name():
    """The matcher indexes symbols by name, so both lines must agree on it.

    A mismatched name would leave the H-share invisible to news matching while
    the A-share collected everything, which reads as "no news for this symbol".
    """
    by_name: dict[str, set[str]] = {}
    for seed in SEED_TICKERS:
        if seed.sector == "cro":
            by_name.setdefault(seed.company_name, set()).add(seed.ticker)

    tigermed = by_name["Hangzhou Tigermed Consulting Co."]
    assert tigermed == {"300347.SZ", "3347.HK"}
    assert by_name["WuXi AppTec Co."] == {"603259.SS", "2359.HK"}


# --- Clinical-stage cohort ---------------------------------------------------
# Pre-revenue developers were previously scattered through "biotech" alongside
# companies with marketed products. The two trade on entirely different things,
# and the ranked score compares a symbol against its cohort, so mixing them
# meant ranking a company with no revenue against one guiding on it.


def _clinical_stage() -> list:
    return [seed for seed in SEED_TICKERS if seed.sector == "clinical_stage"]


def test_the_clinical_stage_cohort_is_large_enough_to_rank():
    """Cross-sectional ranking needs a cohort, not a handful.

    Percentile rank against four names produces four percentiles; the factor
    carries almost no information and the score built on it inherits that.
    """
    cohort = _clinical_stage()

    assert len(cohort) >= 40
    assert all(sectors.group_for(seed.sector) == "pharma_life_sciences" for seed in cohort)


def test_every_clinical_stage_name_has_a_us_line():
    """The account these are for carries US-listed stocks only.

    A company can be worth watching and still be unreachable: a TSX-only
    listing shows a price, a score and a setup, and none of it can be acted
    on. Canadian issuers are here under their Nasdaq symbol for that reason,
    with the home line kept separately for its domestic session and news.
    """
    us_lines = {seed.ticker for seed in _clinical_stage() if "." not in seed.ticker}

    for foreign in (seed for seed in _clinical_stage() if "." in seed.ticker):
        base = foreign.ticker.partition(".")[0]
        assert base in us_lines, f"{foreign.ticker} has no US line to trade"


def test_names_that_stopped_trading_are_not_reintroduced():
    """The failure mode this cohort actually has, encoded so an edit trips it.

    Clinical-stage companies leave by acquisition constantly — four of them
    did during the pass that built this list. Every one still reads as a
    plausible ticker afterwards, and a seeded symbol that no longer trades
    produces an empty chart rather than an error, so nothing surfaces it. Two
    dead tickers reached this list before (a CRO private since 2024, a CRO in
    Chapter 11) and both were found by hand.
    """
    acquired = {
        "ACLX",  # Arcellx -> Gilead, closed April 2026
        "TERN",  # Terns Pharmaceuticals -> Merck, closed May 2026
        "MRUS",  # Merus -> Genmab, closed December 2025
        "APGE",  # Apogee Therapeutics -> AbbVie, agreed June 2026
        "VERV",  # Verve Therapeutics -> Eli Lilly
        "MTSR",  # Metsera -> Pfizer
        "APLT",  # Applied Therapeutics, taken private February 2026
        "NOTV",  # Inotiv, Chapter 11
        "2309.T",  # CMIC Holdings, delisted 2024
        "CFLT",  # Confluent -> IBM
        "PSTG",  # Pure Storage, renamed and moved to P
    }

    seeded = {seed.ticker for seed in SEED_TICKERS}

    assert not (seeded & acquired)


def test_a_company_with_an_approved_product_is_not_clinical_stage():
    """The boundary moves in one direction, and only ever quietly.

    An approval turns a binary-event stock into one that trades on
    prescriptions and guidance. Nothing in the pipeline notices, so the name
    keeps being ranked against pre-revenue peers on factors it no longer has.
    """
    cohort = {seed.ticker for seed in _clinical_stage()}
    now_commercial = {
        "NUVB",  # Nuvation Bio - taletrectinib approved June 2025
        "CRNX",  # Crinetics - paltusotine approved 2025
        "MDGL",  # Madrigal - Rezdiffra
        "KRYS",  # Krystal Biotech - Vyjuvek
        "IOVA",  # Iovance - Amtagvi
        "AUPH",  # Aurinia - Lupkynis
    }

    assert not (cohort & now_commercial)
