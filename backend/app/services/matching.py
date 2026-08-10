"""Attach tracked tickers to free-text headlines.

Vendor APIs hand over a symbol. Regulators and newswires hand over prose:
"Pfizer Inc. announces…", "FDA approves Merck's KEYTRUDA for…". To score those
against a watchlist the company has to be recognised in the text.

The failure that matters is a **false positive**, not a miss. A wrongly matched
headline attaches sentiment to a company the story is not about, fires an alert,
and enters the backtest as evidence — so this errs toward missing a story.
Matching is therefore anchored on word boundaries, requires the legal-suffix
noise to be stripped first, and refuses to match on names too short or too
generic to be evidence of anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stock

# Corporate-form suffixes carry no identifying information: "Pfizer Inc." and
# "Pfizer" are the same company, and leaving them in means a headline that
# writes the name without one never matches.
_SUFFIXES = re.compile(
    r"\b("
    r"inc|inc\.|incorporated|corp|corp\.|corporation|co|co\.|company|"
    r"ltd|ltd\.|limited|plc|llc|lp|nv|n\.v\.|sa|s\.a\.|se|ag|a/s|as|asa|ab|oyj|"
    r"holdings?|group|technologies|technology|pharmaceuticals?|pharma|"
    r"therapeutics|biosciences|bioscience|laboratories|labs|sciences|"
    r"international|worldwide|adr"
    # A trailing \b cannot follow a full stop — "." is not a word character, so
    # \b after "S.A." never holds and the suffix survives. A negative lookahead
    # for a word character does hold, and still refuses to split "Incyte".
    r")(?![\w])\.?",
    re.IGNORECASE,
)

# Removing a suffix can leave a dangling connector: "Merck & Co. Inc." reduces
# to "merck &", which then matches nothing, because a headline says "Merck".
_DANGLING = re.compile(r"(?:^|\s)[&-]+(?=\s|$)")

# Removing "Company" from "Eli Lilly and Company" leaves a trailing "and", so
# the index key becomes "eli lilly and" and a headline saying "Eli Lilly"
# matches nothing. Trailing connectors are dropped for the same reason.
_TRAILING_CONNECTORS = frozenset({"and", "of", "the", "&", "-"})

_PUNCTUATION = re.compile(r"[^\w\s&-]")

# A name this short is a word before it is a company: "AI", "Box", "Net", "Arm"
# all appear in ordinary prose. Those symbols are matched by ticker instead.
_MIN_NAME_LENGTH = 4

# Names that are ordinary English regardless of length. Each of these is a real
# company in the universe, and each would otherwise match constantly.
_TOO_GENERIC = frozenset(
    {
        "box",
        "net",
        "arm",
        "now",
        "elastic",
        "oracle",
        "micron",
        "applied",
        "advanced",
        "united",
        "general",
        "national",
        "american",
        "digital",
        "quantum",
        "core",
    }
)


@dataclass(frozen=True)
class CompanyIndex:
    """Recognisable names and symbols for the tracked universe.

    The derived lookups exist for speed, and the reason is concrete: matching
    used to test every company name against every headline with its own regex,
    and re-sort the name list on each call. Across one EDGAR cycle — six forms
    of a hundred entries — that was over half a second of synchronous CPU work
    inside the event loop, every two minutes, growing with the universe. On a
    single-process server that is time the dashboard and the WebSocket are not
    being served.
    """

    # normalised company name -> every listing of that company. Dual-listed
    # names carry more than one: a story about Novo Nordisk is about the
    # company, so it belongs on NVO *and* NOVO-B.CO. Attaching it to only one
    # is how a home line ends up with no news while its ADR has plenty.
    names: dict[str, tuple[str, ...]]
    # upper-case ticker -> itself, for symbols quoted directly in a headline
    tickers: frozenset[str]

    def __post_init__(self) -> None:
        # First word of each name -> the names starting with it, longest first.
        # A headline is then tested only against names whose opening word it
        # actually contains, which is nearly always none of them.
        buckets: dict[str, list[str]] = {}
        for name in self.names:
            first = name.split(" ", 1)[0]
            buckets.setdefault(first, []).append(name)
        for names in buckets.values():
            names.sort(key=len, reverse=True)
        object.__setattr__(self, "_by_first_word", buckets)

    def candidates(self, words: set[str]) -> list[str]:
        """Names worth testing against a text containing these words."""
        buckets: dict[str, list[str]] = getattr(self, "_by_first_word", {})
        found: list[str] = []
        for word in words:
            found.extend(buckets.get(word, ()))
        # Longest first, so "Bristol-Myers Squibb" wins over a shorter prefix.
        found.sort(key=len, reverse=True)
        return found

    def __bool__(self) -> bool:
        return bool(self.names or self.tickers)


def normalise(name: str) -> str:
    """Reduce a company name to its identifying core, lower-cased.

    Suffixes are stripped **before** punctuation and again after. Several
    corporate forms carry their own punctuation — "A/S", "N.V.", "S.A." — and
    removing punctuation first turns them into bare letters that no longer
    match, leaving "novo nordisk a s" to be compared against "novo nordisk".
    The second pass then catches forms that only separate once punctuation is
    gone, such as a trailing "Co.,".
    """
    without_suffixes = _SUFFIXES.sub(" ", name)
    without_punctuation = _PUNCTUATION.sub(" ", without_suffixes)
    cleaned = _DANGLING.sub(" ", _SUFFIXES.sub(" ", without_punctuation))
    tokens = cleaned.lower().split()
    while tokens and tokens[-1] in _TRAILING_CONNECTORS:
        tokens.pop()
    return " ".join(tokens)


async def build_index(db: AsyncSession) -> CompanyIndex:
    """Build the name and symbol index from the tracked universe."""
    rows = (
        await db.execute(
            select(Stock.ticker, Stock.company_name).where(Stock.is_active.is_(True))
        )
    ).all()

    grouped: dict[str, list[str]] = {}
    tickers: set[str] = set()

    for ticker, company_name in rows:
        tickers.add(ticker.upper())
        if not company_name:
            continue
        key = normalise(company_name)
        if len(key) < _MIN_NAME_LENGTH or key in _TOO_GENERIC:
            continue
        grouped.setdefault(key, []).append(ticker.upper())

    # Sorted so the order a match returns does not depend on row order.
    names = {key: tuple(sorted(values)) for key, values in grouped.items()}
    return CompanyIndex(names=names, tickers=frozenset(tickers))


# An upper-case run that could be a symbol, including venue suffixes (AZN.L)
# and numeric listings (4502.T, 000660.KS). Anchored so it cannot start or end
# mid-word, which is what keeps "MU" out of "museum".
_SYMBOL_TOKEN = re.compile(r"(?<![\w.])([A-Z0-9]{1,7}(?:[.\-][A-Z0-9]{1,4})?)(?![\w.])")

# Symbols that are also ordinary words or abbreviations in upper-case prose.
# Every one of these is a real ticker in this universe or a common headline
# token, and matching them by symbol attaches stories to companies they are not
# about: "A" is Agilent, so a sentence beginning "A study of…" would score
# against Agilent; "AI" is C3.ai, and appears in half the technology headlines
# written. They remain matchable by company name, which is unambiguous.
_AMBIGUOUS_SYMBOLS = frozenset(
    {
        "A", "I", "AI", "IT", "ON", "AT", "BE", "SO", "OR", "AS", "BY", "IS",
        "TO", "DO", "GO", "NO", "UP", "IN", "OF", "AN", "US", "UK", "EU", "FDA",
        "EMA", "SEC", "CEO", "CFO", "USA", "AND", "THE", "NOT", "CAN", "HAS",
        "ITS", "NEW", "ONE", "ALL", "FOR", "ARE", "WAS", "PLC", "INC", "LTD",
        "NYSE", "IPO", "ETF", "GDP", "AGM", "R&D", "M&A", "Q1", "Q2", "Q3", "Q4",
    }
)


def match_tickers(text: str, index: CompanyIndex, limit: int = 3) -> list[str]:
    """Tickers this text is about, most specific first.

    ``limit`` caps *companies*, not symbols: a dual-listed name legitimately
    returns two rows for one company, and counting those against the cap would
    let a single story about Novo Nordisk exhaust it. A headline naming five
    distinct companies is a market round-up, and attributing its sentiment to
    all of them is how a feed poisons a watchlist.

    Longer names are tested first, so "Bristol-Myers Squibb" wins over a
    hypothetical "Bristol".
    """
    if not text or not index:
        return []

    found: list[str] = []
    companies = 0
    haystack = normalise(text)
    words = set(haystack.split())

    for name in index.candidates(words):
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", haystack):
            new = [t for t in index.names[name] if t not in found]
            if not new:
                continue
            found.extend(new)
            companies += 1
            if companies >= limit:
                return found

    # Symbols are matched against the original text: normalisation lower-cases,
    # and an upper-case symbol is most of what makes "MU" a ticker and not a
    # word. Candidate symbols are lifted out of the text in one pass and
    # intersected with the universe, rather than running one regex per tracked
    # symbol against every headline.
    for symbol in _SYMBOL_TOKEN.findall(text):
        if companies >= limit:
            break
        if symbol in found or symbol not in index.tickers:
            continue
        # A one-character symbol in prose is a word, an initial or a list
        # marker far more often than it is a ticker.
        if len(symbol) < 2 or symbol in _AMBIGUOUS_SYMBOLS:
            continue
        found.append(symbol)
        companies += 1

    return found
