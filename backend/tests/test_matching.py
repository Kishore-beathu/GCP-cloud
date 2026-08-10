"""Company-name matching, tuned so a false positive is the expensive error.

A wrongly matched headline attaches sentiment to a company the story is not
about, fires an alert on it, and enters the backtest as evidence. A missed
match only costs one story. Every case here is written from that asymmetry.
"""

from __future__ import annotations

import pytest

from app.models import Stock
from app.services.matching import CompanyIndex, build_index, match_tickers, normalise


def index_of(**pairs: str) -> CompanyIndex:
    """Build an index directly from name -> ticker pairs."""
    grouped: dict[str, list[str]] = {}
    for name, ticker in pairs.items():
        grouped.setdefault(normalise(name), []).append(ticker)
    return CompanyIndex(
        names={key: tuple(sorted(v)) for key, v in grouped.items()},
        tickers=frozenset(pairs.values()),
    )


def test_normalise_strips_corporate_suffixes():
    assert normalise("Pfizer Inc.") == "pfizer"
    assert normalise("GSK plc") == "gsk"
    assert normalise("Novo Nordisk A/S") == "novo nordisk"
    assert normalise("Recursion Pharmaceuticals, Inc.") == "recursion"


def test_matches_a_company_named_without_its_suffix():
    """Headlines write "Pfizer", the database holds "Pfizer Inc.".""" ""
    index = index_of(**{"Pfizer Inc.": "PFE"})

    assert match_tickers("Pfizer reports positive phase 3 results", index) == ["PFE"]


def test_does_not_match_a_name_inside_a_longer_word():
    """Substring matching is how "miss" once fired inside "submission"."""
    index = index_of(**{"Amgen Inc.": "AMGN"})

    assert match_tickers("Amgentech Ltd raises a seed round", index) == []


def test_prefers_the_longer_name_when_two_could_match():
    index = index_of(**{"Bristol-Myers Squibb": "BMY", "Bristol Water": "BWTR"})

    assert match_tickers("Bristol-Myers Squibb wins approval", index)[0] == "BMY"


def test_caps_the_number_of_tickers_per_story():
    """A headline naming five companies is a round-up, not five events."""
    index = index_of(
        **{
            "Pfizer Inc.": "PFE",
            "Merck & Co.": "MRK",
            "Eli Lilly": "LLY",
            "AbbVie Inc.": "ABBV",
        }
    )
    headline = "Pfizer, Merck, Eli Lilly and AbbVie all gained on the session"

    assert len(match_tickers(headline, index, limit=2)) == 2


def test_matches_an_explicitly_quoted_symbol():
    index = index_of(**{"Micron Technology": "MU"})

    assert "MU" in match_tickers("Shares of MU rose after the report", index)


def test_a_symbol_inside_a_word_is_not_a_match():
    index = index_of(**{"Micron Technology": "MU"})

    assert match_tickers("The museum expanded its collection", index) == []


def test_suffixed_symbols_match_with_the_dot_intact():
    index = index_of(**{"AstraZeneca PLC": "AZN.L"})

    assert "AZN.L" in match_tickers("AZN.L closed higher in London", index)


def test_returns_nothing_for_empty_input():
    index = index_of(**{"Pfizer Inc.": "PFE"})

    assert match_tickers("", index) == []
    assert match_tickers("Pfizer", CompanyIndex(names={}, tickers=frozenset())) == []


@pytest.mark.asyncio
async def test_build_index_skips_names_that_are_ordinary_words(db):
    """Box, Arm and Now would otherwise match constantly in ordinary prose."""
    db.add_all(
        [
            Stock(ticker="BOX", company_name="Box Inc.", sector="cloud_storage"),
            Stock(ticker="ARM", company_name="Arm Holdings", sector="ai_semiconductor"),
            Stock(ticker="PFE", company_name="Pfizer Inc.", sector="pharma"),
        ]
    )
    await db.commit()

    index = await build_index(db)

    assert "pfizer" in index.names
    assert "box" not in index.names
    assert "arm" not in index.names
    # They stay reachable by symbol, which is unambiguous.
    assert {"BOX", "ARM"} <= index.tickers


