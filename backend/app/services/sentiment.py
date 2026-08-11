"""Financial sentiment scoring and event classification.

Two backends share one interface:

* ``lexicon`` — a finance-tuned keyword scorer. No downloads, no GPU, runs in
  microseconds. This is the default so a fresh clone works offline.
* ``finbert`` — ProsusAI/finbert via ``transformers``. Install
  ``requirements-ml.txt`` and set ``SENTIMENT_BACKEND=finbert``. The model is
  loaded once and cached for the process lifetime.

Event classification is rule-based in both cases: the taxonomy is small,
domain-specific, and far more predictable from curated patterns than from a
general-purpose classifier.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache

from app.config import get_settings
from app.models import EventType, Sentiment

logger = logging.getLogger(__name__)

# Truncated to the 512-token limit FinBERT was trained on. Characters, not
# tokens, because we truncate before the tokenizer sees the text; ~4 chars per
# token leaves comfortable headroom.
MAX_INPUT_CHARS = 1800


@dataclass(frozen=True)
class SentimentResult:
    """Scored sentiment for one article."""

    sentiment: Sentiment
    score: float
    confidence: float
    model_version: str


@dataclass(frozen=True)
class EventResult:
    """Classified business event for one headline."""

    primary_event: EventType
    confidence: float
    all_events: dict[EventType, float]


# Weights are hand-tuned on pharma/biotech headlines: regulatory and trial
# outcomes move these stocks far more than generic business language.
#
# Keys are regex fragments, matched with a leading word boundary. Write stems as
# ``approv\w*`` to catch approve/approves/approved/approval in one entry. The
# boundary matters: plain substring search made "sub[miss]ion" and
# "com[miss]ion" — two of the most common phrases in positive regulatory news —
# score as the negative term "miss".
_POSITIVE_TERMS: dict[str, float] = {
    # --- Regulatory outcomes: the biggest movers in this sector -------------
    r"approv\w*": 1.0,
    # Inflections only — bare "clear" is too common in ordinary prose
    # ("it is clear that", "unclear") to carry a regulatory signal.
    r"clear(?:s|ed|ance)\b": 0.9,
    r"authoris\w*": 0.85,
    r"authoriz\w*": 0.85,
    r"breakthrough therapy": 1.0,
    r"breakthrough": 0.85,
    r"accelerated approval": 1.0,
    r"priority review": 0.9,
    r"fast track": 0.85,
    r"orphan drug": 0.8,
    r"rare pediatric disease": 0.8,
    r"designation": 0.7,
    r"positive opinion": 1.0,
    r"recommend\w* (?:for )?approval": 1.0,
    r"(?:submission|filing|application) accepted": 0.95,
    r"accepted for (?:review|filing)": 0.95,
    r"label expansion": 0.9,
    r"expanded (?:label|indication)": 0.9,
    r"grant(?:s|ed)": 0.7,
    r"receiv\w* (?:approval|clearance|authoris|authoriz)": 1.0,
    r"ce mark": 0.7,
    r"exclusivity": 0.7,
    r"patent (?:upheld|granted|win|victory)": 0.9,
    # --- Clinical readouts --------------------------------------------------
    r"met (?:its |the |all |both )*(?:co-)?primary": 1.0,
    r"(?:co-)?primary endpoints? (?:was |were |been )?met": 1.0,
    r"achiev\w* (?:its |the )?primary": 1.0,
    r"statistically significant": 0.9,
    r"clinically meaningful": 0.85,
    r"well[- ]tolerated": 0.8,
    r"favou?rable safety": 0.85,
    r"durable (?:response|remission|benefit)": 0.8,
    r"positive (?:topline|data|results?|interim|readout)": 0.95,
    r"survival benefit": 0.9,
    r"superior(?:ity)? to": 0.85,
    r"first patient dosed": 0.6,
    r"(?:enrolment|enrollment) complete": 0.6,
    # --- Commercial and financial ------------------------------------------
    r"beat(?:s|ing)?\b": 0.85,
    r"exceed\w*": 0.8,
    r"surpass\w*": 0.8,
    r"rais(?:es|ed) (?:full[- ]year )?(?:guidance|outlook|forecast)": 1.0,
    r"lift(?:s|ed) (?:guidance|outlook)": 1.0,
    r"record (?:revenue|sales|quarter|profit)": 0.85,
    r"record": 0.5,
    r"growth": 0.55,
    r"profit\w*": 0.6,
    r"upgrad\w*": 0.9,
    r"outperform\w*": 0.8,
    r"(?:share )?(?:buyback|repurchase)": 0.8,
    r"(?:rais\w*|increas\w*) (?:the )?dividend": 0.8,
    r"reimbursement": 0.7,
    r"strong (?:demand|results?|quarter|sales|growth)": 0.8,
    r"robust": 0.6,
    r"(?:milestone|upfront) payment": 0.75,
    r"royalt\w*": 0.5,
    r"licens\w*": 0.55,
    r"partnership": 0.6,
    r"collaborat\w*": 0.55,
    r"acquisition": 0.5,
    r"expansion": 0.6,
    r"award\w*": 0.7,
    r"win(?:s|ning)?\b": 0.7,
    r"success\w*": 0.8,
    r"secur(?:es|ed)": 0.6,
    r"surge\w*": 0.7,
    r"rall(?:y|ies)": 0.6,
}

_NEGATIVE_TERMS: dict[str, float] = {
    # --- Regulatory setbacks ------------------------------------------------
    r"reject\w*": 1.0,
    r"deni(?:es|ed|al)": 0.9,
    r"complete response letter": 1.0,
    r"warning letter": 1.0,
    r"form 483": 0.9,
    r"consent decree": 0.9,
    r"import alert": 0.85,
    r"clinical hold": 1.0,
    r"recall\w*": 1.0,
    r"withdraw\w*": 0.8,
    r"negative opinion": 1.0,
    # --- Clinical failures --------------------------------------------------
    r"did not meet": 1.0,
    r"fail(?:s|ed|ure)?\b": 0.95,
    r"miss(?:es|ed)?\b": 0.8,
    r"discontinu\w*": 0.8,
    r"terminat\w*": 0.7,
    r"halt(?:s|ed)?\b": 0.8,
    r"safety (?:concern|signal|issue)": 0.9,
    r"setback": 0.85,
    r"disappointing": 0.8,
    # --- Commercial and financial ------------------------------------------
    r"declin(?:e|es|ed|ing)": 0.6,
    r"downgrad\w*": 0.9,
    r"underperform\w*": 0.8,
    r"loss(?:es)?\b": 0.6,
    r"cut(?:s)? (?:guidance|outlook|forecast)": 1.0,
    r"low(?:ers|ered) (?:guidance|outlook|forecast)": 1.0,
    # Same events with the noun first: "guidance cut", "outlook lowered".
    r"(?:guidance|outlook|forecast) (?:was |were )?(?:cut|lowered|reduced|trimmed)": 1.0,
    # A negator governs what FOLLOWS it, so the window-based check cannot catch
    # "approval was not granted" — the positive noun sits before the negator.
    # These spell out the common regulatory refusals directly.
    r"not (?:be |been )?(?:grant|approv|clear|authoris|authoriz)\w*": 1.0,
    r"declin(?:es|ed) to (?:approve|grant|clear)": 1.0,
    r"lawsuit": 0.7,
    r"litigation": 0.6,
    r"investigation": 0.7,
    r"probe": 0.6,
    r"layoffs?": 0.7,
    r"restructuring": 0.5,
    r"bankruptcy": 1.0,
    r"going concern": 0.9,
    r"delist\w*": 0.9,
    r"dilution": 0.6,
    r"delay\w*": 0.7,
    r"shortage": 0.6,
    r"plunge\w*": 0.8,
    r"slump\w*": 0.7,
    r"resign\w*": 0.6,
    r"steps down": 0.6,
}


# --- Sector overlays ---------------------------------------------------------
# The lexicon above was tuned entirely on pharma and biotech, which was right
# when the universe was pharma and biotech. It is silent on the vocabulary of
# semiconductors, memory and AI infrastructure: "design win", "hyperscaler",
# "backlog", "export controls" and "tape-out" carry no weight at all, so those
# symbols scored a flat 0.0 and their sentiment percentiles were ties.
#
# Worse than silent in one place. A *shortage* is bad news for a drugmaker that
# cannot supply its product, and good news for a memory maker, because scarcity
# is what lets it raise prices. A single global weight cannot be right for both,
# so the sign is decided per sector rather than argued about globally.


@dataclass(frozen=True)
class _Overlay:
    """Sector-specific additions to, and corrections of, the base lexicon."""

    positive: dict[str, float]
    negative: dict[str, float]
    # Base terms whose polarity is simply wrong for this sector. Suppressed
    # before the overlay's own terms are added, so an entry can be moved from
    # one side to the other by listing it in both places.
    suppress_positive: frozenset[str] = frozenset()
    suppress_negative: frozenset[str] = frozenset()


_SEMICONDUCTOR_TERMS_POSITIVE: dict[str, float] = {
    # --- Demand and pricing -------------------------------------------------
    r"design win": 0.9,
    r"(?:record|strong) (?:bookings|backlog|orders)": 0.9,
    r"backlog": 0.55,
    r"bookings": 0.55,
    r"sold out": 0.9,
    r"price (?:increase|hike|rise)": 0.8,
    r"pricing power": 0.85,
    r"(?:raises?|raised|hikes?) prices": 0.8,
    r"supercycle": 0.9,
    r"(?:demand|orders) (?:surge|soar|jump)\w*": 0.85,
    r"capacity expansion": 0.7,
    r"(?:qualifies|qualified|qualification) (?:for|with)": 0.7,
    r"(?:volume|mass) production": 0.75,
    r"yield improvement": 0.8,
    r"tape[- ]out": 0.6,
    r"(?:hyperscaler|data cent(?:er|re)) (?:demand|capex|spending)": 0.8,
    r"ai (?:demand|capex|spending|buildout)": 0.8,
    r"(?:hbm|dram|nand|ssd|flash)\b": 0.15,
    r"next[- ]gen(?:eration)?": 0.5,
    r"(?:node|process) (?:ramp|shrink)": 0.6,
    r"foundry (?:win|deal|agreement)": 0.85,
    r"(?:multi[- ]year|long[- ]term) (?:supply|purchase) agreement": 0.85,
    r"government (?:backing|support|subsid\w*|funding)": 0.7,
    r"chips act": 0.6,
}

_SEMICONDUCTOR_TERMS_NEGATIVE: dict[str, float] = {
    # --- Cycle and policy risk ----------------------------------------------
    r"(?:inventory|channel) (?:glut|correction|build)": 0.9,
    r"oversupply": 0.95,
    r"(?:price|asp) (?:decline|erosion|drop|collapse)": 0.9,
    r"(?:demand|order) (?:weakness|slowdown|softness)": 0.85,
    r"(?:cuts?|reduc\w*|trims?) (?:capex|capital spending|production|output)": 0.85,
    r"export (?:controls?|restrictions?|ban)": 0.9,
    r"entity list": 0.9,
    r"sanction\w*": 0.8,
    r"tariff\w*": 0.7,
    r"yield (?:issues?|problems?)": 0.85,
    r"(?:fab|plant|production) (?:halt|outage|fire|shutdown)": 0.9,
    r"(?:loses?|lost) (?:a )?(?:key |major )?(?:customer|order|contract)": 0.9,
    r"(?:share|market share) loss": 0.8,
    r"obsole\w*": 0.7,
    r"write[- ]down": 0.8,
}

# For a maker of a scarce component, scarcity is pricing power. These are the
# base lexicon's negatives read the other way round.
_SCARCITY_AS_STRENGTH: dict[str, float] = {
    r"shortage": 0.8,
    r"crunch": 0.7,
    r"(?:tight|tightening) supply": 0.8,
    r"supply constrain\w*": 0.7,
    r"allocation": 0.6,
    r"undersupply": 0.85,
}

_SUPPLY_SIDE_OVERLAY = _Overlay(
    positive={
        **_SEMICONDUCTOR_TERMS_POSITIVE,
        **_SCARCITY_AS_STRENGTH,
    },
    negative=_SEMICONDUCTOR_TERMS_NEGATIVE,
    # "shortage" ships as a negative for the pharma case and must not fire on
    # both sides at once, which would net to zero and look like no opinion.
    suppress_negative=frozenset({r"shortage"}),
)

# AI names are largely the same supply chain read from the demand side; the
# overlay is shared rather than duplicated, and diverges when evidence says to.
_OVERLAYS: dict[str, _Overlay] = {
    "data_storage": _SUPPLY_SIDE_OVERLAY,
    "ai": _SUPPLY_SIDE_OVERLAY,
}


def _compile(terms: dict[str, float]) -> tuple[tuple[re.Pattern[str], float], ...]:
    """Compile each term with a leading word boundary, longest phrase first.

    Ordering matters for reporting only — every pattern is evaluated — but it
    keeps the more specific phrase first when a human reads a match trace.
    """
    return tuple(
        (re.compile(r"\b" + fragment, re.IGNORECASE), weight)
        for fragment, weight in sorted(terms.items(), key=lambda kv: -len(kv[0]))
    )


_POSITIVE_PATTERNS = _compile(_POSITIVE_TERMS)
_NEGATIVE_PATTERNS = _compile(_NEGATIVE_TERMS)

_Patterns = tuple[tuple[re.Pattern[str], float], ...]


@lru_cache(maxsize=16)
def _patterns_for(sector_group: str | None) -> tuple[_Patterns, _Patterns]:
    """The positive and negative pattern sets that apply to one sector group.

    Cached because compiling ~200 patterns per article would dwarf the cost of
    matching them. An unknown or missing group gets the base lexicon, so a
    symbol whose sector is unmapped is scored exactly as it was before.
    """
    overlay = _OVERLAYS.get(sector_group or "")
    if overlay is None:
        return _POSITIVE_PATTERNS, _NEGATIVE_PATTERNS

    positive = {
        term: weight
        for term, weight in _POSITIVE_TERMS.items()
        if term not in overlay.suppress_positive
    }
    negative = {
        term: weight
        for term, weight in _NEGATIVE_TERMS.items()
        if term not in overlay.suppress_negative
    }
    positive.update(overlay.positive)
    negative.update(overlay.negative)
    return _compile(positive), _compile(negative)

# Negators flip the polarity of the term that follows them. Word-bounded: a bare
# "not" substring also lives inside "another", "notable" and "nothing", which
# silently inverted perfectly good news.
_NEGATOR_RE = re.compile(
    r"\b(?:not|no|never|without|cannot|denie[sd]|"
    r"fail(?:s|ed)? to|unable to|declin(?:es|ed) to)\b",
    re.IGNORECASE,
)

# Negation does not reach across a clause boundary: in "did not meet the
# endpoint, but approval was granted" the negator belongs to the first clause.
_CLAUSE_BREAKS = (";", ",", " but ", " however", " although ", " while ", " despite ")

# How far back a negator may sit, in characters.
_NEGATION_WINDOW = 40

# One term repeated many times should corroborate, not dominate.
_MAX_OCCURRENCES_PER_TERM = 3

_EVENT_PATTERNS: dict[EventType, tuple[str, ...]] = {
    EventType.FDA_APPROVAL: (
        r"\bfda\b", r"\bema\b", r"\bmhra\b", r"\bapprov", r"\bclearance\b",
        r"\bcomplete response letter\b", r"\bpdufa\b", r"\bmarketing authorisation\b",
        r"\bmarketing authorization\b", r"\b510\(k\)\b", r"\bce mark\b",
        # Regulatory milestones that precede an approval decision: a filing
        # acceptance or a special designation is a catalyst in its own right.
        r"\bregulatory submission\b", r"\b(?:submission|application|filing) accepted\b",
        r"\baccepted for (?:review|filing)\b", r"\bmarketing application\b",
        r"\b(?:bla|nda|snda|sbla|maa|anda)\b", r"\bdesignation\b",
        r"\bbreakthrough therapy\b", r"\bpriority review\b", r"\bfast track\b",
        r"\borphan drug\b", r"\bchmp\b", r"\b(?:positive|negative) opinion\b",
        r"\blabel expansion\b", r"\bregulatory\b",
    ),
    EventType.CLINICAL_TRIAL: (
        r"\bphase (?:1|2|3|i{1,3})\b", r"\btrial\b", r"\bstudy\b", r"\bendpoint\b",
        r"\btopline\b", r"\benrolment\b", r"\benrollment\b", r"\bcohort\b",
    ),
    EventType.REVENUE: (
        r"\bearnings\b", r"\brevenue\b", r"\bguidance\b", r"\bquarterly results\b",
        r"\bq[1-4] (?:20\d\d|results)\b", r"\beps\b", r"\bfull[- ]year results\b",
        # SEC 8-K item 2.02 and the periodic reports arrive with this wording.
        r"\bresults of operations\b", r"\bfinancial condition\b",
        r"\bannual report\b", r"\bquarterly report\b", r"\boutlook\b",
        # Shareholder returns are financial events, not regulatory ones — a
        # board "approving" a buyback otherwise matched the FDA pattern.
        r"\b(?:share )?(?:buyback|repurchase)\b", r"\bdividend\b",
    ),
    EventType.MERGER_ACQUISITION: (
        r"\bacquis", r"\bacquire", r"\bmerger\b", r"\bmerges\b", r"\btakeover\b",
        r"\bbuyout\b", r"\bdivest", r"\bto acquire\b",
    ),
    EventType.LITIGATION: (
        r"\blawsuit\b", r"\blitigation\b", r"\bpatent (?:dispute|infringement|win)\b",
        r"\bcourt\b", r"\bsettlement\b", r"\bappeal\b",
    ),
    EventType.RECALL: (
        r"\brecall", r"\bwarning letter\b", r"\bform 483\b", r"\bconsent decree\b",
        r"\bimport alert\b", r"\bcontaminat",
    ),
    EventType.PARTNERSHIP: (
        r"\bpartnership\b", r"\bcollaborat", r"\blicens", r"\bjoint venture\b",
        r"\bsupply agreement\b", r"\bcdmo (?:deal|agreement|contract)\b",
        # SEC 8-K item 1.01 — how most deals are first disclosed.
        r"\bmaterial definitive agreement\b", r"\bupfront payment\b",
        r"\bmilestone payment\b",
    ),
    EventType.EXEC_CHANGE: (
        r"\bceo\b", r"\bcfo\b", r"\bcso\b", r"\bchief executive\b", r"\bappoints\b",
        r"\bsteps down\b", r"\bresigns\b", r"\bnames? new\b",
        # SEC 8-K item 5.02
        r"\bdeparture or election of directors\b", r"\bprincipal officers\b",
    ),
    EventType.FACILITY: (
        r"\bfacility\b", r"\bplant\b", r"\bmanufacturing site\b", r"\bcapacity expansion\b",
        r"\bgroundbreaking\b", r"\bgmp\b", r"\binspection\b",
    ),
    EventType.ANALYST_RATING: (
        r"\bupgrade", r"\bdowngrade", r"\bprice target\b", r"\breiterates\b",
        r"\binitiates coverage\b", r"\boutperform\b", r"\bunderperform\b",
    ),
    EventType.CAPITAL_RAISE: (
        r"\boffering\b", r"\bipo\b", r"\bseries [a-e]\b", r"\bfunding round\b",
        r"\braises \$", r"\bconvertible notes\b", r"\bprivate placement\b",
        # SEC forms S-1/424B4 and 8-K item 3.02
        r"\bregistration statement\b", r"\bprospectus\b",
        r"\bunregistered sales of equity\b",
    ),
}


def _truncate(text: str) -> str:
    return text[:MAX_INPUT_CHARS]


def _prepare(headline: str, body: str | None) -> str:
    """Join headline and body into the single string a backend scores."""
    parts = [headline.strip()]
    if body:
        parts.append(body.strip())
    return _truncate(" ".join(p for p in parts if p))


def _is_negated(text: str, match_start: int) -> bool:
    """True when a negator governs the term starting at ``match_start``.

    The lookback stops at the nearest clause boundary, so a negator belonging to
    an earlier clause cannot invert a later one.
    """
    window = text[max(0, match_start - _NEGATION_WINDOW) : match_start]
    for separator in _CLAUSE_BREAKS:
        index = window.rfind(separator)
        if index != -1:
            window = window[index + len(separator) :]
    return _NEGATOR_RE.search(window) is not None


class LexiconAnalyzer:
    """Keyword scorer tuned for pharma and life-sciences headlines."""

    model_version = "lexicon-v3"

    @staticmethod
    def _tally(text: str, sector_group: str | None = None) -> tuple[float, float, int]:
        """Sum positive and negative weight over every occurrence in ``text``.

        Each occurrence is negation-checked on its own, so "did not meet the
        primary endpoint, but the label expansion was approved" books the first
        clause negative and the second positive.
        """
        positive = negative = 0.0
        hits = 0
        positive_patterns, negative_patterns = _patterns_for(sector_group)

        for patterns, is_positive in (
            (positive_patterns, True),
            (negative_patterns, False),
        ):
            for pattern, weight in patterns:
                occurrences = 0
                for match in pattern.finditer(text):
                    occurrences += 1
                    if occurrences > _MAX_OCCURRENCES_PER_TERM:
                        break
                    hits += 1
                    # A negated positive reads as negative, and vice versa.
                    if is_positive != _is_negated(text, match.start()):
                        positive += weight
                    else:
                        negative += weight

        return positive, negative, hits

    def explain(
        self, headline: str, body: str | None = None, sector_group: str | None = None
    ) -> dict:
        """Which terms fired and how — for tuning the lexicon against real news."""
        text = _prepare(headline, body).lower()
        matched: list[dict] = []
        positive_patterns, negative_patterns = _patterns_for(sector_group)
        for patterns, is_positive in (
            (positive_patterns, True),
            (negative_patterns, False),
        ):
            for pattern, weight in patterns:
                for match in pattern.finditer(text):
                    negated = _is_negated(text, match.start())
                    matched.append(
                        {
                            "term": pattern.pattern.lstrip("\\b"),
                            "matched_text": match.group(0),
                            "weight": weight,
                            "polarity": "positive" if is_positive != negated else "negative",
                            "negated": negated,
                        }
                    )
        result = self.score(headline, body, sector_group)
        return {
            "sentiment": result.sentiment.value,
            "score": result.score,
            "confidence": result.confidence,
            "sector_group": sector_group,
            "matches": matched,
        }

    def score(
        self, headline: str, body: str | None = None, sector_group: str | None = None
    ) -> SentimentResult:
        text = _prepare(headline, body).lower()
        if not text:
            return SentimentResult(Sentiment.NEUTRAL, 0.0, 0.0, self.model_version)

        positive, negative, hits = self._tally(text, sector_group)
        total = positive + negative
        if total == 0:
            return SentimentResult(Sentiment.NEUTRAL, 0.0, 0.25, self.model_version)

        # score runs -1..1; confidence grows with the number of corroborating hits.
        score = (positive - negative) / total
        confidence = min(1.0, 0.4 + 0.15 * hits)

        if score > 0.15:
            sentiment = Sentiment.POSITIVE
        elif score < -0.15:
            sentiment = Sentiment.NEGATIVE
        else:
            sentiment = Sentiment.NEUTRAL
        return SentimentResult(sentiment, round(score, 4), round(confidence, 4), self.model_version)


class FinBertAnalyzer:
    """ProsusAI/finbert wrapper. The pipeline is built once, on first use."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.model_version = f"finbert:{model_name}"
        self._pipeline = None

    def _get_pipeline(self):
        if self._pipeline is None:
            from transformers import pipeline  # imported lazily: heavy dependency

            logger.info("Loading FinBERT model %s (first call only)", self.model_name)
            self._pipeline = pipeline(
                "sentiment-analysis", model=self.model_name, truncation=True, max_length=512
            )
        return self._pipeline

    def score(
        self, headline: str, body: str | None = None, sector_group: str | None = None
    ) -> SentimentResult:
        # FinBERT learned its own vocabulary from a general financial corpus and
        # has no sector switch to set, so the argument is accepted for interface
        # parity and deliberately unused rather than faked.
        text = _prepare(headline, body)
        if not text:
            return SentimentResult(Sentiment.NEUTRAL, 0.0, 0.0, self.model_version)

        started = time.perf_counter()
        raw = self._get_pipeline()(text)[0]
        logger.debug("FinBERT inference took %.1f ms", (time.perf_counter() - started) * 1000)

        label = str(raw["label"]).lower()
        confidence = float(raw["score"])
        sentiment = {
            "positive": Sentiment.POSITIVE,
            "negative": Sentiment.NEGATIVE,
        }.get(label, Sentiment.NEUTRAL)

        sign = {Sentiment.POSITIVE: 1.0, Sentiment.NEGATIVE: -1.0}.get(sentiment, 0.0)
        return SentimentResult(
            sentiment, round(sign * confidence, 4), round(confidence, 4), self.model_version
        )


