"""Seeding has to reach databases that already exist, not only fresh ones.

Inserting-only was enough while the universe grew purely by addition. Once a
symbol is *reclassified*, insert-only means the change reaches a fresh clone
and never reaches a database that already has the row — so the same code
produces two different sector maps depending on when the database was first
seeded, and which group a symbol is ranked in depends on that history.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Stock
from app.services.tickers import TickerSeed, seed_stocks


@pytest.fixture
def one_seed(monkeypatch):
    """A single-symbol universe, so the assertions are about behaviour."""

    def _install(*seeds: TickerSeed):
        monkeypatch.setattr("app.services.tickers.load_ticker_seeds", lambda: list(seeds))

    return _install


@pytest.mark.asyncio
async def test_a_missing_symbol_is_inserted(db, one_seed):
    one_seed(TickerSeed("IQV", "IQVIA Holdings Inc.", "cro", "NYSE"))

    report = await seed_stocks(db)

    assert report == {"added": 1, "reclassified": 0, "retired": 0, "restored": 0}
    stock = (await db.execute(select(Stock).where(Stock.ticker == "IQV"))).scalar_one()
    assert stock.sector == "cro"


@pytest.mark.asyncio
async def test_an_existing_symbol_is_reclassified_in_place(db, one_seed):
    """The Charles River case: seeded as cdmo, corrected to cro."""
    one_seed(TickerSeed("CRL", "Charles River Laboratories", "cdmo", "NYSE"))
    await seed_stocks(db)

    one_seed(TickerSeed("CRL", "Charles River Laboratories", "cro", "NYSE"))
    report = await seed_stocks(db)

    assert report == {"added": 0, "reclassified": 1, "retired": 0, "restored": 0}
    stock = (await db.execute(select(Stock).where(Stock.ticker == "CRL"))).scalar_one()
    assert stock.sector == "cro"


@pytest.mark.asyncio
async def test_reseeding_an_unchanged_universe_changes_nothing(db, one_seed):
    """Startup runs this every time; it must be quiet when there is no news."""
    one_seed(TickerSeed("MEDP", "Medpace Holdings", "cro", "NASDAQ"))
    await seed_stocks(db)

    assert await seed_stocks(db) == {
        "added": 0,
        "reclassified": 0,
        "retired": 0,
        "restored": 0,
    }


@pytest.mark.asyncio
async def test_a_locally_renamed_company_is_left_alone(db, one_seed):
    """Only the sector is reconciled.

    Company name, exchange and currency are corrected from live vendor data
    elsewhere; rewriting them from the seed list on every startup would undo
    that work once per restart.
    """
    one_seed(TickerSeed("ICLR", "ICON plc", "cro", "NASDAQ"))
    await seed_stocks(db)

    stock = (await db.execute(select(Stock).where(Stock.ticker == "ICLR"))).scalar_one()
    stock.company_name = "ICON Public Limited Company"
    await db.commit()

    await seed_stocks(db)

    stock = (await db.execute(select(Stock).where(Stock.ticker == "ICLR"))).scalar_one()
    assert stock.company_name == "ICON Public Limited Company"


@pytest.mark.asyncio
async def test_dual_listings_are_seeded_as_two_independent_rows(db, one_seed):
    """The A-share and the H-share are separate instruments, not one symbol."""
    one_seed(
        TickerSeed("300347.SZ", "Hangzhou Tigermed Consulting Co.", "cro"),
        TickerSeed("3347.HK", "Hangzhou Tigermed Consulting Co.", "cro"),
    )

    report = await seed_stocks(db)

    assert report["added"] == 2
    rows = (await db.execute(select(Stock))).scalars().all()
    assert {row.currency for row in rows} == {"CNY", "HKD"}


@pytest.mark.asyncio
async def test_a_symbol_that_leaves_the_seed_list_is_retired(db, one_seed):
    """Tickers get renamed; the old symbol must not linger in the watchlist.

    Pure Storage became Everpure and moved PSTG to P. Without this the dead
    symbol stayed forever, priceless and unexplained, with its replacement
    added beside it.
    """
    one_seed(TickerSeed("PSTG", "Pure Storage Inc.", "storage_hardware", "NYSE"))
    await seed_stocks(db)

    one_seed(TickerSeed("P", "Everpure Inc.", "storage_hardware", "NYSE"))
    report = await seed_stocks(db)

    assert report["added"] == 1
    assert report["retired"] == 1
    retired = (await db.execute(select(Stock).where(Stock.ticker == "PSTG"))).scalar_one()
    # Deactivated, not deleted: its stored price history is still true.
    assert retired.is_active is False


@pytest.mark.asyncio
async def test_a_returning_symbol_is_restored_rather_than_duplicated(db, one_seed):
    """Retirement has to reverse, or a delisting becomes permanent by accident."""
    seed = TickerSeed("QMCO", "Quantum Corporation", "storage_hardware", "NASDAQ")
    one_seed(seed)
    await seed_stocks(db)

    one_seed()  # symbol drops out of the universe
    await seed_stocks(db)

    one_seed(seed)  # and comes back
    report = await seed_stocks(db)

    assert report == {"added": 0, "reclassified": 0, "retired": 0, "restored": 1}
    assert (await db.execute(select(func.count(Stock.id)))).scalar_one() == 1
