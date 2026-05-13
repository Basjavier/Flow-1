"""
Scoring engine — composite 0-100 score for each property.

Weights (from CLAUDE.md):
  40%  Price/m² vs corridor median
  30%  Delta fiscal (avalúo SII / precio mercado)
  20%  Time on market (motivated seller signal)
  10%  Price reduction from initial listing
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog

from config import settings

log = structlog.get_logger(__name__)

# Score thresholds for alert generation
SCORE_HIGH = settings.score_high_threshold
SCORE_MEDIUM = settings.score_medium_threshold


# ---------------------------------------------------------------------------
# Sub-score functions (each returns 0-100)
# ---------------------------------------------------------------------------


def score_price_m2(precio_m2: float, median_m2: float) -> float:
    """
    Below-median price/m² is opportunity; above-median is penalised.
    Formula: max(0, 100 - ((precio_m2 / median_m2 - 1) × 100))
    """
    if median_m2 <= 0:
        return 50.0
    raw = 100.0 - ((precio_m2 / median_m2 - 1.0) * 100.0)
    return float(max(0.0, min(100.0, raw)))


def score_delta_fiscal(avaluo_fiscal: int, precio_mercado: int) -> float:
    """
    delta = avaluo_fiscal / precio_mercado
    > 0.85 → high score (market price ≈ fiscal value → undervalued signal)
    < 0.50 → low score (over-asking vs fiscal)
    """
    if precio_mercado <= 0:
        return 50.0
    delta = avaluo_fiscal / precio_mercado
    if delta >= 0.85:
        return 100.0
    if delta >= 0.75:
        return 85.0
    if delta >= 0.65:
        return 70.0
    if delta >= 0.55:
        return 55.0
    if delta >= 0.50:
        return 40.0
    return 20.0


def score_time_on_market(days: int) -> float:
    """
    Longer listings signal motivated sellers.
    > 60 days → high score; < 7 days → neutral.
    """
    if days > 120:
        return 95.0
    if days > 90:
        return 85.0
    if days > 60:
        return 75.0
    if days > 30:
        return 55.0
    if days > 14:
        return 40.0
    if days > 7:
        return 35.0
    return 30.0


def score_price_reduction(precio_actual: int, precio_inicial: int) -> float:
    """
    Price reduction from first-seen listing price signals motivated seller.
    > 5% reduction → score rises.
    """
    if precio_inicial <= 0 or precio_actual >= precio_inicial:
        return 50.0  # no reduction or no data
    reduction = (precio_inicial - precio_actual) / precio_inicial
    if reduction > 0.20:
        return 100.0
    if reduction > 0.15:
        return 90.0
    if reduction > 0.10:
        return 80.0
    if reduction > 0.05:
        return 65.0
    return 55.0


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------


@dataclass
class ScoreBreakdown:
    total: float
    price_m2: float
    delta_fiscal: Optional[float]
    time_on_market: float
    price_reduction: float
    has_sii_data: bool


def compute_score(
    precio_m2: float,
    median_m2: float,
    days_on_market: Optional[int],
    precio_actual: int,
    precio_inicial: Optional[int],
    avaluo_fiscal: Optional[int] = None,
) -> ScoreBreakdown:
    """
    Compute composite property score.

    When SII data is unavailable, weights are redistributed:
      55% price/m², 30% time-on-market, 15% price-reduction.
    """
    s_price = score_price_m2(precio_m2, median_m2)
    s_tom = score_time_on_market(days_on_market or 0)
    s_red = score_price_reduction(precio_actual, precio_inicial or 0)

    has_sii = avaluo_fiscal is not None and avaluo_fiscal > 0
    if has_sii:
        s_fiscal = score_delta_fiscal(avaluo_fiscal, precio_actual)
        total = (
            s_price * 0.40
            + s_fiscal * 0.30
            + s_tom * 0.20
            + s_red * 0.10
        )
    else:
        s_fiscal = None
        total = s_price * 0.55 + s_tom * 0.30 + s_red * 0.15

    return ScoreBreakdown(
        total=round(total, 2),
        price_m2=round(s_price, 2),
        delta_fiscal=round(s_fiscal, 2) if s_fiscal is not None else None,
        time_on_market=round(s_tom, 2),
        price_reduction=round(s_red, 2),
        has_sii_data=has_sii,
    )


def classify_alert_level(score: float) -> Optional[str]:
    """Return alert level string or None if score doesn't warrant an alert."""
    if score >= SCORE_HIGH:
        return "HIGH"
    if score >= SCORE_MEDIUM:
        return "MEDIUM"
    return None


# ---------------------------------------------------------------------------
# New signal scores (Module 1-2)
# ---------------------------------------------------------------------------


def potencial_loteo_score(precio_ha: float, median_ha: float, zonificacion: str = "") -> float:
    """
    Score land subdivision potential.
    precio_ha < median → opportunity. Bonus for favorable zoning.
    """
    if median_ha <= 0:
        return 50.0
    ratio = precio_ha / median_ha
    if ratio <= 0.60:    base = 100.0
    elif ratio <= 0.75:  base = 85.0
    elif ratio <= 0.90:  base = 70.0
    elif ratio <= 1.05:  base = 55.0
    elif ratio <= 1.20:  base = 40.0
    else:                base = 20.0
    # zoning bonus
    zon = zonificacion.upper()
    if any(x in zon for x in ("H", "HABITACIONAL", "ZH")):
        base = min(100.0, base + 10.0)
    elif any(x in zon for x in ("AG", "AGRIC")):
        base = max(0.0, base - 10.0)
    return float(base)


def urgency_score(
    days_on_market: int,
    reduccion_pct: float,
    precio_vs_avaluo_ratio: float = 1.0,
) -> float:
    """
    Independent urgency signal (0-100): motivated seller / distressed asset.
    - days_on_market > 60:    +40
    - reduccion_pct > 10%:    +35
    - precio < 85% avaluo:    +25
    """
    pts = 0.0
    if days_on_market > 60:
        pts += 40.0
    elif days_on_market > 30:
        pts += 20.0
    if reduccion_pct > 0.10:
        pts += 35.0
    elif reduccion_pct > 0.05:
        pts += 18.0
    if 0 < precio_vs_avaluo_ratio < 0.85:
        pts += 25.0
    elif 0 < precio_vs_avaluo_ratio < 0.95:
        pts += 12.0
    return float(min(100.0, pts))


def flip_score(
    upside_pct: float,
    commune_liquidity: float = 50.0,
) -> float:
    """
    Score for short-term flip potential (0-100).
    upside_pct: (corridor_median_m2 / precio_m2 - 1) * 100
    commune_liquidity: pre-computed 0-100 liquidity score for commune
    """
    # upside component (60%)
    if upside_pct >= 25:    up_score = 100.0
    elif upside_pct >= 15:  up_score = 85.0
    elif upside_pct >= 10:  up_score = 70.0
    elif upside_pct >= 5:   up_score = 55.0
    elif upside_pct >= 0:   up_score = 40.0
    else:                   up_score = 20.0
    # liquidity is passed in directly
    return float(min(100.0, up_score * 0.60 + commune_liquidity * 0.40))
