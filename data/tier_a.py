"""
Tier A auction assets — April/May 2026 batch.
Add new assets here as additional auction opportunities are identified.
"""

from datetime import date
from remates.models import Asset

TIER_A = [
    Asset(
        id="77833",
        name="Sendero Norte 55 D.142",
        address="Sendero Norte 55, Depto. 142",
        city="Viña del Mar",
        bedrooms=2,
        bathrooms=2,
        parking=1,
        m2=50.0,
        catalog_base_uf=650.0,
        market_uf_per_m2_raw=60.0,
        n_comparables=4,
        auction_date=date(2026, 4, 29),
        score=85,
        notes=(
            "✅ VENTA VALIDADA: salió a 3,000 UF (60 UF/m²). "
            "Comparable original 33.31 UF/m² estaba 80% por debajo — era necesidad de venta, no mercado real. "
            "Usar como calibración para Viña del Mar sector plan."
        ),
    ),
    Asset(
        id="77948",
        name="El Arándano 5506 D.101H",
        address="El Arándano 5506, Depto. 101, H",
        city="La Serena",
        bedrooms=3,
        bathrooms=2,
        parking=1,
        m2=59.0,
        catalog_base_uf=700.0,
        market_uf_per_m2_raw=28.83,
        n_comparables=8,
        auction_date=date(2026, 5, 13),
        score=85,
        notes=(
            "✅ Activo prioritario — 8 comparables, 3d/2b, La Serena en crecimiento. "
            "Misma fecha de remate que #77947: coordinar capital."
        ),
    ),
    Asset(
        id="77947",
        name="El Arándano 5113 D.402D",
        address="El Arándano 5113, Depto. 402, D",
        city="La Serena",
        bedrooms=3,
        bathrooms=1,
        parking=1,
        m2=49.0,
        catalog_base_uf=600.0,
        market_uf_per_m2_raw=28.83,
        n_comparables=8,
        auction_date=date(2026, 5, 13),
        score=82,
        notes=(
            "⚠ 3d/1b: ajuste de -12% al fair value aplicado automáticamente. "
            "Max bid real ~630 UF (30% ROI ajustado), no 761 UF del CSV original. "
            "Misma fecha de remate que #77948: conflicto de capital."
        ),
    ),
]

# Quick lookup by ID
TIER_A_BY_ID: dict[str, Asset] = {a.id: a for a in TIER_A}
