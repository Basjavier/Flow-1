"""
Alert endpoints.
"""
from __future__ import annotations

from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import AlertListResponse, AlertResponse
from database.models import Alert, AlertLevel
from database.session import get_db

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse)
async def list_alerts(
    db: Annotated[AsyncSession, Depends(get_db)],
    alert_level: Optional[str] = Query(None, description="HIGH or MEDIUM"),
    unsent_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    filters = [Alert.is_active == True]  # noqa: E712
    if alert_level:
        filters.append(Alert.alert_level == alert_level.upper())
    if unsent_only:
        filters.append(Alert.sent_at.is_(None))

    count_stmt = select(func.count()).select_from(Alert).where(and_(*filters))
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Alert)
        .where(and_(*filters))
        .order_by(Alert.score.desc(), Alert.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await db.execute(stmt)).scalars().all()

    return AlertListResponse(
        total=total,
        items=[AlertResponse.model_validate(a) for a in items],
    )


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stmt = select(Alert).where(Alert.id == alert_id)
    alert = (await db.execute(stmt)).scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertResponse.model_validate(alert)


@router.delete("/{alert_id}")
async def dismiss_alert(
    alert_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Soft-dismiss an alert (set is_active=False)."""
    stmt = select(Alert).where(Alert.id == alert_id)
    alert = (await db.execute(stmt)).scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_active = False
    await db.flush()
    return {"status": "dismissed", "id": alert_id}
