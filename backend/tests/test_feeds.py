"""The shared feed reader: three dialects, three date formats, one shape."""

from __future__ import annotations

from datetime import timezone

import httpx
import pytest

from app.services.feeds import fetch_feed, parse_datetime, parse_feed, strip_html

RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Wire</title>
    <item>
      <title>Pfizer announces positive phase 3 results</title>
      <link>https://example.com/1</link>
      <pubDate>Mon, 10 Aug 2026 09:15:00 GMT</pubDate>
      <description>&lt;p&gt;The trial &lt;b&gt;met&lt;/b&gt; its endpoint.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Older item</title>
      <link>https://example.com/0</link>
      <pubDate>Sun, 09 Aug 2026 09:15:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>8-K - PFIZER INC (0000078003) (Filer)</title>
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1/x.htm"/>
    <updated>2026-08-10T09:20:00-04:00</updated>
    <summary>Filed 8-K</summary>
    <id>urn:tag:sec.gov,2026:accession-number=0000078003-26-000123</id>
  </entry>
</feed>
"""


def test_parses_rss_items():
    entries = parse_feed(RSS)

    assert len(entries) == 2
    assert entries[0].title == "Pfizer announces positive phase 3 results"
    assert entries[0].link == "https://example.com/1"
    assert entries[0].published_at.tzinfo is not None


def test_returns_newest_first():
    """Callers slice by a cutoff, so ordering has to be dependable."""
    entries = parse_feed(RSS)

    assert entries[0].published_at > entries[1].published_at


def test_parses_atom_with_a_link_attribute():
    """Atom puts the URL in an attribute; RSS puts it in the text."""
    entry = parse_feed(ATOM)[0]

    assert entry.link == "https://www.sec.gov/Archives/edgar/data/1/x.htm"
    assert entry.title.startswith("8-K - PFIZER INC")
    assert entry.published_at.utcoffset().total_seconds() == -4 * 3600


def test_malformed_xml_yields_nothing_rather_than_raising():
    """One broken feed must not stop a cycle covering five others."""
    assert parse_feed("<rss><channel><item>") == []
    assert parse_feed("") == []


def test_items_without_a_usable_date_are_dropped():
    """An undated item cannot be aligned to a price move."""
    xml = """<rss><channel>
      <item><title>No date</title><link>https://example.com/x</link></item>
    </channel></rss>"""

    assert parse_feed(xml) == []


def test_items_without_a_link_are_dropped():
    xml = """<rss><channel>
      <item><title>No link</title><pubDate>Mon, 10 Aug 2026 09:15:00 GMT</pubDate></item>
    </channel></rss>"""

    assert parse_feed(xml) == []


def test_parse_datetime_reads_all_three_formats():
    rfc822 = parse_datetime("Mon, 10 Aug 2026 09:15:00 GMT")
    iso = parse_datetime("2026-08-10T09:15:00+00:00")
    zulu = parse_datetime("2026-08-10T09:15:00Z")

    assert rfc822 == iso == zulu


def test_naive_timestamps_are_treated_as_utc():
    """Guessing a local zone would shift an announcement by hours."""
    parsed = parse_datetime("2026-08-10T09:15:00")

    assert parsed.tzinfo is timezone.utc


def test_parse_datetime_handles_junk():
    assert parse_datetime("last Tuesday") is None
    assert parse_datetime(None) is None
    assert parse_datetime("") is None


def test_strip_html_leaves_scoreable_words():
    """The lexicon scores words; unstripped markup is tokens with no signal."""
    cleaned = strip_html("<p>The trial <b>met</b> its <i>primary endpoint</i>.</p>")

    assert cleaned == "The trial met its primary endpoint ."
    assert "<" not in cleaned


def test_strip_html_truncates_and_handles_empty():
    assert strip_html("x" * 5000, limit=100) == "x" * 100
    assert strip_html(None) is None
    assert strip_html("   ") is None


# --- Redirects ---------------------------------------------------------------
# httpx does not follow redirects by default, and several publishers answer a
# 3xx for their canonical feed URL. Without following them the feed reads as
# permanently empty and nothing says why.


@pytest.mark.asyncio
async def test_fetch_feed_follows_a_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rss.xml":
            return httpx.Response(302, headers={"Location": "https://e.com/real.xml"})
        return httpx.Response(200, text=RSS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        entries = await fetch_feed(client, "https://e.com/rss.xml")

    assert len(entries) == 2


@pytest.mark.asyncio
async def test_fetch_feed_returns_empty_on_a_network_error():
    """A dead host must not raise into an ingest cycle covering five others."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_feed(client, "https://nowhere.invalid/rss") == []
