"""
Reports and analytics endpoints.
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import CommuneSummary, CorridorStatResponse, PortfolioSummary
from database.models import CorridorStat, Property
from database.queries import get_portfolio_summary
from database.session import get_db

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary", response_model=PortfolioSummary)
async def portfolio_summary(db: Annotated[AsyncSession, Depends(get_db)]):
    data = await get_portfolio_summary(db)
    return PortfolioSummary(**data) if data else PortfolioSummary(
        total_active=0, high_opportunity=0, medium_opportunity=0, n_comunas=0
    )


@router.get("/by-commune", response_model=list[CommuneSummary])
async def report_by_commune(db: Annotated[AsyncSession, Depends(get_db)]):
    stmt = text("""
        SELECT
            comuna,
            corredor,
            COUNT(*)                                AS n_properties,
            ROUND(AVG(score)::numeric, 2)           AS avg_score,
            ROUND(AVG(precio_m2)::numeric, 0)       AS avg_precio_m2,
            PERCENTILE_CONT(0.5) WITHIN GROUP
                (ORDER BY precio_m2)                AS median_precio_m2,
            MIN(precio)                             AS min_precio,
            MAX(precio)                             AS max_precio
        FROM properties
        WHERE is_active = TRUE
        GROUP BY comuna, corredor
        ORDER BY avg_score DESC NULLS LAST
    """)
    rows = (await db.execute(stmt)).fetchall()
    return [
        CommuneSummary(
            comuna=r[0],
            corredor=r[1],
            n_properties=r[2],
            avg_score=float(r[3]) if r[3] else None,
            avg_precio_m2=float(r[4]) if r[4] else None,
            median_precio_m2=float(r[5]) if r[5] else None,
            min_precio=r[6],
            max_precio=r[7],
        )
        for r in rows
    ]


@router.get("/corridor-stats", response_model=list[CorridorStatResponse])
async def corridor_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    comuna: str | None = Query(None),
    tipo_propiedad: str | None = Query(None),
):
    filters = []
    if comuna:
        filters.append(CorridorStat.comuna.ilike(f"%{comuna}%"))
    if tipo_propiedad:
        filters.append(CorridorStat.tipo_propiedad == tipo_propiedad)

    stmt = (
        select(CorridorStat)
        .where(*filters)
        .order_by(CorridorStat.comuna, CorridorStat.m2_min)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [CorridorStatResponse.model_validate(r) for r in rows]


@router.get("/top-opportunities")
async def top_opportunities(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=50),
):
    """High-score properties with rich context — used in dashboard and email digest."""
    stmt = text("""
        SELECT
            p.id, p.external_id, p.source, p.tipo_propiedad,
            p.comuna, p.corredor, p.address,
            p.precio, p.precio_uf, p.m2, p.precio_m2,
            p.dormitorios, p.banos,
            p.score, p.score_price_m2, p.score_delta_fiscal,
            p.score_time_on_market, p.score_price_reduction,
            p.url, p.fecha_publicacion,
            s.avaluo_fiscal
        FROM properties p
        LEFT JOIN sii_avaluos s ON s.property_id = p.id
        WHERE p.is_active = TRUE
          AND p.score IS NOT NULL
        ORDER BY p.score DESC
        LIMIT :limit
    """)
    rows = (await db.execute(stmt, {"limit": limit})).fetchall()
    cols = [
        "id", "external_id", "source", "tipo_propiedad", "comuna", "corredor", "address",
        "precio", "precio_uf", "m2", "precio_m2", "dormitorios", "banos",
        "score", "score_price_m2", "score_delta_fiscal", "score_time_on_market",
        "score_price_reduction", "url", "fecha_publicacion", "avaluo_fiscal",
    ]
    return [dict(zip(cols, r)) for r in rows]
