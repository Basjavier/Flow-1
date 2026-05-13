"""
Analytical queries using raw SQL — per CLAUDE.md convention: no ORM for heavy analytics.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_corridor_median_price_m2(
    session: AsyncSession,
    tipo_propiedad: str,
    comuna: str,
    m2: float,
) -> float | None:
    """
    Return the pre-computed median CLP/m² for the matching corridor bucket.
    Falls back to commune-wide median (any m² bucket) if no exact bucket exists.
    """
    sql = text("""
        SELECT median_precio_m2
        FROM corridor_stats
        WHERE tipo_propiedad = :tipo
          AND comuna = :comuna
          AND m2_min <= :m2
          AND m2_max >= :m2
        ORDER BY calculated_at DESC
        LIMIT 1
    """)
    row = (await session.execute(sql, {"tipo": tipo_propiedad, "comuna": comuna, "m2": m2})).fetchone()
    if row:
        return float(row[0])

    # Fallback: any bucket in this commune
    sql_fallback = text("""
        SELECT median_precio_m2
        FROM corridor_stats
        WHERE tipo_propiedad = :tipo
          AND comuna = :comuna
        ORDER BY calculated_at DESC
        LIMIT 1
    """)
    row = (await session.execute(sql_fallback, {"tipo": tipo_propiedad, "comuna": comuna})).fetchone()
    return float(row[0]) if row else None


async def get_properties_for_corridor_stats(
    session: AsyncSession,
    tipo_propiedad: str,
    comuna: str,
    m2_min: float,
    m2_max: float,
) -> list[dict[str, Any]]:
    """Return active properties in a corridor bucket for statistics computation."""
    sql = text("""
        SELECT id, precio_m2, m2
        FROM properties
        WHERE is_active = TRUE
          AND tipo_propiedad = :tipo
          AND comuna = :comuna
          AND m2 >= :m2_min
          AND m2 < :m2_max
        ORDER BY created_at DESC
    """)
    rows = (
        await session.execute(
            sql,
            {"tipo": tipo_propiedad, "comuna": comuna, "m2_min": m2_min, "m2_max": m2_max},
        )
    ).fetchall()
    return [{"id": r[0], "precio_m2": r[1], "m2": r[2]} for r in rows]


async def get_properties_needing_score_update(
    session: AsyncSession,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Properties whose score is NULL or whose corridor stats were updated after last score."""
    sql = text("""
        SELECT
            p.id, p.tipo_propiedad, p.comuna, p.m2, p.precio, p.precio_m2,
            p.precio_inicial, p.fecha_publicacion, p.corredor
        FROM properties p
        WHERE p.is_active = TRUE
          AND (p.score IS NULL OR p.updated_at < NOW() - INTERVAL '6 hours')
        ORDER BY p.created_at DESC
        LIMIT :limit
    """)
    rows = (await session.execute(sql, {"limit": limit})).fetchall()
    return [
        {
            "id": r[0],
            "tipo_propiedad": r[1],
            "comuna": r[2],
            "m2": r[3],
            "precio": r[4],
            "precio_m2": r[5],
            "precio_inicial": r[6],
            "fecha_publicacion": r[7],
            "corredor": r[8],
        }
        for r in rows
    ]


async def get_high_score_properties(
    session: AsyncSession,
    min_score: float = 60.0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = text("""
        SELECT
            p.id, p.external_id, p.source, p.tipo_propiedad, p.comuna,
            p.corredor, p.precio, p.precio_m2, p.m2, p.score,
            p.dormitorios, p.banos, p.url, p.fecha_publicacion
        FROM properties p
        WHERE p.is_active = TRUE
          AND p.score >= :min_score
        ORDER BY p.score DESC, p.created_at DESC
        LIMIT :limit
    """)
    rows = (await session.execute(sql, {"min_score": min_score, "limit": limit})).fetchall()
    cols = [
        "id", "external_id", "source", "tipo_propiedad", "comuna",
        "corredor", "precio", "precio_m2", "m2", "score",
        "dormitorios", "banos", "url", "fecha_publicacion",
    ]
    return [dict(zip(cols, r)) for r in rows]


async def get_portfolio_summary(session: AsyncSession) -> dict[str, Any]:
    """Aggregate stats for the current active portfolio."""
    sql = text("""
        SELECT
            COUNT(*)                                    AS total_active,
            COUNT(*) FILTER (WHERE score >= 75)         AS high_opportunity,
            COUNT(*) FILTER (WHERE score BETWEEN 60 AND 74.99) AS medium_opportunity,
            ROUND(AVG(score)::numeric, 2)               AS avg_score,
            ROUND(AVG(precio_m2)::numeric, 0)           AS avg_precio_m2,
            COUNT(DISTINCT comuna)                      AS n_comunas,
            MAX(created_at)                             AS last_scraped_at
        FROM properties
        WHERE is_active = TRUE
    """)
    row = (await session.execute(sql)).fetchone()
    if not row:
        return {}
    return {
        "total_active": row[0],
        "high_opportunity": row[1],
        "medium_opportunity": row[2],
        "avg_score": float(row[3]) if row[3] else None,
        "avg_precio_m2": float(row[4]) if row[4] else None,
        "n_comunas": row[5],
        "last_scraped_at": row[6],
    }


async def upsert_corridor_stat(
    session: AsyncSession,
    tipo_propiedad: str,
    comuna: str,
    m2_min: float,
    m2_max: float,
    median_precio_m2: float,
    mean_precio_m2: float,
    p25: float,
    p75: float,
    n_properties: int,
) -> None:
    """Insert or replace a corridor stat row."""
    sql = text("""
        INSERT INTO corridor_stats
            (tipo_propiedad, comuna, m2_min, m2_max,
             median_precio_m2, mean_precio_m2, p25_precio_m2, p75_precio_m2,
             n_properties, calculated_at)
        VALUES
            (:tipo, :comuna, :m2_min, :m2_max,
             :median, :mean, :p25, :p75,
             :n, NOW() AT TIME ZONE 'UTC')
    """)
    await session.execute(
        sql,
        {
            "tipo": tipo_propiedad,
            "comuna": comuna,
            "m2_min": m2_min,
            "m2_max": m2_max,
            "median": median_precio_m2,
            "mean": mean_precio_m2,
            "p25": p25,
            "p75": p75,
            "n": n_properties,
        },
    )


async def get_unsent_alerts(
    session: AsyncSession,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = text("""
        SELECT
            a.id, a.property_id, a.score, a.alert_level, a.created_at,
            p.url, p.comuna, p.tipo_propiedad, p.precio, p.m2, p.score AS prop_score
        FROM alerts a
        JOIN properties p ON p.id = a.property_id
        WHERE a.sent_at IS NULL
          AND a.is_active = TRUE
        ORDER BY a.score DESC, a.created_at ASC
        LIMIT :limit
    """)
    rows = (await session.execute(sql, {"limit": limit})).fetchall()
    cols = [
        "id", "property_id", "score", "alert_level", "created_at",
        "url", "comuna", "tipo_propiedad", "precio", "m2", "prop_score",
    ]
    return [dict(zip(cols, r)) for r in rows]
