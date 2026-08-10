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
    """Recognisable names and symbols for the tracked universe."""

    # normalised company name -> every listing of that company. Dual-listed
    # names carry more than one: a story about Novo Nordisk is about the
    # company, so it belongs on NVO *and* NOVO-B.CO. Attaching it to only one
    # is how a home line ends up with no news while its ADR has plenty.
    names: dict[str, tuple[str, ...]]
    # upper-case ticker -> itself, for symbols quoted directly in a headline
    tickers: frozenset[str]

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


def _ticker_pattern(symbol: str) -> re.Pattern[str]:
    # Symbols carrying a venue suffix (AZN.L) need the dot escaped, and a
    # bare-word boundary would break on it.
    return re.compile(rf"(?<![\w.]){re.escape(symbol)}(?![\w.])")


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

    for name in sorted(index.names, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", haystack):
            new = [t for t in index.names[name] if t not in found]
            if not new:
                continue
            found.extend(new)
            companies += 1
            if companies >= limit:
                return found

    # Symbols are matched against the original text: normalisation lower-cases,
    # and an upper-case symbol is most of what makes "MU" a ticker and not a word.
    for symbol in sorted(index.tickers):
        if companies >= limit:
            break
        if symbol in found:
            continue
        if _ticker_pattern(symbol).search(text):
            found.append(symbol)
            companies += 1

    return found
