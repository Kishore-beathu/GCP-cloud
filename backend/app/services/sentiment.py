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
_POSITIVE_TERMS: dict[str, float] = {
    "approval": 1.0, "approves": 1.0, "approved": 1.0, "clearance": 0.9,
    "breakthrough": 0.9, "granted": 0.7, "authorisation": 0.8, "authorization": 0.8,
    "beats": 0.9, "beat": 0.8, "exceeds": 0.8, "surpassed": 0.8, "record": 0.6,
    "growth": 0.6, "profit": 0.6, "upgrade": 0.9, "upgraded": 0.9, "outperform": 0.8,
    "raises guidance": 1.0, "raised guidance": 1.0, "positive": 0.6, "success": 0.8,
    "successful": 0.8, "wins": 0.8, "win": 0.6, "awarded": 0.7, "expansion": 0.6,
    "partnership": 0.6, "collaboration": 0.5, "acquisition": 0.5, "milestone": 0.6,
    "orphan drug": 0.7, "fast track": 0.8, "priority review": 0.8, "met primary": 1.0,
    "statistically significant": 0.9, "surge": 0.7, "rally": 0.6, "strong demand": 0.7,
}

_NEGATIVE_TERMS: dict[str, float] = {
    "rejection": 1.0, "rejects": 1.0, "rejected": 1.0, "denied": 0.9,
    "complete response letter": 1.0, "warning letter": 1.0, "form 483": 0.9,
    "recall": 1.0, "recalled": 1.0, "withdraw": 0.8, "withdrawn": 0.8,
    "misses": 0.9, "missed": 0.8, "miss": 0.7, "decline": 0.6, "declines": 0.6,
    "downgrade": 0.9, "downgraded": 0.9, "underperform": 0.8, "loss": 0.6,
    "losses": 0.6, "cuts guidance": 1.0, "cut guidance": 1.0, "lawsuit": 0.7,
    "litigation": 0.6, "investigation": 0.7, "probe": 0.6, "failed": 1.0,
    "failure": 0.9, "halt": 0.8, "halted": 0.8, "discontinued": 0.8,
    "layoffs": 0.7, "restructuring": 0.5, "bankruptcy": 1.0, "delay": 0.7,
    "delayed": 0.7, "shortage": 0.6, "did not meet": 1.0, "plunge": 0.8,
    "slump": 0.7, "resigns": 0.6, "steps down": 0.6,
}

# Negators flip the polarity of the term that follows them within a short window.
_NEGATORS = ("not", "no", "never", "fails to", "failed to", "without", "denies")

_EVENT_PATTERNS: dict[EventType, tuple[str, ...]] = {
    EventType.FDA_APPROVAL: (
        r"\bfda\b", r"\bema\b", r"\bmhra\b", r"\bapprov", r"\bclearance\b",
        r"\bcomplete response letter\b", r"\bpdufa\b", r"\bmarketing authorisation\b",
        r"\bmarketing authorization\b", r"\b510\(k\)\b", r"\bce mark\b",
    ),
    EventType.CLINICAL_TRIAL: (
        r"\bphase (?:1|2|3|i{1,3})\b", r"\btrial\b", r"\bstudy\b", r"\bendpoint\b",
        r"\btopline\b", r"\benrolment\b", r"\benrollment\b", r"\bcohort\b",
    ),
    EventType.REVENUE: (
        r"\bearnings\b", r"\brevenue\b", r"\bguidance\b", r"\bquarterly results\b",
        r"\bq[1-4] (?:20\d\d|results)\b", r"\beps\b", r"\bfull[- ]year results\b",
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
    ),
    EventType.EXEC_CHANGE: (
        r"\bceo\b", r"\bcfo\b", r"\bcso\b", r"\bchief executive\b", r"\bappoints\b",
        r"\bsteps down\b", r"\bresigns\b", r"\bnames? new\b",
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
    """True when a negator appears in the ~30 characters before a matched term."""
    window = text[max(0, match_start - 30) : match_start]
    return any(negator in window for negator in _NEGATORS)


class LexiconAnalyzer:
    """Keyword scorer tuned for pharma and life-sciences headlines."""

    model_version = "lexicon-v1"

    def score(self, headline: str, body: str | None = None) -> SentimentResult:
        text = _prepare(headline, body).lower()
        if not text:
            return SentimentResult(Sentiment.NEUTRAL, 0.0, 0.0, self.model_version)

        positive = negative = 0.0
        hits = 0
        for terms, is_positive in ((_POSITIVE_TERMS, True), (_NEGATIVE_TERMS, False)):
            for term, weight in terms.items():
                start = text.find(term)
                if start == -1:
                    continue
                hits += 1
                # A negated positive reads as negative, and vice versa.
                polarity_positive = is_positive != _is_negated(text, start)
                if polarity_positive:
                    positive += weight
                else:
                    negative += weight

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

    def score(self, headline: str, body: str | None = None) -> SentimentResult:
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

    def analyze_sentiment(self, headline: str, body: str | None = None) -> SentimentResult:
        """Score one article. Never raises: a backend failure degrades to neutral."""
        try:
            return self._backend.score(headline, body)
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