class SentimentAnalyzer:
    """Public entry point: sentiment plus event classification."""

    def __init__(self, backend: str | None = None, finbert_model: str | None = None) -> None:
        settings = get_settings()
        backend = (backend or settings.sentiment_backend).lower()
        model_name = finbert_model or settings.finbert_model

        if backend == "finbert":
            self._backend: LexiconAnalyzer | FinBertAnalyzer = FinBertAnalyzer(model_name)
        else:
            if backend != "lexicon":
                logger.warning("Unknown SENTIMENT_BACKEND %r; using lexicon", backend)
            self._backend = LexiconAnalyzer()

    @property
    def model_version(self) -> str:
        return self._backend.model_version

    def analyze_sentiment(
        self, headline: str, body: str | None = None, sector_group: str | None = None
    ) -> SentimentResult:
        """Score one article. Never raises: a backend failure degrades to neutral.

        ``sector_group`` selects the lexicon overlay — see ``_OVERLAYS``. Omit it
        and the base pharma lexicon applies, which is the correct default for an
        unmapped symbol and the historical behaviour for every other one.
        """
        try:
            return self._backend.score(headline, body, sector_group)
        except Exception:  # pragma: no cover - defensive, keeps ingestion alive
            logger.exception("Sentiment scoring failed; recording neutral")
            return SentimentResult(Sentiment.NEUTRAL, 0.0, 0.0, f"{self.model_version}+error")

    def classify_event_type(self, headline: str, body: str | None = None) -> EventResult:
        """Map a headline onto the event taxonomy by pattern match density."""
        text = _prepare(headline, body).lower()
        if not text:
            return EventResult(EventType.OTHER, 0.0, {})

        scores: dict[EventType, float] = {}
        for event, patterns in _EVENT_PATTERNS.items():
            matches = sum(1 for pattern in patterns if re.search(pattern, text))
            if matches:
                scores[event] = matches / len(patterns)

        if not scores:
            return EventResult(EventType.OTHER, 0.0, {})

        primary = max(scores, key=lambda event: scores[event])
        total = sum(scores.values())
        confidence = round(scores[primary] / total, 4) if total else 0.0
        return EventResult(primary, confidence, {k: round(v, 4) for k, v in scores.items()})


@lru_cache
def get_analyzer() -> SentimentAnalyzer:
    """Return the process-wide analyzer (models are expensive to build)."""
    return SentimentAnalyzer()
