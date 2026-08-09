"""Labelled headlines used to measure the sentiment lexicon.

Every entry is written in the register the real sources actually use: SEC filing
descriptions, Finnhub company-news headlines, and company press releases. Keep
new cases realistic — the value of this corpus is that it stands in for live
news the sandbox cannot reach.

Add a case whenever a real headline is scored wrongly, then fix the lexicon
until it passes. That is how the vocabulary earns each new term.
"""

from __future__ import annotations

# (headline, expected sentiment)
POSITIVE: tuple[str, ...] = (
    # Regulatory — the highest-impact catalysts in this sector
    "European Commission approves marketing authorisation for lead therapy",
    "FDA grants Breakthrough Therapy designation to lead candidate",
    "FDA grants priority review for supplemental application",
    "Company receives orphan drug designation for rare disease candidate",
    "Regulatory submission accepted for review",
    "Marketing application accepted for filing by the agency",
    "CHMP adopts positive opinion for the therapy",
    "Accelerated approval granted for oncology therapy",
    "Label expansion approved in second indication",
    "FDA clears next-generation diagnostic device",
    "Patent upheld, exclusivity extended to 2032",
    # Clinical readouts
    "Phase 3 study met its primary endpoint",
    "Primary endpoint met with statistically significant improvement",
    "Pivotal study achieved its primary endpoint",
    "Topline results positive; therapy was well tolerated",
    "Another study confirms durable response in treated patients",
    "Interim analysis shows clinically meaningful survival benefit",
    "First patient dosed in pivotal study",
    "No safety concerns were observed in the study",
    # Commercial and financial
    "Q3 revenue beats consensus estimates",
    "Company raises full-year outlook after strong quarter",
    "Board approves $2 billion share repurchase programme",
    "Reimbursement agreement secured in Germany",
    "Announces licensing deal with $150 million upfront payment",
    "Record quarterly sales driven by strong demand",
    "Analyst upgrades to outperform on pipeline strength",
)

NEGATIVE: tuple[str, ...] = (
    "FDA issues complete response letter for the application",
    "Phase 3 trial did not meet its primary endpoint",
    "Company recalls lots after FDA warning letter",
    "Trial failed to demonstrate superiority",
    "Guidance cut on slower launch uptake",
    "Full-year outlook lowered after weak demand",
    "Study placed on clinical hold after safety concern",
    "Analyst downgrades to underperform on pricing pressure",
    "Q3 revenue misses consensus estimates",
    "Programme discontinued after disappointing interim data",
    "Regulatory submission rejected by the agency",
    "Approval was not granted at this review cycle",
    "Shares plunge after trial setback",
    "Chief executive resigns amid investigation",
    "Study failed to meet superiority; company will discontinue the programme",
    "Form 483 observations issued following site inspection",
)

NEUTRAL: tuple[str, ...] = (
    "Company to present at healthcare conference next month",
    "Annual shareholder meeting scheduled for June",
    "Investor day webcast will be available online",
    "Company announces participation in an industry panel",
)

# Phrases that a naive substring matcher scores wrongly. Each one cost real
# signal before the lexicon moved to word-boundary matching.
SUBSTRING_TRAPS: tuple[tuple[str, str], ...] = (
    # "submission" and "commission" both contain the negative term "miss"
    ("Regulatory submission accepted for review", "positive"),
    ("European Commission approves the therapy", "positive"),
    # "another"/"notable"/"nothing" all contain the negator "not"
    ("Another study confirms durable response", "positive"),
    ("Notable growth in quarterly revenue", "positive"),
    # "twin" contains "win"; "glossy" contains "loss"
    ("Twin studies scheduled to begin enrolment", "neutral"),
)


def all_labelled() -> tuple[tuple[str, str], ...]:
    """Every case as ``(headline, expected_sentiment)``."""
    return (
        tuple((h, "positive") for h in POSITIVE)
        + tuple((h, "negative") for h in NEGATIVE)
        + tuple((h, "neutral") for h in NEUTRAL)
    )
