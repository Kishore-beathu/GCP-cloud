"""A small RSS/Atom reader, shared by every feed-based source.

Regulators and newswires all publish XML feeds and none of them agree on the
dialect: RSS 2.0, Atom, RDF, with or without namespaces, and three date
formats. This normalises them into one shape so each integration only has to
decide *which* feed to read and how to attach a ticker to an entry.

Deliberately stdlib-only. A feed parser is a small amount of code and a large
amount of dependency surface — and this one runs against untrusted XML from
several vendors, so `defusedxml`-style hardening matters more than features.
``xml.etree`` with entity resolution left off is the conservative choice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

# Feeds routinely omit namespaces or invent their own, so tags are matched on
# the local name rather than the fully-qualified one.
_NS = re.compile(r"^\{[^}]+\}")

DEFAULT_USER_AGENT = "trading-intelligence-agent/1.0 (+personal research use)"


@dataclass(frozen=True)
class FeedEntry:
    """One item from a feed, in the fields every dialect actually carries."""

    title: str
    link: str
    published_at: datetime
    summary: str | None = None
    # Kept because several regulators put the useful identifier here rather
    # than in the link — an accession number, a trial ID, a filing type.
    identifier: str | None = None


def _local(tag: str) -> str:
    return _NS.sub("", tag)


def _text(element: ElementTree.Element, *names: str) -> str | None:
    """First non-empty child matching ``names``, in the order ``names`` gives.

    The order is the caller's preference order, so each name is searched
    across all children before the next name is tried. Scanning the children
    once and taking whichever tag turned up first instead made the result
    depend on the feed's element order: ``("link", "guid")`` returned the guid
    whenever the feed happened to emit ``<guid>`` first, which most RSS does.
    """
    for name in names:
        for child in element:
            if _local(child.tag) != name:
                continue
            if child.text and child.text.strip():
                return child.text.strip()
            # Atom puts the URL in an attribute rather than in the text.
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return None


def _link(element: ElementTree.Element) -> str | None:
    """The entry's article URL, or None when it has nothing usable.

    A guid is only a URL when the feed says so. RSS marks the difference with
    ``isPermaLink="false"``, and Yahoo uses that for an opaque internal id --
    which, stored as the article's URL, rendered as a *relative* href and left
    the reader on the dashboard when they clicked it.
    """
    for child in element:
        if _local(child.tag) != "link":
            continue
        # Atom repeats <link> with rel="self"/"edit"/"replies"; the article
        # itself is rel="alternate", which is also the default when absent.
        if child.attrib.get("rel", "alternate") != "alternate":
            continue
        candidate = (child.text or "").strip() or child.attrib.get("href", "").strip()
        if _is_url(candidate):
            return candidate

    for name in ("guid", "id"):
        for child in element:
            if _local(child.tag) != name:
                continue
            if child.attrib.get("isPermaLink", "").lower() == "false":
                continue
            candidate = (child.text or "").strip()
            if _is_url(candidate):
                return candidate
    return None


def _is_url(value: str | None) -> bool:
    """Absolute http(s) only — a relative path would resolve against our own
    origin and navigate the reader nowhere."""
    return bool(value) and value.lower().startswith(("http://", "https://"))


def parse_datetime(value: str | None) -> datetime | None:
    """Read the three date formats these feeds use, always returning UTC.

    RFC 822 (RSS), ISO 8601 (Atom), and ISO with a trailing ``Z``, which
    ``fromisoformat`` rejected before Python 3.11 and which several feeds still
    emit. A naive timestamp is assumed UTC: guessing a local zone would shift
    an announcement by hours and silently corrupt the news-to-price alignment
    the backtester depends on.
    """
    if not value:
        return None
    text = value.strip()

    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        logger.debug("Unparseable feed date %r", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class ParseReport:
    """Why a feed produced the entries it did.

    A bare entry count cannot distinguish "the channel is empty right now"
    from "every item was dropped because its date format is unfamiliar", and
    those need opposite responses — wait, versus fix the parser.
    """

    items_seen: int = 0
    kept: int = 0
    root_tag: str | None = None
    dropped_no_title: int = 0
    dropped_no_link: int = 0
    dropped_no_date: int = 0
    parse_error: str | None = None

    def summary(self) -> str:
        if self.parse_error:
            return f"XML did not parse: {self.parse_error}"
        if not self.items_seen:
            if self.root_tag and self.root_tag not in ("rss", "feed", "RDF"):
                # An error page is usually well-formed XML, so "no items" alone
                # reads as a quiet feed when it is really the wrong document.
                return (
                    f"Parsed as XML, but the root element is <{self.root_tag}>, "
                    "which is not a feed — this looks like an error page."
                )
            return "Parsed, but the feed contains no items right now."
        parts = [f"{self.items_seen} items, {self.kept} usable"]
        for label, count in (
            ("no title", self.dropped_no_title),
            ("no link", self.dropped_no_link),
            ("unreadable date", self.dropped_no_date),
        ):
            if count:
                parts.append(f"{count} dropped for {label}")
        return "; ".join(parts)


def parse_feed_with_report(xml: str) -> tuple[list[FeedEntry], ParseReport]:
    """Parse a feed and report what was dropped and why."""
    report = ParseReport()
    try:
        # A leading BOM or stray whitespace before the declaration is common
        # and some parsers reject it, so it is trimmed rather than trusted.
        root = ElementTree.fromstring(xml.lstrip("\ufeff \t\r\n"))
    except ElementTree.ParseError as exc:
        logger.warning("Feed is not well-formed XML: %s", exc)
        report.parse_error = str(exc)
        return [], report

    report.root_tag = _local(root.tag)

    entries: list[FeedEntry] = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue
        report.items_seen += 1

        title = _text(element, "title")
        if not title:
            report.dropped_no_title += 1
            continue

        link = _link(element)
        if not link:
            report.dropped_no_link += 1
            continue

        published = parse_datetime(
            _text(element, "pubDate", "published", "updated", "date", "created")
        )
        if published is None:
            # An item with no usable timestamp cannot be aligned to a price
            # move, so it is worth less than the noise it adds.
            report.dropped_no_date += 1
            continue

        entries.append(
            FeedEntry(
                title=" ".join(title.split()),
                link=link,
                published_at=published,
                summary=_text(element, "description", "summary", "content"),
                identifier=_text(element, "guid", "id", "accession-number"),
            )
        )

    entries.sort(key=lambda entry: entry.published_at, reverse=True)
    report.kept = len(entries)
    return entries, report


def parse_feed(xml: str) -> list[FeedEntry]:
    """Read an RSS, RDF or Atom document into entries, newest first.

    A malformed document yields an empty list rather than raising: one broken
    feed must not take down an ingest cycle covering five others.
    """
    return parse_feed_with_report(xml)[0]


async def fetch_feed(
    client: httpx.AsyncClient,
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    params: dict | None = None,
    timeout: float = 30.0,
) -> list[FeedEntry]:
    """Fetch and parse one feed. Never raises; logs and returns [] on failure."""
    try:
        response = await client.get(
            url,
            params=params,
            headers={"User-Agent": user_agent, "Accept": "application/xml, text/xml, */*"},
            timeout=timeout,
            # httpx does not follow redirects by default. Several of these
            # publishers answer 3xx for their canonical feed URL — the FDA
            # newsroom serves a 302 to an interstitial — so without this the
            # feed reads as "reachable but empty" forever.
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        # Some httpx errors stringify to "", which makes a log line say
        # "Feed request failed for X: " and name no cause at all.
        logger.warning(
            "Feed request failed for %s: %s: %s", url, type(exc).__name__, exc or "no detail"
        )
        return []

    if response.status_code != 200:
        logger.warning("Feed %s returned HTTP %s", url, response.status_code)
        return []

    return parse_feed(response.text)


def strip_html(text: str | None, limit: int = 2000) -> str | None:
    """Flatten feed HTML to plain text for the sentiment analyser.

    Summaries arrive as escaped HTML more often than not, and the lexicon
    scores on words: an unstripped ``<a href=…>`` contributes tag soup to the
    token stream and nothing to the signal.
    """
    if not text:
        return None
    without_tags = re.sub(r"<[^>]+>", " ", text)
    collapsed = " ".join(without_tags.split())
    return collapsed[:limit] or None
