"""
APScheduler jobs — runs scrapers every N hours, scores results, fires alerts.

Start the scheduler standalone:
  python -m scheduler.jobs

Or embed it inside the FastAPI lifespan.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings, COMMUNE_TO_CORREDOR
from database.models import Alert, AlertLevel, Property, PropertySource, PropertyType, SIIAvaluo
from database.queries import get_properties_needing_score_update
from database.session import get_db_session
from scoring.corridor_stats import refresh_corridor_stats
from scoring.engine import ScoreBreakdown, classify_alert_level, compute_score

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Ingest helpers
# ---------------------------------------------------------------------------


async def _upsert_property(session: AsyncSession, data: dict) -> tuple[Property, bool]:
    """
    Insert or update a scraped property. Returns (property, is_new).
    Records price history entry if price changed.
    """
    stmt = select(Property).where(
        Property.source == data["source"],
        Property.external_id == data["external_id"],
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    is_new = existing is None
    if is_new:
        prop = Property(
            external_id=data["external_id"],
            source=data["source"],
            tipo_propiedad=data.get("tipo_propiedad", "departamento"),
            comuna=data["comuna"],
            corredor=COMMUNE_TO_CORREDOR.get(data["comuna"]),
            address=data.get("address"),
            precio=data["precio"],
            precio_uf=data.get("precio_uf"),
            precio_inicial=data["precio"],  # first price = initial
            m2=data["m2"],
            precio_m2=data["precio_m2"],
            dormitorios=data.get("dormitorios"),
            banos=data.get("banos"),
            estacionamientos=data.get("estacionamientos"),
            descripcion=data.get("descripcion"),
            url=data["url"],
            fecha_publicacion=data.get("fecha_publicacion"),
        )
        session.add(prop)
        await session.flush()
    else:
        prop = existing
        price_changed = existing.precio != data["precio"]
        if price_changed:
            from database.models import PriceHistory
            session.add(
                PriceHistory(
                    property_id=prop.id,
                    precio=data["precio"],
                    precio_uf=data.get("precio_uf"),
                )
            )
        # Update mutable fields
        prop.precio = data["precio"]
        prop.precio_uf = data.get("precio_uf", prop.precio_uf)
        prop.precio_m2 = data["precio_m2"]
        prop.m2 = data["m2"]
        prop.address = data.get("address") or prop.address
        prop.is_active = True
        await session.flush()

    return prop, is_new


async def _apply_scores(session: AsyncSession) -> int:
    """
    Recalculate scores for all properties that need updating.
    Returns count of scored properties.
    """
    from database.queries import get_corridor_median_price_m2

    pending = await get_properties_needing_score_update(session, limit=500)
    scored = 0

    for row in pending:
        median = await get_corridor_median_price_m2(
            session,
            tipo_propiedad=row["tipo_propiedad"],
            comuna=row["comuna"],
            m2=row["m2"],
        )
        if not median:
            continue

        # SII data if available
        sii_stmt = select(SIIAvaluo.avaluo_fiscal).where(SIIAvaluo.property_id == row["id"])
        avaluo = (await session.execute(sii_stmt)).scalar_one_or_none()

        days = None
        if row["fecha_publicacion"]:
            delta = datetime.now(timezone.utc) - row["fecha_publicacion"]
            days = delta.days

        breakdown = compute_score(
            precio_m2=row["precio_m2"],
            median_m2=median,
            days_on_market=days,
            precio_actual=row["precio"],
            precio_inicial=row["precio_inicial"],
            avaluo_fiscal=avaluo,
        )

        # Update property
        prop_stmt = select(Property).where(Property.id == row["id"])
        prop = (await session.execute(prop_stmt)).scalar_one_or_none()
        if prop:
            prop.score = breakdown.total
            prop.score_price_m2 = breakdown.price_m2
            prop.score_delta_fiscal = breakdown.delta_fiscal
            prop.score_time_on_market = breakdown.time_on_market
            prop.score_price_reduction = breakdown.price_reduction
            scored += 1

            # Create alert if threshold crossed
            level = classify_alert_level(breakdown.total)
            if level:
                await _maybe_create_alert(session, prop, breakdown.total, level)

    log.info("scoring_complete", scored=scored)
    return scored


async def _maybe_create_alert(
    session: AsyncSession,
    prop: Property,
    score: float,
    level: str,
) -> None:
    """Create alert only if no active unsent alert already exists for this property."""
    existing_stmt = select(Alert).where(
        Alert.property_id == prop.id,
        Alert.is_active == True,  # noqa: E712
        Alert.sent_at.is_(None),
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing:
        return

    alert = Alert(
        property_id=prop.id,
        score=score,
        alert_level=AlertLevel(level),
    )
    session.add(alert)
    log.info("alert_created", property_id=prop.id, score=score, level=level)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


async def run_full_pipeline(
    sources: list[str] | None = None,
    communes: list[str] | None = None,
    tipos: list[str] | None = None,
    max_pages: int = 5,
) -> dict:
    """
    1. Scrape configured sources
    2. Upsert into DB
    3. Refresh corridor stats
    4. Score all pending properties
    5. Generate alerts
    """
    if sources is None:
        sources = ["portal_inmobiliario", "yapo"]
    if tipos is None:
        tipos = ["departamento"]

    total_scraped = 0
    total_new = 0

    # --- Scrape ---
    all_raw: list[dict] = []
    for source in sources:
        try:
            if source == "portal_inmobiliario":
                from scraper.portal_inmobiliario import scrape_all_priority_communes as scrape_portal
                items = await scrape_portal(tipos=tipos, max_pages=max_pages)
            elif source == "yapo":
                from scraper.yapo import scrape_all_priority_communes as scrape_yapo
                items = await scrape_yapo(tipos=tipos, max_pages=max_pages)
            elif source == "toctoc":
                from scraper.toctoc import scrape_all_priority_communes as scrape_toctoc
                items = await scrape_toctoc(tipos=tipos, max_pages=max_pages)
            else:
                log.warning("unknown_source", source=source)
                continue
            all_raw.extend(items)
            log.info("source_scraped", source=source, count=len(items))
        except Exception as exc:
            log.error("source_scrape_failed", source=source, error=str(exc))

    # --- Upsert ---
    async with get_db_session() as session:
        for data in all_raw:
            try:
                _, is_new = await _upsert_property(session, data)
                total_scraped += 1
                total_new += int(is_new)
            except Exception as exc:
                log.error("upsert_failed", url=data.get("url", ""), error=str(exc))

        # --- Corridor stats ---
        try:
            await refresh_corridor_stats(session)
        except Exception as exc:
            log.error("corridor_stats_failed", error=str(exc))

        # --- Score ---
        try:
            scored = await _apply_scores(session)
        except Exception as exc:
            log.error("scoring_failed", error=str(exc))
            scored = 0

    log.info(
        "pipeline_complete",
        scraped=total_scraped,
        new=total_new,
        scored=scored,
    )
    return {"scraped": total_scraped, "new": total_new, "scored": scored}


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_full_pipeline,
        "interval",
        hours=settings.scrape_interval_hours,
        id="full_pipeline",
        replace_existing=True,
        kwargs={"sources": ["portal_inmobiliario", "yapo"]},
    )
    return scheduler


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


async def _main():
    import logging
    logging.basicConfig(level=logging.INFO)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))

    scheduler = build_scheduler()
    scheduler.start()
    log.info("scheduler_started", interval_hours=settings.scrape_interval_hours)

    # Run once immediately on startup
    await run_full_pipeline()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("scheduler_stopped")


if __name__ == "__main__":
    asyncio.run(_main())
