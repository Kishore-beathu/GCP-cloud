"""The six added sources: parsing, attribution, and refusing to guess.

Each source is exercised against the payload shape it actually returns, with
the emphasis on what it must *not* do — attribute a story to the wrong company,
store a market-wide event as company news, or turn a quiet day into an error.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import NewsArticle, Stock
from app.services.matching import CompanyIndex, normalise



def index_for(**pairs: str) -> CompanyIndex:
    grouped: dict[str, list[str]] = {}
    for name, ticker in pairs.items():
        grouped.setdefault(normalise(name), []).append(ticker)
    return CompanyIndex(
        names={key: tuple(sorted(v)) for key, v in grouped.items()},
        tickers=frozenset(pairs.values()),
    )


def mock_http(monkeypatch, handler) -> None:
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: original(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
    )


def rfc822(when: datetime) -> str:
    return when.strftime("%a, %d %b %Y %H:%M:%S GMT")


# --- SEC EDGAR firehose ------------------------------------------------------


def test_edgar_title_is_split_into_form_and_company():
    from app.integrations.edgar_firehose import parse_title

    form, company = parse_title("8-K - PFIZER INC (0000078003) (Filer)")

    assert form == "8-K"
    assert company == "PFIZER INC"


def test_edgar_title_that_does_not_match_is_rejected():
    from app.integrations.edgar_firehose import parse_title

    assert parse_title("something unexpected") == (None, None)


def test_edgar_headline_expands_the_form_code():
    """"8-K" carries no sentiment; its official title does."""
    from app.integrations.edgar_firehose import to_headline

    headline = to_headline("8-K", "PFIZER INC")

    assert headline.startswith("PFIZER INC filed 8-K")
    assert len(headline) > len("PFIZER INC filed 8-K")


@pytest.mark.asyncio
async def test_edgar_firehose_stores_a_matching_filing(db, seeded_stocks, monkeypatch):
    from app.integrations.edgar_firehose import ingest_recent_filings

    now = datetime.now(timezone.utc)
    atom = f"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>8-K - PFIZER INC (0000078003) (Filer)</title>
        <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/78003/x.htm"/>
        <updated>{now.isoformat()}</updated>
      </entry>
      <entry>
        <title>8-K - SOME OTHER COMPANY (0000000001) (Filer)</title>
        <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1/y.htm"/>
        <updated>{now.isoformat()}</updated>
      </entry>
    </feed>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=atom))

    report = await ingest_recent_filings(db, forms=("8-K",))

    assert report.added == 1  # the untracked registrant is ignored
    article = (await db.execute(select(NewsArticle))).scalar_one()
    assert "PFIZER" in article.headline


@pytest.mark.asyncio
async def test_edgar_firehose_ignores_filings_older_than_the_window(
    db, seeded_stocks, monkeypatch
):
    from app.integrations.edgar_firehose import ingest_recent_filings

    old = datetime.now(timezone.utc) - timedelta(hours=6)
    atom = f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>8-K - PFIZER INC (0000078003) (Filer)</title>
      <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/78003/x.htm"/>
      <updated>{old.isoformat()}</updated>
    </entry></feed>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=atom))

    assert (await ingest_recent_filings(db, forms=("8-K",), lookback_minutes=30)).added == 0


# --- FDA ---------------------------------------------------------------------


def test_fda_recall_headline_carries_the_class():
    """Class I is "serious adverse health consequences or death" — it must show."""
    from app.integrations.fda import _recall_headline

    headline = _recall_headline(
        {
            "recalling_firm": "Pfizer Inc.",
            "product_description": "Nurtec ODT 75mg tablets",
            "classification": "Class I",
            "reason_for_recall": "Cross-contamination with another product",
        }
    )

    assert "Pfizer" in headline
    assert "Class I (most serious)" in headline
    assert "Cross-contamination" in headline


def test_fda_recall_without_a_firm_is_dropped():
    from app.integrations.fda import _recall_headline

    assert _recall_headline({"product_description": "Something"}) is None


def test_fda_enforcement_attributes_to_the_recalling_firm_only():
    """A product description naming another brand must not misattribute."""
    from app.integrations.fda import parse_enforcement

    index = index_for(**{"Pfizer Inc.": "PFE", "Merck & Co.": "MRK"})
    payload = {
        "results": [
            {
                "recalling_firm": "Pfizer Inc.",
                # Merck is named in the product text but did not recall anything.
                "product_description": "Generic equivalent of Merck's product",
                "classification": "Class II",
                "reason_for_recall": "Labelling error",
                "recall_number": "D-1234-2026",
                "report_date": "20260810",
            }
        ]
    }

    articles = parse_enforcement(payload, index, "drug/enforcement.json")

    assert [a.ticker for a in articles] == ["PFE"]


def test_fda_enforcement_without_a_recall_number_is_dropped():
    """The recall number is the identity; without it dedup cannot work."""
    from app.integrations.fda import parse_enforcement

    payload = {
        "results": [
            {
                "recalling_firm": "Pfizer Inc.",
                "product_description": "Tablets",
                "classification": "Class II",
            }
        ]
    }

    assert parse_enforcement(payload, index_for(**{"Pfizer Inc.": "PFE"}), "x") == []


@pytest.mark.asyncio
async def test_openfda_404_is_an_empty_result_not_an_error(monkeypatch):
    """openFDA answers 404 when a search matches nothing — a quiet day."""
    from app.integrations.fda import fetch_enforcement

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404, json={}))
    ) as client:
        assert await fetch_enforcement(client, "drug/enforcement.json", datetime.now().date(), 10) == {}


# --- Yahoo per-ticker news ---------------------------------------------------


@pytest.mark.asyncio
async def test_yahoo_news_keeps_stories_that_name_the_company(db, seeded_stocks, monkeypatch):
    from app.integrations.yahoo_news import ingest_yahoo_news

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>Moderna reports positive Phase 3 data</title>
        <link>https://finance.example.com/1</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))
    monkeypatch.setattr("app.integrations.yahoo_news.REQUEST_DELAY_SECONDS", 0)

    report = await ingest_yahoo_news(db, ["MRNA"])

    assert report.added == 1
    article = (await db.execute(select(NewsArticle))).scalar_one()
    assert article.source == "yahoo_news"


@pytest.mark.asyncio
async def test_yahoo_news_drops_the_market_commentary_in_a_symbols_feed(
    db, seeded_stocks, monkeypatch
):
    """Requesting per symbol does not mean the feed is about that symbol.

    Yahoo answers ?s=AMZN with what it thinks an Amazon holder might want to
    read, so the feed carried "Bull of the Day: Carter's (CRI)" — which scored
    +1.00 and was stored as positive news for Amazon. Sentiment is a pillar of
    the ranked score, so one company's good news was lifting another's rank,
    and the same row would put it on the shortlist as a symbol with a catalyst.

    This test previously asserted the opposite, pinning the bug in place: it
    fed in "An article that never names the company" and required it to be
    stored, on the reasoning that a per-symbol request needs no matching.
    """
    from app.integrations.yahoo_news import ingest_yahoo_news

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>Bull of the Day: Carter's (CRI)</title>
        <link>https://finance.example.com/2</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
      <item>
        <title>Billionaire David Tepper Sold Every Share of UnitedHealth</title>
        <link>https://finance.example.com/3</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))
    monkeypatch.setattr("app.integrations.yahoo_news.REQUEST_DELAY_SECONDS", 0)

    report = await ingest_yahoo_news(db, ["MRNA"])

    assert report.added == 0


@pytest.mark.asyncio
async def test_yahoo_news_matches_a_company_named_without_its_full_legal_name(
    db, seeded_stocks, monkeypatch
):
    """Headlines say "Pfizer", never "Pfizer Inc.".

    Filtering on the stored company name as a substring would drop nearly every
    genuine story, trading one silent corruption for another.
    """
    from app.integrations.yahoo_news import ingest_yahoo_news

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>Pfizer wins FDA approval for expanded label</title>
        <link>https://finance.example.com/4</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))
    monkeypatch.setattr("app.integrations.yahoo_news.REQUEST_DELAY_SECONDS", 0)

    report = await ingest_yahoo_news(db, ["PFE"])

    assert report.added == 1


@pytest.mark.asyncio
async def test_yahoo_news_matches_a_bare_symbol_in_the_headline(
    db, seeded_stocks, monkeypatch
):
    """A headline can quote the ticker instead of the name."""
    from app.integrations.yahoo_news import ingest_yahoo_news

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>MRNA jumps on trial readout</title>
        <link>https://finance.example.com/5</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))
    monkeypatch.setattr("app.integrations.yahoo_news.REQUEST_DELAY_SECONDS", 0)

    report = await ingest_yahoo_news(db, ["MRNA"])

    assert report.added == 1


@pytest.mark.asyncio
async def test_yahoo_news_respects_the_disable_switch(db, seeded_stocks, monkeypatch):
    from app.integrations.yahoo_news import ingest_yahoo_news

    monkeypatch.setenv("YAHOO_NEWS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert (await ingest_yahoo_news(db, ["MRNA"])).added == 0
    finally:
        get_settings.cache_clear()


# --- Newswires ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_newswire_matches_on_the_headline_not_the_body(db, seeded_stocks, monkeypatch):
    """Release bodies name partners and acquirers; matching them misattributes."""
    from app.integrations.newswire import Wire, ingest_newswires

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>Moderna reports positive interim data</title>
        <link>https://wire.example.com/1</link>
        <pubDate>{rfc822(now)}</pubDate>
        <description>The study was run in partnership with Pfizer Inc.</description>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))

    report = await ingest_newswires(
        db, wires=(Wire(key="t", name="Test", url="https://wire.example.com/rss"),)
    )

    assert report.added == 1
    rows = (await db.execute(select(Stock.ticker).join(NewsArticle, NewsArticle.ticker_id == Stock.id))).scalars().all()
    assert rows == ["MRNA"]  # not PFE, which is only in the body


@pytest.mark.asyncio
async def test_newswire_ignores_items_naming_nobody_tracked(db, seeded_stocks, monkeypatch):
    from app.integrations.newswire import Wire, ingest_newswires

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>A private company raises a Series B</title>
        <link>https://wire.example.com/2</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))

    report = await ingest_newswires(
        db, wires=(Wire(key="t", name="Test", url="https://wire.example.com/rss"),)
    )

    assert report.added == 0


# --- Trading halts -----------------------------------------------------------


def test_halt_title_yields_symbol_and_reason():
    from app.integrations.halts import parse_halt_title

    assert parse_halt_title("MRNA - T1 - News Pending") == ("MRNA", "T1")
    assert parse_halt_title("PFE | LUDP | Volatility Pause") == ("PFE", "LUDP")


def test_halt_title_without_a_symbol_is_rejected():
    from app.integrations.halts import parse_halt_title

    symbol, _ = parse_halt_title("")
    assert symbol is None


@pytest.mark.asyncio
async def test_news_pending_halt_is_stored(db, seeded_stocks, monkeypatch):
    """A T1 halt says an announcement exists before it has been made."""
    from app.integrations.halts import ingest_halts

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>MRNA - T1 - News Pending</title>
        <link>https://nasdaqtrader.example.com/halt/1</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))

    report = await ingest_halts(db)

    assert report.added == 1
    article = (await db.execute(select(NewsArticle))).scalar_one()
    assert "news pending" in article.headline


@pytest.mark.asyncio
async def test_market_wide_halts_are_not_stored_as_company_news(
    db, seeded_stocks, monkeypatch
):
    """A circuit breaker is not a fact about any one issuer."""
    from app.integrations.halts import ingest_halts

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>MRNA - MWC1 - Market Wide Circuit Breaker</title>
        <link>https://nasdaqtrader.example.com/halt/2</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))

    assert (await ingest_halts(db)).added == 0


@pytest.mark.asyncio
async def test_halts_for_untracked_symbols_are_ignored(db, seeded_stocks, monkeypatch):
    from app.integrations.halts import ingest_halts

    now = datetime.now(timezone.utc)
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>ZZZZ - T1 - News Pending</title>
        <link>https://nasdaqtrader.example.com/halt/3</link>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))

    assert (await ingest_halts(db)).added == 0


# --- Clinical trials ---------------------------------------------------------


def test_trial_status_changes_are_filtered_to_the_notable_ones():
    """"Recruiting" churns constantly and resolves nothing."""
    from app.integrations.clinical import parse_studies

    index = index_for(**{"Moderna Inc.": "MRNA"})

    def study(status: str, nct: str) -> dict:
        return {
            "protocolSection": {
                "identificationModule": {"nctId": nct, "briefTitle": "A study of mRNA-1283"},
                "statusModule": {
                    "overallStatus": status,
                    "lastUpdatePostDateStruct": {"date": "2026-08-10"},
                },
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Moderna Inc."}},
                "designModule": {"phases": ["PHASE3"]},
            }
        }

    articles = parse_studies(
        {"studies": [study("RECRUITING", "NCT1"), study("TERMINATED", "NCT2")]}, index
    )

    assert len(articles) == 1
    assert "terminated early" in articles[0].headline
    assert articles[0].url.endswith("NCT2")


def test_trial_is_attributed_to_the_sponsor_not_the_title():
    """A trial title names the compound; a competitor's name in it must not match."""
    from app.integrations.clinical import parse_studies

    index = index_for(**{"Moderna Inc.": "MRNA", "Pfizer Inc.": "PFE"})
    payload = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {
                        "nctId": "NCT3",
                        "briefTitle": "mRNA-1283 versus Pfizer comparator",
                    },
                    "statusModule": {
                        "overallStatus": "COMPLETED",
                        "lastUpdatePostDateStruct": {"date": "2026-08-10"},
                    },
                    "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Moderna Inc."}},
                    "designModule": {"phases": ["PHASE3"]},
                }
            }
        ]
    }

    assert [a.ticker for a in parse_studies(payload, index)] == ["MRNA"]


