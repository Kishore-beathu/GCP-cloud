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
    r"international|worldwide"
    r")\b\.?",
    re.IGNORECASE,
)

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
    """Recognisable names and symbols for the tracked universe."""

    # normalised company name -> ticker
    names: dict[str, str]
    # upper-case ticker -> itself, for symbols quoted directly in a headline
    tickers: frozenset[str]

    def __bool__(self) -> bool:
        return bool(self.names or self.tickers)


def normalise(name: str) -> str:
    """Reduce a company name to its identifying core, lower-cased."""
    without_punctuation = _PUNCTUATION.sub(" ", name)
    without_suffixes = _SUFFIXES.sub(" ", without_punctuation)
    return " ".join(without_suffixes.split()).lower()


async def build_index(db: AsyncSession) -> CompanyIndex:
    """Build the name and symbol index from the tracked universe."""
    rows = (
        await db.execute(
            select(Stock.ticker, Stock.company_name).where(Stock.is_active.is_(True))
        )
    ).all()

    names: dict[str, str] = {}
    tickers: set[str] = set()

    for ticker, company_name in rows:
        tickers.add(ticker.upper())
        if not company_name:
            continue
        key = normalise(company_name)
        if len(key) < _MIN_NAME_LENGTH or key in _TOO_GENERIC:
            continue
        # First writer wins, so a US line and its home line do not fight over
        # the same name; ordering by ticker keeps that choice deterministic.
        names.setdefault(key, ticker.upper())

    return CompanyIndex(names=names, tickers=frozenset(tickers))


def _ticker_pattern(symbol: str) -> re.Pattern[str]:
    # Symbols carrying a venue suffix (AZN.L) need the dot escaped, and a
    # bare-word boundary would break on it.
    return re.compile(rf"(?<![\w.]){re.escape(symbol)}(?![\w.])")


def match_tickers(text: str, index: CompanyIndex, limit: int = 3) -> list[str]:
    """Tickers this text is about, most specific first.

    Longer names are tested first so "Bristol-Myers Squibb" wins over a
    hypothetical "Bristol", and the result is capped: a headline naming five
    companies is a market round-up, and attributing its sentiment to all of
    them is how a feed poisons a watchlist.
    """
    if not text or not index:
        return []

    found: list[str] = []
    haystack = normalise(text)

    for name in sorted(index.names, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", haystack):
            ticker = index.names[name]
            if ticker not in found:
                found.append(ticker)
                if len(found) >= limit:
                    return found

    # Symbols are matched against the original text: normalisation lower-cases,
    # and an upper-case symbol is most of what makes "MU" a ticker and not a word.
    for symbol in index.tickers:
        if len(found) >= limit:
            break
        if symbol in found:
            continue
        if _ticker_pattern(symbol).search(text):
            found.append(symbol)

    return found
