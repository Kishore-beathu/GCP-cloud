"""Alert CRUD and notification history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AlertHistory, Stock, UserAlert
from app.routers.stocks import get_stock_or_404
from app.schemas import AlertCreate, AlertHistoryOut, AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _to_alert_out(alert: UserAlert, ticker: str) -> AlertOut:
    return AlertOut(
        id=alert.id,
        user_id=alert.user_id,
        ticker=ticker,
        alert_type=alert.alert_type,
        condition=alert.condition or {},
        channels=alert.channels or [],
        is_active=alert.is_active,
        created_at=alert.created_at,
        last_triggered_at=alert.last_triggered_at,
    )


@router.post(
    "", response_model=AlertOut, status_code=status.HTTP_201_CREATED, summary="Create an alert"
)
async def create_alert(payload: AlertCreate, db: AsyncSession = Depends(get_db)) -> AlertOut:
    stock = await get_stock_or_404(db, payload.ticker)

    alert = UserAlert(
        user_id=payload.user_id,
        ticker_id=stock.id,
        alert_type=payload.alert_type.value,
        condition=payload.condition,
        channels=payload.channels,
        is_active=True,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return _to_alert_out(alert, stock.ticker)


@router.get("", response_model=list[AlertOut], summary="List alerts")
async def list_alerts(
    user_id: str = Query(default="local"),
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> list[AlertOut]:
    query = (
        select(UserAlert, Stock.ticker)
        .join(Stock, Stock.id == UserAlert.ticker_id)
        .where(UserAlert.user_id == user_id)
        .order_by(UserAlert.created_at.desc())
    )
    if active_only:
        query = query.where(UserAlert.is_active.is_(True))

    rows = (await db.execute(query)).all()
    return [_to_alert_out(alert, ticker) for alert, ticker in rows]


@router.delete(
    "/{alert_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Deactivate an alert"
)
async def delete_alert(alert_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    """Soft-delete: the alert stops firing but its history stays intact."""
    alert = await db.get(UserAlert, alert_id)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Alert {alert_id} not found"
        )
    alert.is_active = False
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/history", response_model=list[AlertHistoryOut], summary="Recent alert firings"
)
async def alert_history(
    user_id: str = Query(default="local"),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[AlertHistory]:
    rows = (
        await db.execute(
            select(AlertHistory)
            .join(UserAlert, UserAlert.id == AlertHistory.alert_id)
            .where(UserAlert.user_id == user_id)
            .order_by(AlertHistory.triggered_at.desc())
            .limit(limit)
        )
    ).scalars()
    return list(rows)
