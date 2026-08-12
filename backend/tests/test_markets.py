"""Market resolution from vendor symbols, and the multi-region search filters."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import markets

# --- Symbol -> market resolution ---------------------------------------------


@pytest.mark.parametrize(
    "ticker,region,mic,currency",
    [
        # North America
        ("PFE", "north_america", "XNYS", "USD"),
        ("SHOP.TO", "north_america", "XTSE", "CAD"),
        ("CGX.V", "north_america", "XTSX", "CAD"),
        ("WALMEX.MX", "north_america", "XMEX", "MXN"),
        # Europe
        ("AZN.L", "europe", "XLON", "GBp"),
        ("SAN.PA", "europe", "XPAR", "EUR"),
        ("BAYN.DE", "europe", "XETR", "EUR"),
        ("ROG.SW", "europe", "XSWX", "CHF"),
        ("NOVO-B.CO", "europe", "XCSE", "DKK"),
        ("SOBI.ST", "europe", "XSTO", "SEK"),
        ("EQNR.OL", "europe", "XOSL", "NOK"),
        ("GRF.MC", "europe", "XMAD", "EUR"),
        # Asia-Pacific
        ("7203.T", "asia_pacific", "XTKS", "JPY"),
        ("0700.HK", "asia_pacific", "XHKG", "HKD"),
        ("600276.SS", "asia_pacific", "XSHG", "CNY"),
        ("207940.KS", "asia_pacific", "XKRX", "KRW"),
        ("SUNPHARMA.NS", "asia_pacific", "XNSE", "INR"),
        ("CSL.AX", "asia_pacific", "XASX", "AUD"),
        ("D05.SI", "asia_pacific", "XSES", "SGD"),
    ],
)
def test_resolve_symbol_suffixes(ticker, region, mic, currency):
    market = markets.resolve(ticker)
    assert (market.region, market.mic, market.currency) == (region, mic, currency)


def test_unsuffixed_symbols_default_to_the_us():
    assert markets.resolve("MRNA").mic == "XNYS"
    assert markets.resolve("brk.a".upper().replace(".A", "")).region == "north_america"


def test_resolution_is_case_insensitive():
    assert markets.resolve("azn.l").mic == markets.resolve("AZN.L").mic


def test_longer_suffixes_win_over_shorter_ones():
    """'.TW' must not shadow a longer suffix ending in the same letters."""
    assert markets.resolve("2330.TW").mic == "XTAI"
    assert markets.resolve("CBA.AX").mic == "XASX"


# --- London's pence quotes ---------------------------------------------------


def test_london_quotes_in_minor_units():
    london = markets.resolve("AZN.L")
    assert london.quotes_in_minor_units is True
    # 10,500 pence is £105, not £10,500.
    assert markets.normalise_price(10_500.0, london) == 105.0


def test_other_markets_are_not_rescaled():
    for ticker in ("PFE", "SAN.PA", "7203.T"):
        market = markets.resolve(ticker)
        assert market.quotes_in_minor_units is False
        assert markets.normalise_price(100.0, market) == 100.0


# --- Sessions ----------------------------------------------------------------


def test_sessions_differ_by_region():
    """At 02:00 UTC Tokyo trades and New York does not."""
    moment = datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)  # a Wednesday
    assert markets.resolve("7203.T").is_open(moment) is True
    assert markets.resolve("PFE").is_open(moment) is False

    # At 15:00 UTC New York is open and Tokyo has closed.
    afternoon = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)
    assert markets.resolve("PFE").is_open(afternoon) is True
    assert markets.resolve("7203.T").is_open(afternoon) is False


def test_weekends_are_closed_everywhere():
    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    assert not markets.open_markets(saturday)


def test_every_market_has_complete_metadata():
    for market in markets.MARKETS:
        assert market.region in markets.REGIONS
        assert len(market.country) == 2
        assert 3 <= len(market.currency) <= 3
        assert market.opens < market.closes


def test_mics_are_unique():
    mics = [market.mic for market in markets.MARKETS]
    assert len(mics) == len(set(mics))


# --- Session state -----------------------------------------------------------


def test_the_us_session_renders_as_the_window_a_european_reader_sees():
    """"15:30-22:00" is only true in central Europe, and only in summer.

    Derived from the exchange calendar rather than a fixed offset, so it moves
    with daylight saving on both sides instead of being an hour wrong for the
    several weeks a year when the two zones switch on different dates.
    """
    from datetime import datetime, timezone

    us = markets.resolve("MU")

    summer = markets.session_state(
        us, tz="Europe/Amsterdam", moment=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    )
    winter = markets.session_state(
        us, tz="Europe/Amsterdam", moment=datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc)
    )

    assert summer["session_in_tz"] == "15:30-22:00"
    assert winter["session_in_tz"] == "15:30-22:00"
    assert summer["session_local"].startswith("09:30-16:00")


def test_next_open_skips_the_weekend():
    """Friday evening's "next open" is Monday, not Saturday."""
    from datetime import datetime, timezone

    us = markets.resolve("MU")
    friday_evening = datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc)

    opens = markets.next_open(us, friday_evening)

    assert opens.weekday() == 0  # Monday


def test_session_state_counts_down_to_the_close_while_open():
    from datetime import datetime, timezone

    us = markets.resolve("MU")
    midday_ny = datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc)  # 12:00 ET

    state = markets.session_state(us, moment=midday_ny)

    assert state["is_open"] is True
    # Four hours to the 16:00 close.
    assert state["minutes_until_change"] == 240
