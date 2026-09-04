"""Reading what an 8-K said, not just which item it reported.

The platform knew *that* an 8-K was filed and which items it carried. The item
code says "Other Events"; the document says the trial met its endpoint. That
gap is the largest untapped signal in the stack, and it scored exactly 0.00.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import OperationalError

from app.services.filing_text import (
    MAX_BODY_CHARS,
    extract_item,
    prepare,
    strip_boilerplate,
    to_text,
)
from app.services.sentiment import LexiconAnalyzer

ANNOUNCEMENT = (
    "On August 20, 2026, the Company announced that its Phase 3 trial met its "
    "primary endpoint, with a statistically significant improvement in overall "
    "survival. The therapy was well tolerated."
)

BOILERPLATE = (
    "Forward-Looking Statements. This report contains forward-looking "
    "statements involving risks and uncertainties, including the risk of delay "
    "or failure to obtain approval."
)


def _filing(body: str = ANNOUNCEMENT, tail: str = BOILERPLATE) -> str:
    return f"""<html><body>
      <p>Item 8.01 Other Events.</p>
      <p>{body}</p>
      <p>{tail}</p>
      <p>Item 9.01 Financial Statements and Exhibits.</p>
      <p>99.1 Press release dated August 20, 2026.</p>
    </body></html>"""


# --- Extraction ---------------------------------------------------------------


def test_block_tags_become_line_breaks():
    """Without breaks, the last word of a paragraph joins the first of the next.

    That is how "the endpoint was met" and "Risks include failure" become one
    sentence, and one sentence is what the negation window works over.
    """
    text = to_text("<p>Endpoint met.</p><p>Risks include failure.</p>")

    assert "met.\nRisks" in text or "met.\n\nRisks" in text


def test_only_the_target_item_is_extracted():
    """An 8-K is mostly its exhibit list.

    Scoring the whole document would read a press-release title in an exhibit
    index as though it were the announcement.
    """
    section = extract_item(to_text(_filing()))

    assert "primary endpoint" in section
    assert "99.1 Press release" not in section


def test_the_item_title_is_not_treated_as_content():
    section = extract_item(to_text(_filing()))

    assert not section.lower().startswith("other events")


def test_boilerplate_is_cut_before_scoring():
    """Every 8-K carries it, so it says nothing about any of them.

    "Forward-looking statements" alone contains risk, uncertainty, delay and
    failure — four negative terms in wording identical across every filing ever
    made, which would drag a short announcement negative on language that is
    not about the announcement.
    """
    cleaned = strip_boilerplate(f"{ANNOUNCEMENT} {BOILERPLATE}")

    assert "primary endpoint" in cleaned
    assert "forward-looking" not in cleaned.lower()


def test_a_section_that_is_only_boilerplate_is_rejected_entirely():
    """A safe-harbour notice with no announcement attached is not news.

    Cutting it to nothing and refusing to store it beats keeping it: stored,
    it would score negative on language every filing carries.
    """
    assert strip_boilerplate(BOILERPLATE) == ""
    assert prepare(_filing(BOILERPLATE, tail="")) is None


def test_a_short_announcement_still_has_its_boilerplate_cut():
    """The bug the first version had, pinned.

    Two attempts at a minimum-content floor both failed the same way, at two
    hundred characters and then at forty. Short filings are exactly where the
    boilerplate does most damage — there is least real text to outweigh it —
    so the floor is gone entirely.
    """
    short = "The trial met its primary endpoint. " + BOILERPLATE

    assert "forward-looking" not in strip_boilerplate(short).lower()


# --- What it is for -----------------------------------------------------------


def test_a_filing_that_scored_nothing_now_scores_its_news():
    analyzer = LexiconAnalyzer()
    headline = "Company filed 8-K: Other Events"

    assert analyzer.score(headline).score == 0.0
    assert analyzer.score(headline, prepare(_filing())).score > 0.5


def test_a_failed_readout_reads_as_bad_news():
    """The direction has to survive the extraction, not just the presence."""
    analyzer = LexiconAnalyzer()
    bad = _filing(
        "The Company announced that the trial did not meet its primary endpoint "
        "and the programme will be discontinued."
    )

    assert analyzer.score("Company filed 8-K: Other Events", prepare(bad)).score < -0.5


# --- Refusing to score nothing ------------------------------------------------


def test_a_pointer_to_an_exhibit_is_not_an_announcement():
    """"See the attached press release" is a signpost, not news.

    Stored, it would add a neutral row and a little false confidence to the
    news-volume factor, which is exactly the kind of filler already removed
    from two other sources.
    """
    assert prepare(_filing("See Exhibit 99.1.", tail="")) is None


def test_a_filing_without_the_target_item_yields_nothing():
    other = "<html><body><p>Item 5.02 Departure of Directors.</p><p>A director resigned.</p></body></html>"

    assert prepare(other) is None


def test_malformed_html_does_not_raise():
    assert prepare("<p>Item 8.01<<<>>") is None


def test_a_long_filing_is_truncated_to_the_scorer_s_limit():
    long_body = ANNOUNCEMENT + (" The study continued as planned." * 400)

    prepared = prepare(_filing(long_body, tail=""))

    assert prepared is not None
    assert len(prepared) <= MAX_BODY_CHARS


# --- Wired into the ingest ----------------------------------------------------


@pytest.mark.asyncio
async def test_an_8k_reporting_item_801_gets_its_narrative_as_the_body():
    """The whole point, end to end.

    Before this the body was the item title — "Other Events" — which is the
    drawer the filing was put in rather than what it says, and scores zero.
    """
    import httpx

    from app.integrations.sec import fetch_sec_filings

    submissions = {
        "name": "Test Pharma Inc.",
        "filings": {
            "recent": {
                "form": ["8-K"],
                "accessionNumber": ["0001234567-26-000001"],
                "filingDate": ["2026-08-20"],
                "primaryDocument": ["form8k.htm"],
                "primaryDocDescription": ["8-K"],
                "items": ["8.01"],
            }
        },
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if "submissions" in str(request.url):
            return httpx.Response(200, json=submissions)
        return httpx.Response(200, text=_filing())

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        articles = await fetch_sec_filings(client, "TSTP", "0001234567")

    assert len(articles) == 1
    assert "primary endpoint" in articles[0].body
    assert LexiconAnalyzer().score(articles[0].headline, articles[0].body).score > 0.5


@pytest.mark.asyncio
async def test_a_filing_whose_document_cannot_be_read_still_produces_an_article():
    """Soft failure. A missing body loses signal; an exception loses the ingest."""
    import httpx

    from app.integrations.sec import fetch_sec_filings

    submissions = {
        "name": "Test Pharma Inc.",
        "filings": {
            "recent": {
                "form": ["8-K"],
                "accessionNumber": ["0001234567-26-000001"],
                "filingDate": ["2026-08-20"],
                "primaryDocument": ["form8k.htm"],
                "primaryDocDescription": ["8-K"],
                "items": ["8.01"],
            }
        },
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if "submissions" in str(request.url):
            return httpx.Response(200, json=submissions)
        return httpx.Response(404, text="not found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        articles = await fetch_sec_filings(client, "TSTP", "0001234567")

    assert len(articles) == 1
    assert "Other Events" in articles[0].body


@pytest.mark.asyncio
async def test_other_item_types_are_not_fetched():
    """One item type, proven, before widening — and one request each costs.

    An 8-K reporting a director's departure is not fetched at all, so the
    extra request is spent only where a narrative is expected.
    """
    import httpx

    from app.integrations.sec import fetch_sec_filings

    fetched: list[str] = []
    submissions = {
        "name": "Test Pharma Inc.",
        "filings": {
            "recent": {
                "form": ["8-K"],
                "accessionNumber": ["0001234567-26-000002"],
                "filingDate": ["2026-08-20"],
                "primaryDocument": ["form8k.htm"],
                "primaryDocDescription": ["8-K"],
                "items": ["5.02"],
            }
        },
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        if "submissions" in str(request.url):
            return httpx.Response(200, json=submissions)
        return httpx.Response(200, text=_filing())

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        await fetch_sec_filings(client, "TSTP", "0001234567")

    assert all("submissions" in url for url in fetched)


# --- Backfilling what was ingested before ------------------------------------


@pytest.mark.asyncio
async def test_the_backfill_reaches_filings_already_stored(db, seeded_stocks, monkeypatch):
    """Ingestion dedupes on the article URL, so a stored filing is never revisited.

    Without this, reading filing text applies only to filings arriving from now
    on and never to the corpus the sentiment pillar and the backtest read —
    which makes the feature decorative.
    """
    import httpx

    from app.models import NewsArticle, SentimentScore
    from app.services.rescore import backfill_filing_text

    stock = seeded_stocks[0]
    article = NewsArticle(
        ticker_id=stock.id,
        headline=f"{stock.company_name} filed 8-K: Other Events",
        body="Form 8-K filed 2026-08-20 (Current report). Reported items: Other Events.",
        source="sec_edgar",
        url="https://www.sec.gov/Archives/edgar/data/1/2/form8k.htm",
        published_at=datetime.now(timezone.utc),
    )
    db.add(article)
    await db.flush()
    db.add(
        SentimentScore(
            article_id=article.id, sentiment="neutral", score=0.0, confidence=0.25,
            event_type="other", event_confidence=0.0, model_version="lexicon-v1",
        )
    )
    await db.commit()

    monkeypatch.setattr(
        httpx.AsyncClient,
        "get",
        lambda self, url, **kwargs: _response(_filing()),
    )

    report = await backfill_filing_text(db)

    assert report.updated == 1
    assert report.rescored == 1
    await db.refresh(article)
    assert "primary endpoint" in article.body


@pytest.mark.asyncio
async def test_a_filing_that_already_has_its_narrative_is_left_alone(db, seeded_stocks):
    """A long body already carries the text; fetching it again spends a request."""
    from app.models import NewsArticle
    from app.services.rescore import backfill_filing_text

    stock = seeded_stocks[0]
    db.add(
        NewsArticle(
            ticker_id=stock.id,
            headline="Co filed 8-K: Other Events",
            body="x" * 1200,
            source="sec_edgar",
            url="https://www.sec.gov/Archives/edgar/data/1/2/form8k.htm",
            published_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    report = await backfill_filing_text(db)

    assert report.examined == 0
    assert report.fetched == 0


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


async def _response(text: str):
    return _Resp(text)


def _refusal(status: int):
    """A response that fails ``raise_for_status`` the way httpx really does.

    The status code has to survive into the report, so a hand-rolled stub that
    raises a bare exception would not exercise the thing under test.
    """
    import httpx

    request = httpx.Request("GET", "https://www.sec.gov/x")
    response = httpx.Response(status, request=request)

    async def _get(self, url, **kwargs):
        raise httpx.HTTPStatusError("refused", request=request, response=response)

    return _get


async def _seed_filings(db, stock, count: int) -> None:
    from app.models import NewsArticle

    for index in range(count):
        db.add(
            NewsArticle(
                ticker_id=stock.id,
                headline="Co filed 8-K: Other Events",
                body="Form 8-K filed 2026-08-20 (Current report).",
                source="sec_edgar",
                url=f"https://www.sec.gov/Archives/edgar/data/1/{index}/form8k.htm",
                published_at=datetime.now(timezone.utc),
            )
        )
    await db.commit()


@pytest.mark.asyncio
async def test_a_refused_batch_reports_which_status_refused_it(
    db, seeded_stocks, monkeypatch
):
    """"unreachable: 499" is a count, not a diagnosis.

    The first real run came back with every filing unreachable and no way to
    tell a rate limit from a rejected user agent — which are opposite fixes.
    """
    import httpx

    from app.services import rescore

    monkeypatch.setattr(rescore, "SEC_REQUEST_DELAY_SECONDS", 0.0)
    await _seed_filings(db, seeded_stocks[0], 3)
    monkeypatch.setattr(httpx.AsyncClient, "get", _refusal(429))

    report = await rescore.backfill_filing_text(db)

    assert report.unreachable == 3
    assert report.failures == {"HTTP 429": 3}
    # The status says a source refused us; the URL says whether we asked for
    # the right thing. A 404 on a URL we built wrong and a 429 on a correct
    # one are repaired in different files, and the count alone cannot tell
    # them apart.
    assert report.failed_samples[0]["url"].startswith("https://www.sec.gov/")
    assert report.failed_samples[0]["failure"] == "HTTP 429"


@pytest.mark.asyncio
async def test_the_backfill_stops_rather_than_hammering_a_source_refusing_it(
    db, seeded_stocks, monkeypatch
):
    """Ten refusals in a row is being blocked, not ten bad documents.

    Sending the remaining hundreds anyway is what turns a rate limit into a
    ban, and it produces no information the first ten did not already give.
    """
    import httpx

    from app.services import rescore

    monkeypatch.setattr(rescore, "SEC_REQUEST_DELAY_SECONDS", 0.0)
    await _seed_filings(db, seeded_stocks[0], rescore.MAX_CONSECUTIVE_FAILURES + 15)

    attempts = 0
    refuse = _refusal(403)

    async def _counting_get(self, url, **kwargs):
        nonlocal attempts
        attempts += 1
        return await refuse(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "get", _counting_get)

    report = await rescore.backfill_filing_text(db)

    assert attempts == rescore.MAX_CONSECUTIVE_FAILURES
    assert report.stopped_early is not None
    assert "15 filings untried" in report.stopped_early
    assert report.failures == {"HTTP 403": rescore.MAX_CONSECUTIVE_FAILURES}


@pytest.mark.asyncio
async def test_an_intermittent_failure_does_not_stop_the_run(
    db, seeded_stocks, monkeypatch
):
    """The counter is consecutive failures, not total ones.

    Filings are malformed often enough that a total-failure cap would abandon
    a healthy run partway through.
    """
    import httpx

    from app.services import rescore

    monkeypatch.setattr(rescore, "SEC_REQUEST_DELAY_SECONDS", 0.0)
    await _seed_filings(db, seeded_stocks[0], 12)

    calls = 0
    refuse = _refusal(404)

    async def _every_other(self, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls % 2:
            return await refuse(self, url, **kwargs)
        return _Resp(_filing())

    monkeypatch.setattr(httpx.AsyncClient, "get", _every_other)

    report = await rescore.backfill_filing_text(db)

    assert calls == 12
    assert report.stopped_early is None
    assert report.unreachable == 6
    assert report.fetched == 6


@pytest.mark.asyncio
async def test_a_sample_names_its_ticker_without_a_lazy_load(
    db, seeded_stocks, monkeypatch
):
    """The sample line must not reach through a lazy relationship.

    ``NewsArticle.stock`` is lazily loaded, and touching it inside an async
    session raises MissingGreenlet. The line that did so runs only on a
    successful extraction, so while every fetch was 404ing it never executed —
    and the existing test passed because its Stock was already warm in the
    session's identity map. Expunging first is what makes this a real test:
    it reproduces a cold session, which is what the endpoint actually has.
    """
    import httpx

    from app.models import NewsArticle
    from app.services import rescore

    stock = seeded_stocks[0]
    ticker = stock.ticker
    db.add(
        NewsArticle(
            ticker_id=stock.id,
            headline="Co filed 8-K: Other Events",
            body="Form 8-K filed 2026-08-20 (Current report).",
            source="sec_edgar",
            url="https://www.sec.gov/Archives/edgar/data/1/2/form8k.htm",
            published_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    db.expunge_all()

    monkeypatch.setattr(rescore, "SEC_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        httpx.AsyncClient, "get", lambda self, url, **kwargs: _response(_filing())
    )

    report = await rescore.backfill_filing_text(db)

    assert report.updated == 1
    assert report.samples, "a successful extraction should record a sample"
    assert report.samples[0]["ticker"] == ticker


@pytest.mark.asyncio
async def test_one_bad_filing_does_not_lose_the_run(db, seeded_stocks, monkeypatch):
    """A run of 150 returned a 500 and stored nothing, naming no row.

    Every other SEC path treats a bad document as a soft failure. This one
    raised, so a single unusual filing discarded every good one alongside it
    and left no way to tell which filing was responsible.
    """
    import httpx

    from app.models import NewsArticle, SentimentScore
    from app.services import rescore

    stock = seeded_stocks[0]
    for index in range(3):
        article = NewsArticle(
            ticker_id=stock.id,
            headline="Co filed 8-K: Other Events",
            body="Form 8-K filed 2026-08-20 (Current report).",
            source="sec_edgar",
            url=f"https://www.sec.gov/Archives/edgar/data/1/{index}/form8k.htm",
            published_at=datetime.now(timezone.utc),
        )
        db.add(article)
        await db.flush()
        # The rescore branch is the one that raised, and it runs only for an
        # article that already carries a score.
        db.add(
            SentimentScore(
                article_id=article.id, sentiment="neutral", score=0.0,
                confidence=0.25, event_type="other", event_confidence=0.0,
                model_version="lexicon-v1",
            )
        )
    await db.commit()
    db.expunge_all()

    monkeypatch.setattr(rescore, "SEC_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        httpx.AsyncClient, "get", lambda self, url, **kwargs: _response(_filing())
    )

    calls = 0
    real = rescore.overlay_key

    def _explode_once(sector):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("unexpected sector shape")
        return real(sector)

    monkeypatch.setattr(rescore, "overlay_key", _explode_once)

    report = await rescore.backfill_filing_text(db)

    # The two healthy filings still land, and the failure is named.
    assert report.fetched == 3
    assert report.updated == 2
    assert report.errors == {"ValueError": 1}
    assert report.error_samples[0]["error"] == "ValueError"
    assert report.error_samples[0]["url"].startswith("https://www.sec.gov/")


@pytest.mark.asyncio
async def test_a_failed_commit_is_reported_rather_than_raised(
    db, seeded_stocks, monkeypatch
):
    """The commit is the last step that can lose the whole run silently.

    It sits outside the per-filing guard by nature — a lock conflict with the
    scheduler surfaces there, not on any one article — so without this the
    response is a bare 500 naming nothing, which is the failure mode this
    endpoint has already produced twice.
    """
    import httpx

    from app.models import NewsArticle
    from app.services import rescore

    stock = seeded_stocks[0]
    db.add(
        NewsArticle(
            ticker_id=stock.id,
            headline="Co filed 8-K: Other Events",
            body="Form 8-K filed 2026-08-20 (Current report).",
            source="sec_edgar",
            url="https://www.sec.gov/Archives/edgar/data/1/9/form8k.htm",
            published_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    db.expunge_all()

    monkeypatch.setattr(rescore, "SEC_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        httpx.AsyncClient, "get", lambda self, url, **kwargs: _response(_filing())
    )

    async def _locked():
        raise OperationalError("commit", {}, Exception("database is locked"))

    monkeypatch.setattr(db, "commit", _locked)

    report = await rescore.backfill_filing_text(db)

    assert "OperationalError" in report.errors
    assert report.stopped_early is not None
    assert "nothing was saved" in report.stopped_early
    # The counts must not claim work that was rolled back.
    assert report.updated == 0
    assert report.rescored == 0