@pytest.mark.asyncio
async def test_generic_names_do_not_capture_unrelated_headlines(db):
    db.add(Stock(ticker="BOX", company_name="Box Inc.", sector="cloud_storage"))
    await db.commit()

    index = await build_index(db)

    assert match_tickers("Pfizer ships every box from its Belgian plant", index) == []


@pytest.mark.asyncio
async def test_build_index_is_deterministic_across_dual_listings(db):
    """A US line and its home line must not fight over the same company name."""
    db.add_all(
        [
            Stock(ticker="AZN", company_name="AstraZeneca PLC", sector="pharma"),
            Stock(ticker="AZN.L", company_name="AstraZeneca PLC", sector="pharma"),
        ]
    )
    await db.commit()

    first = await build_index(db)
    second = await build_index(db)

    assert first.names["astrazeneca"] == second.names["astrazeneca"]
    # Both listings of one company, so a story reaches the home line too.
    assert first.names["astrazeneca"] == ("AZN", "AZN.L")


# --- Against the real universe ----------------------------------------------
# Normalisation bugs are invisible in isolation and obvious here: a name that
# reduces to "merck &" or "eli lilly and" indexes fine and then matches no
# headline anyone would actually write.


def real_index() -> CompanyIndex:
    from app.services.tickers import SEED_TICKERS

    grouped: dict[str, list[str]] = {}
    for seed in SEED_TICKERS:
        key = normalise(seed.company_name)
        if len(key) >= 4:
            grouped.setdefault(key, []).append(seed.ticker)
    return CompanyIndex(
        names={key: tuple(sorted(v)) for key, v in grouped.items()},
        tickers=frozenset(seed.ticker for seed in SEED_TICKERS),
    )


def test_no_seeded_name_reduces_to_a_dangling_connector():
    from app.services.tickers import SEED_TICKERS

    broken = [
        (seed.company_name, normalise(seed.company_name))
        for seed in SEED_TICKERS
        if normalise(seed.company_name).endswith((" &", " and", " of", " the", "-"))
    ]

    assert broken == []


def test_no_seeded_name_reduces_to_nothing():
    """A name that normalises away entirely can never be matched."""
    from app.services.tickers import SEED_TICKERS

    empty = [seed.ticker for seed in SEED_TICKERS if not normalise(seed.company_name)]

    assert empty == []


def test_headlines_as_a_wire_would_write_them_match():
    """Each of these is how the company is actually named in copy."""
    index = real_index()
    cases = {
        "Pfizer reports positive phase 3 data in atopic dermatitis": "PFE",
        "Merck wins FDA approval for KEYTRUDA in early-stage lung cancer": "MRK",
        "Eli Lilly raises full-year guidance on tirzepatide demand": "LLY",
        "Novo Nordisk cuts obesity drug price in the US": "NVO",
        "Western Digital announces a new HDD platform for hyperscalers": "WDC",
        "Snowflake beats revenue estimates as AI workloads grow": "SNOW",
        "Applied Materials guides above consensus on WFE spending": "AMAT",
    }

    for headline, expected in cases.items():
        assert expected in match_tickers(headline, index), headline


def test_an_unrelated_headline_matches_nothing():
    index = real_index()

    assert match_tickers("European shares closed higher on Tuesday", index) == []
    assert match_tickers("The weather in Copenhagen was mild", index) == []


def test_a_dual_listed_company_returns_every_line():
    """A story about the company belongs on all of its listings.

    This is what gives a home line news at all: US wires write about "Novo
    Nordisk", not about NOVO-B.CO, so matching only one line leaves the other
    permanently empty.
    """
    index = real_index()

    tickers = match_tickers("Novo Nordisk cuts obesity drug prices in the US", index)

    assert {"NVO", "NOVO-B.CO"} <= set(tickers)


def test_dual_listings_do_not_exhaust_the_company_limit():
    """The cap counts companies; two lines of one company are still one story."""
    index = real_index()
    headline = "AstraZeneca and Novo Nordisk both reported results this morning"

    tickers = match_tickers(headline, index, limit=2)

    assert {"AZN", "AZN.L"} <= set(tickers)
    assert {"NVO", "NOVO-B.CO"} <= set(tickers)
