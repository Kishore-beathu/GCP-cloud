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
    for child in element:
        if _local(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            # Atom puts the URL in an attribute rather than in the text.
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return None


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


def parse_feed(xml: str) -> list[FeedEntry]:
    """Read an RSS, RDF or Atom document into entries, newest first.

    A malformed document yields an empty list rather than raising: one broken
    feed must not take down an ingest cycle covering five others.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        logger.warning("Feed is not well-formed XML: %s", exc)
        return []

    entries: list[FeedEntry] = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue

        title = _text(element, "title")
        link = _text(element, "link", "guid", "id")
        if not title or not link:
            continue

        published = parse_datetime(
            _text(element, "pubDate", "published", "updated", "date")
        )
        if published is None:
            # An item with no usable timestamp cannot be aligned to a price
            # move, so it is worth less than the noise it adds.
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
    return entries


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