def test_trials_without_a_tracked_sponsor_are_dropped():
    from app.integrations.clinical import parse_studies

    payload = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {"nctId": "NCT4", "briefTitle": "A study"},
                    "statusModule": {"overallStatus": "COMPLETED"},
                    "sponsorCollaboratorsModule": {
                        "leadSponsor": {"name": "A University Hospital"}
                    },
                }
            }
        ]
    }

    assert parse_studies(payload, index_for(**{"Moderna Inc.": "MRNA"})) == []


# --- Sources that work without their optional feed ---------------------------
# Half of each of these sources is a JSON API and half is an RSS feed. When a
# publisher moves the feed, the API half must keep running rather than the
# whole source going dark.


@pytest.mark.asyncio
async def test_fda_runs_without_a_press_feed_url(db, seeded_stocks, monkeypatch):
    """openFDA enforcement is the more valuable half and needs no feed URL."""
    from app.integrations.fda import ingest_fda

    monkeypatch.setenv("FDA_PRESS_FEED", "")
    get_settings.cache_clear()
    try:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "recalling_firm": "Moderna Inc.",
                            "product_description": "mRNA-1273 vials",
                            "classification": "Class II",
                            "reason_for_recall": "Labelling error",
                            "recall_number": "D-9999-2026",
                            "report_date": "20260810",
                        }
                    ]
                },
            )

        mock_http(monkeypatch, handler)

        report = await ingest_fda(db)

        assert report.added == 1
        # Only the openFDA endpoints were called; no feed request was made.
        assert all("api.fda.gov" in url for url in requested)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_clinical_runs_without_an_ema_feed_url(db, seeded_stocks, monkeypatch):
    from app.integrations.clinical import ingest_clinical_and_regulatory

    monkeypatch.setenv("EMA_FEED", "")
    get_settings.cache_clear()
    try:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "studies": [
                        {
                            "protocolSection": {
                                "identificationModule": {
                                    "nctId": "NCT9",
                                    "briefTitle": "A study of mRNA-1283",
                                },
                                "statusModule": {
                                    "overallStatus": "TERMINATED",
                                    "lastUpdatePostDateStruct": {"date": "2026-08-10"},
                                },
                                "sponsorCollaboratorsModule": {
                                    "leadSponsor": {"name": "Moderna Inc."}
                                },
                                "designModule": {"phases": ["PHASE3"]},
                            }
                        }
                    ]
                },
            )

        mock_http(monkeypatch, handler)

        report = await ingest_clinical_and_regulatory(db)

        assert report.added == 1
        assert all("clinicaltrials.gov" in url for url in requested)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_a_company_mentioned_deep_in_someone_elses_story_is_dropped(
    db, seeded_stocks, monkeypatch
):
    """The live case: a rival's press release stored as this company's news.

    "GeoVax Highlights Gedeptin(R) Tumor-Priming Strategy as Immuno-Oncology
    Enters New Phase" was filed under Replimune at +1.00, because Replimune
    appeared far down the body as a comparator. Matching the whole body made
    any passing mention count as authorship.
    """
    from app.integrations.yahoo_news import ingest_yahoo_news

    now = datetime.now(timezone.utc)
    rival = (
        "GeoVax Labs today announced results for Gedeptin in head and neck "
        "cancer. " + ("The immuno-oncology field has expanded rapidly. " * 8)
        + "Analysts compared the approach to Moderna and others."
    )
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>GeoVax Highlights Gedeptin Tumor-Priming Strategy</title>
        <link>https://finance.example.com/rival</link>
        <description>{rival}</description>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))
    monkeypatch.setattr("app.integrations.yahoo_news.REQUEST_DELAY_SECONDS", 0)

    report = await ingest_yahoo_news(db, ["MRNA"])

    assert report.added == 0


@pytest.mark.asyncio
async def test_a_release_that_names_the_company_in_its_lead_is_kept(
    db, seeded_stocks, monkeypatch
):
    """Headline-only would be too strict; a release names its subject up front.

    Plenty of genuine releases carry a headline about the drug or the result
    rather than the company, with the company in the dateline or first
    sentence. Those must survive.
    """
    from app.integrations.yahoo_news import ingest_yahoo_news

    now = datetime.now(timezone.utc)
    lead = (
        "CAMBRIDGE, Mass. — Moderna Inc. today announced that its candidate "
        "met the primary endpoint in a pivotal study."
    )
    rss = f"""<rss version="2.0"><channel>
      <item>
        <title>Wins FDA Approval For Advanced Melanoma Treatment</title>
        <link>https://finance.example.com/lead</link>
        <description>{lead}</description>
        <pubDate>{rfc822(now)}</pubDate>
      </item>
    </channel></rss>"""
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=rss))
    monkeypatch.setattr("app.integrations.yahoo_news.REQUEST_DELAY_SECONDS", 0)

    report = await ingest_yahoo_news(db, ["MRNA"])

    assert report.added == 1
