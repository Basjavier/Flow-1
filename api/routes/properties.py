"""
Property endpoints.
"""
from __future__ import annotations

from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import PropertyFilter, PropertyListResponse, PropertyResponse
from database.models import Property, PropertySource, PropertyType
from database.queries import get_high_score_properties
from database.session import get_db

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/properties", tags=["properties"])


@router.get("", response_model=PropertyListResponse)
async def list_properties(
    db: Annotated[AsyncSession, Depends(get_db)],
    comuna: Optional[str] = Query(None),
    tipo_propiedad: Optional[str] = Query(None),
    corredor: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    max_precio: Optional[int] = Query(None, ge=0),
    min_m2: Optional[float] = Query(None, ge=0),
    max_m2: Optional[float] = Query(None, ge=0),
    source: Optional[str] = Query(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    filters = [Property.is_active == True]  # noqa: E712
    if comuna:
        filters.append(Property.comuna.ilike(f"%{comuna}%"))
    if tipo_propiedad:
        filters.append(Property.tipo_propiedad == tipo_propiedad)
    if corredor:
        filters.append(Property.corredor == corredor)
    if min_score is not None:
        filters.append(Property.score >= min_score)
    if max_precio is not None:
        filters.append(Property.precio <= max_precio)
    if min_m2 is not None:
        filters.append(Property.m2 >= min_m2)
    if max_m2 is not None:
        filters.append(Property.m2 <= max_m2)
    if source:
        filters.append(Property.source == source)

    count_stmt = select(func.count()).select_from(Property).where(and_(*filters))
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Property)
        .where(and_(*filters))
        .order_by(Property.score.desc().nullslast(), Property.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await db.execute(stmt)).scalars().all()

    return PropertyListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[PropertyResponse.model_validate(p) for p in items],
    )


@router.get("/top", response_model=list[PropertyResponse])
async def top_properties(
    db: Annotated[AsyncSession, Depends(get_db)],
    min_score: float = Query(default=60.0, ge=0, le=100),
    limit: int = Query(default=20, ge=1, le=100),
):
    rows = await get_high_score_properties(db, min_score=min_score, limit=limit)
    # Fetch full ORM objects for proper serialization
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    stmt = select(Property).where(Property.id.in_(ids)).order_by(Property.score.desc())
    items = (await db.execute(stmt)).scalars().all()
    return [PropertyResponse.model_validate(p) for p in items]


@router.get("/{property_id}", response_model=PropertyResponse)
async def get_property(
    property_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stmt = select(Property).where(Property.id == property_id)
    prop = (await db.execute(stmt)).scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    return PropertyResponse.model_validate(prop)
