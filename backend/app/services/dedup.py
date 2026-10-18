"""Recognise the same story arriving from several sources.

With one vendor, a URL was a sufficient identity. With SEC, FDA, Finnhub,
Yahoo, three newswires and the exchange halt feed, one press release commonly
lands four times within a minute under four URLs and four slightly different
headlines. Storing those as four independent articles would triple-count the
event in the backtester, fire the same alert repeatedly, and bury the feed.

The approach is deliberately conservative. Two items are the same story only
when they concern the same ticker, arrive close together in time, and share
almost all of their significant words. Merging two genuinely different stories
is far more damaging than failing to merge two copies: a missed merge shows a
duplicate, a wrong merge hides a real event.
"""

from __future__ import annotations

import re
from datetime import timedelta

# Wires prefix and suffix their own boilerplate around the same sentence.
_NOISE = re.compile(
    r"\b("
    r"press\s+release|exclusive|update\s*\d*|breaking|report|reports|says|said|"
    r"announces|announced|announcement|statement|shares|stock|stocks|inc|corp|"
    r"ltd|plc|nyse|nasdaq|the|a|an|of|for|to|in|on|at|by|with|and|its|it|is|are|"
    r"as|from|that|this|after|over"
    r")\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^\w\s]")

# Two copies of a release land within minutes; the same company issuing two
# genuinely different releases inside this window is rare, and the token test
# still has to pass.
DEFAULT_WINDOW = timedelta(hours=6)

# Share of significant words that must appear in both headlines. Set from
# looking at real wire copy: rewrites change the framing words and keep the
# nouns, so the overlap of what survives noise removal stays high.
DEFAULT_THRESHOLD = 0.75

# Below this many significant words a ratio is meaningless — two three-word
# headlines share two words by accident all the time.
_MIN_TOKENS = 4


def signature(headline: str) -> frozenset[str]:
    """The significant words of a headline, for overlap comparison."""
    cleaned = _NON_WORD.sub(" ", headline.lower())
    without_noise = _NOISE.sub(" ", cleaned)
    return frozenset(word for word in without_noise.split() if len(word) > 2)


def similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard overlap of two signatures, 0.0 to 1.0."""
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


# Outcome axes. Two headlines landing on opposite ends of any one of these are
# never the same story, however many words they share — "FDA approves X" and
# "FDA rejects X" differ by a single token out of eight, which sails past any
# overlap threshold loose enough to be useful. Merging those would hide a
# rejection behind an approval: the worst error this module can make.
#
# Negatives are tested first and suppress the positive on the same axis, so the
# "meet" inside "did not meet" cannot register as a hit.
_OUTCOME_AXES: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (
        re.compile(
            r"\b(?:approv\w+|clear(?:ed|ance)|authoris\w+|authoriz\w+|grant(?:s|ed)?)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:reject\w+|refus\w+|declin\w+|denie[sd]|denial|"
            r"not\s+approv\w+|complete\s+response\s+letter|crl)\b",
            re.IGNORECASE,
        ),
    ),
    (
        re.compile(
            r"\b(?:success\w*|succeed\w*|met|meets|achiev\w+|positive|beat[s]?|tops)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:fail\w*|miss(?:es|ed)?|negative|"
            r"(?:did|does|do)\s+not\s+(?:meet|achieve)|failed\s+to\s+meet|"
            r"discontinu\w+|terminat\w+|halt\w*|withdraw\w+|recall\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        re.compile(r"\b(?:rais\w+|lift\w+|upgrad\w+|increas\w+)\b", re.IGNORECASE),
        re.compile(r"\b(?:cut[s]?|lower\w+|downgrad\w+|reduc\w+|slash\w+)\b", re.IGNORECASE),
    ),
)


def _polarity(text: str) -> tuple[int, ...]:
    """Per axis: -1 negative, 1 positive, 0 absent."""
    signs = []
    for positive, negative in _OUTCOME_AXES:
        if negative.search(text):
            signs.append(-1)
        elif positive.search(text):
            signs.append(1)
        else:
            signs.append(0)
    return tuple(signs)


def conflicting_outcomes(headline_a: str, headline_b: str) -> bool:
    """Whether the two headlines report opposite outcomes on any axis."""
    return any(
        left * right < 0
        for left, right in zip(_polarity(headline_a), _polarity(headline_b))
    )


def is_duplicate(
    headline_a: str,
    headline_b: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> bool:
    """Whether two headlines are the same story told twice."""
    if conflicting_outcomes(headline_a, headline_b):
        return False

    left, right = signature(headline_a), signature(headline_b)
    if len(left) < _MIN_TOKENS or len(right) < _MIN_TOKENS:
        # Too little to judge on. Fall back to exact equality after
        # normalisation, which still catches a verbatim syndication.
        return left == right and bool(left)
    return similarity(left, right) >= threshold
