"""Open-market insider transactions from Form 4.

The news firehose filters Form 4 out and is right to: one officer selling on a
schedule is noise, and the feed carries thousands a day. Aggregated they are a
different thing — a cluster of officers buying their own stock is information
no news feed carries, because it is not news.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.integrations.insider import (
    OPEN_MARKET_CODES,
    parse_form4,
    raw_xml_document,
)
from app.models import InsiderTransaction, Stock
from app.services import fundamentals


def _form4(code: str, shares: str, price: str, disposed: str = "A") -> str:
    return f"""<?xml version="1.0"?>
    <ownershipDocument>
      <reportingOwner>
        <reportingOwnerId><rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship>
          <isOfficer>1</isOfficer>
          <officerTitle>Chief Executive Officer</officerTitle>
        </reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <transactionDate><value>2026-08-20</value></transactionDate>
          <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>{shares}</value></transactionShares>
            <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>{disposed}</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>"""


# --- Parsing ------------------------------------------------------------------


def test_an_open_market_purchase_is_parsed_with_its_value():
    rows = parse_form4(_form4("P", "1000", "45.50"))

    assert len(rows) == 1
    row = rows[0]
    assert row["open_market"] is True
    assert row["code"] == "P"
    assert row["shares"] == 1000
    assert row["value"] == pytest.approx(45500.0)
    assert row["insider_name"] == "Jane Doe"
    assert row["insider_title"] == "Chief Executive Officer"
    assert row["traded_on"] == date(2026, 8, 20)


def test_a_disposal_is_signed_negative():
    """Summing a symbol's rows has to give net conviction on its own.

    Storing the sign at parse time means nothing downstream needs to re-read
    the acquired/disposed flag to work out which way a trade went.
    """
    rows = parse_form4(_form4("S", "1000", "45.50", disposed="D"))

    assert rows[0]["value"] == pytest.approx(-45500.0)


@pytest.mark.parametrize("code", ["A", "M", "F", "G"])
def test_compensation_events_are_not_open_market(code):
    """A grant, an exercise, tax withholding and a gift are not opinions.

    They outnumber deliberate trades by a wide margin, so counting them would
    bury the signal in the mechanics of paying executives.
    """
    rows = parse_form4(_form4(code, "5000", "0"))

    assert rows[0]["open_market"] is False
    assert code not in OPEN_MARKET_CODES


def test_a_value_wrapped_field_is_read_from_its_inner_element():
    """Every field in this schema is nested one level deeper than it looks.

    Reading the outer element returns whitespace, which parses to zero — a
    trade of no shares at no price, stored as though it were real.
    """
    rows = parse_form4(_form4("P", "250", "12.00"))

    assert rows[0]["shares"] == 250
    assert rows[0]["price_per_share"] == 12.0


def test_malformed_xml_yields_nothing_rather_than_raising():
    assert parse_form4("<not-xml") == []
    assert parse_form4("<ownershipDocument></ownershipDocument>") == []


def test_the_rendered_document_path_is_converted_to_the_source():
    """primaryDocument points at an XSL view, which parses into nothing useful."""
    assert raw_xml_document("xslF345X05/wk-form4_123.xml") == "wk-form4_123.xml"
    assert raw_xml_document("wk-form4_123.xml") == "wk-form4_123.xml"


# --- The factor ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_buyers_are_counted_by_person_not_by_filing(db):
    """An officer who files three times in a week is one opinion."""
    stock = Stock(ticker="MU", company_name="Micron", sector="memory")
    db.add(stock)
    await db.commit()
    await db.refresh(stock)

    today = datetime.now(timezone.utc).date()
    db.add_all(
        [
            InsiderTransaction(
                ticker_id=stock.id, accession=f"acc{index}", sequence=0,
                traded_on=today - timedelta(days=index), transaction_code="P",
                insider_name="Jane Doe", shares=100, price_per_share=10, value=1000.0,
            )
            for index in range(3)
        ]
        + [
            InsiderTransaction(
                ticker_id=stock.id, accession="accX", sequence=0,
                traded_on=today, transaction_code="P",
                insider_name="John Roe", shares=100, price_per_share=10, value=1000.0,
            )
        ]
    )
    await db.commit()

    factors = await fundamentals.load_all(db)

    assert factors[stock.id].insider_buyers_90d == 2
    assert factors[stock.id].insider_net_value_90d == pytest.approx(4000.0)


@pytest.mark.asyncio
async def test_trades_outside_the_window_do_not_count(db):
    stock = Stock(ticker="MU", company_name="Micron", sector="memory")
    db.add(stock)
    await db.commit()
    await db.refresh(stock)

    old = datetime.now(timezone.utc).date() - timedelta(
        days=fundamentals.INSIDER_WINDOW_DAYS + 10
    )
    db.add(
        InsiderTransaction(
            ticker_id=stock.id, accession="old", sequence=0, traded_on=old,
            transaction_code="P", insider_name="Jane Doe", value=99999.0,
        )
    )
    await db.commit()

    factors = await fundamentals.load_all(db)

    assert factors[stock.id].insider_buyers_90d == 0
    assert factors[stock.id].insider_net_value_90d is None


def test_no_trades_is_not_the_same_as_no_net_buying():
    """"Nobody traded" and "trades netted to nothing" are different facts.

    Ranked together, every symbol with no Form 4 coverage would sit mid-
    distribution on no information — the mistake the analyst-opinion factor
    was written to avoid.
    """
    from app.services.scoring import _insider_values

    quiet = fundamentals.Fundamentals()
    balanced = fundamentals.Fundamentals(
        insider_net_value_90d=0.0, insider_buyers_90d=1, insider_sellers_90d=1
    )

    assert _insider_values(quiet)["insider_net_value_90d"] is None
    assert _insider_values(balanced)["insider_net_value_90d"] == 0.0


def test_insider_factors_carry_no_pillar_weight():
    from app.services import scoring

    assert set(scoring.INSIDER_WEIGHTS)
    assert all(weight == 0.0 for _, weight, _ in scoring.INSIDER_WEIGHTS.values())


@pytest.mark.asyncio
async def test_the_ingest_says_so_when_no_symbol_has_a_cik(db):
    """Form 4 is a US filing; a universe without CIKs cannot be asked for one."""
    from app.integrations.insider import ingest_insider_transactions

    db.add(Stock(ticker="4502.T", company_name="Takeda", sector="pharma"))
    await db.commit()

    report = await ingest_insider_transactions(db)

    assert report.symbols == 0
    assert "CIK" in report.note
