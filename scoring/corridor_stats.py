"""
Corridor statistics — compute and refresh median price/m² per
(tipo_propiedad, comuna, m²-bucket).

Called by the scheduler after each scrape run.
"""
from __future__ import annotations

import statistics
from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from config import M2_BUCKETS, PRIORITY_COMMUNES
from database.models import PropertyType
from database.queries import (
    get_properties_for_corridor_stats,
    upsert_corridor_stat,
)

log = structlog.get_logger(__name__)


def _m2_bucket(m2: float) -> tuple[float, float]:
    """Return the bucket (min, max) that contains this m² value."""
    for lo, hi in M2_BUCKETS:
        if lo <= m2 < (hi if hi != float("inf") else 1e9):
            return lo, hi if hi != float("inf") else 9999.0
    return M2_BUCKETS[-1][0], 9999.0


async def refresh_corridor_stats(session: AsyncSession) -> int:
    """
    Recalculate medians for all (tipo, commune, bucket) combinations
    that have at least 3 data points. Returns number of rows updated.
    """
    updated = 0
    for tipo in PropertyType:
        for commune in PRIORITY_COMMUNES:
            for m2_min, m2_max in M2_BUCKETS:
                real_max = m2_max if m2_max != float("inf") else 9999.0
                rows = await get_properties_for_corridor_stats(
                    session,
                    tipo_propiedad=tipo.value,
                    comuna=commune,
                    m2_min=m2_min,
                    m2_max=real_max,
                )
                if len(rows) < 3:
                    continue

                prices = sorted(r["precio_m2"] for r in rows)
                median = statistics.median(prices)
                mean = statistics.mean(prices)
                p25 = _percentile(prices, 25)
                p75 = _percentile(prices, 75)

                await upsert_corridor_stat(
                    session,
                    tipo_propiedad=tipo.value,
                    comuna=commune,
                    m2_min=m2_min,
                    m2_max=real_max,
                    median_precio_m2=median,
                    mean_precio_m2=mean,
                    p25=p25,
                    p75=p75,
                    n_properties=len(prices),
                )
                updated += 1
                log.debug(
                    "corridor_stat_updated",
                    tipo=tipo.value,
                    comuna=commune,
                    m2_range=f"{m2_min}-{real_max}",
                    median=round(median, 0),
                    n=len(prices),
                )

    log.info("corridor_stats_refreshed", rows_updated=updated)
    return updated


def _percentile(sorted_data: list[float], pct: int) -> float:
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    idx = (pct / 100) * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo)
