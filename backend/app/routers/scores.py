"""Ranked stock scores, and the evidence for whether the ranking works."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import scoring, sectors

router = APIRouter(prefix="/scores", tags=["scores"])


@router.get("", summary="Every tracked symbol, ranked best first")
async def list_scores(
    group: str | None = Query(default=None, description="Limit to an industry group"),
    sector: str | None = Query(
        default=None,
        description="Limit to one sector within a group, e.g. clinical_stage",
    ),
    limit: int = Query(default=25, ge=1, le=500),
    min_coverage: float = Query(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Drop symbols scored on less than this share of the inputs",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Rank the universe on price behaviour and news.

    Ranks are always computed against the **whole** universe and filtered
    afterwards. Re-ranking within a filtered slice would make "rank 1" mean
    something different in every request.
    """
    if group and not sectors.sectors_in(group.strip().lower()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown group {group!r}. Try GET /stocks/sectors.",
        )
    if sector and not sectors.is_known_sector(sector):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown sector {sector!r}. Try GET /stocks/sectors.",
        )

    scored = await scoring.score_universe(db)
    if group:
        key = group.strip().lower()
        scored = [item for item in scored if item.sector_group == key]
    if sector:
        key = sector.strip().lower()
        scored = [item for item in scored if (item.sector or "").lower() == key]
    if min_coverage:
        scored = [item for item in scored if item.coverage >= min_coverage]

    return {
        "generated_for": len(scored),
        "weights": {
            "pillars": scoring.PILLAR_WEIGHTS,
            "technical": {key: weight for key, (_, weight, _) in scoring.TECHNICAL_WEIGHTS.items()},
            "sentiment": {key: weight for key, (_, weight, _) in scoring.SENTIMENT_WEIGHTS.items()},
        },
        "method": (
            "Each factor is a percentile against the rest of the universe today, "
            "weighted and averaged. The score ranks; it does not forecast. "
            "See GET /scores/validation for whether it has separated anything."
        ),
        "scores": [item.as_dict() for item in scored[:limit]],
    }


@router.get("/validation", summary="Did a high score precede a better return?")
async def score_validation(
    as_of_days_ago: int = Query(default=30, ge=7, le=365),
    horizon_days: int = Query(default=21, ge=1, le=180),
    periods: int = Query(
        default=6, ge=1, le=24, description="How many start dates to test"
    ),
    step_days: int = Query(default=21, ge=1, le=90, description="Gap between start dates"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Measure the ranking against what happened next, on your own data.

    Reports each pillar separately over several start dates, because a single
    period cannot distinguish a ranking that does not work from a month that
    went against it — and because a blend that works only through one of its
    halves is worth knowing about. Reports the result whatever it says,
    including "not enough history".
    """
    return await scoring.validate(db, as_of_days_ago, horizon_days, periods, step_days)


@router.get("/{ticker}", summary="One symbol's score, with every factor")
async def get_score(ticker: str, db: AsyncSession = Depends(get_db)) -> dict:
    symbol = ticker.strip().upper()
    scored = await scoring.score_universe(db)
    for item in scored:
        if item.ticker == symbol:
            return item.as_dict()

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            f"{symbol} has no score. Either it is not tracked, or it has neither "
            f"{scoring.MIN_SESSIONS} sessions of price history nor any scored news."
        ),
    )
