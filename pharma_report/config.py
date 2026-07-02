"""Environment-driven configuration for the weekly report job."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_ISPE_CHAPTERS = [
    "ISPE DACH (Germany, Austria, Switzerland)",
    "ISPE France",
    "ISPE UK & Ireland",
    "ISPE Nordic",
    "ISPE Belgium",
    "ISPE Netherlands",
    "ISPE Italy",
    "ISPE Spain",
    "ISPE Poland",
    "ISPE Ireland",
]

DEFAULT_NEWS_SOURCES = [
    "FiercePharma",
    "PharmaTimes",
    "European Pharmaceutical Review",
    "EFPIA",
    "EMA (European Medicines Agency) news",
    "in-Pharma Technologist",
]


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str | None = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY"))
    model: str = field(default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-opus-4-8"))
    gcs_bucket: str | None = field(default_factory=lambda: os.environ.get("REPORT_BUCKET"))
    ispe_chapters: list[str] = field(default_factory=lambda: list(DEFAULT_ISPE_CHAPTERS))
    news_sources: list[str] = field(default_factory=lambda: list(DEFAULT_NEWS_SOURCES))
    web_search_max_uses: int = field(
        default_factory=lambda: int(os.environ.get("WEB_SEARCH_MAX_USES", "15"))
    )
    lookahead_days: int = field(default_factory=lambda: int(os.environ.get("LOOKAHEAD_DAYS", "14")))
    lookback_days: int = field(default_factory=lambda: int(os.environ.get("LOOKBACK_DAYS", "7")))


def load_config() -> Config:
    return Config()
