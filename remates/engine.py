"""
Financial engine: scenario presets and core calculations.
"""

from __future__ import annotations
import copy
from typing import Dict

from .models import Asset, ScenarioParams, ScenarioResult

# ---------------------------------------------------------------------------
# Scenario presets
# ---------------------------------------------------------------------------

BASE = ScenarioParams(
    name="Base",
    holding_months=4,
    renovation_pct=0.08,
    sale_pct_market=1.00,
    appreciation_annual=0.015,
)

CONSERVATIVE = ScenarioParams(
    name="Conservador",
    holding_months=7,
    renovation_pct=0.12,
    sale_pct_market=0.88,
    appreciation_annual=0.00,
)

STRESS = ScenarioParams(
    name="Stress",
    holding_months=10,
    renovation_pct=0.15,
    sale_pct_market=0.80,
    appreciation_annual=0.00,
)

SCENARIOS: Dict[str, ScenarioParams] = {
    "base": BASE,
    "conservative": CONSERVATIVE,
    "stress": STRESS,
}

# ROI targets for bid table generation
BID_ROI_TARGETS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]

BID_LEVEL_META = {
    0.15: ("MINIMO",       "Mínimo aceptable",    "Margen justo. Solo si hay alta certeza de los comps."),
    0.20: ("CONSERVADOR",  "Conservador básico",  "Buffer razonable para imprevistos menores."),
    0.25: ("RECOMENDADO",  "Conservador recomendado", "Cubre emergencias y extensión de plazo."),
    0.30: ("OBJETIVO",     "PRECIO OBJETIVO",     "RECOMENDADO: buena rentabilidad con margen robusto."),
    0.35: ("AGRESIVO",     "Agresivo-seguro",     "Exige salida de precio clara. Bid inicial ideal."),
    0.40: ("MUY_CONSERV",  "Muy conservador",     "Si hay dudas del comparable de mercado."),
}


# ---------------------------------------------------------------------------
# FinancialEngine
# ---------------------------------------------------------------------------

class FinancialEngine:

    @staticmethod
    def run(
        asset: Asset,
        params: ScenarioParams,
        entry_price: float | None = None,
    ) -> ScenarioResult:
        ep = entry_price if entry_price is not None else asset.catalog_base_uf
        return ScenarioResult(asset=asset, params=params, entry_price=ep)

    @staticmethod
    def max_bid(asset: Asset, params: ScenarioParams, target_roi: float) -> float:
        """
        Max entry price achievable while hitting target_roi under given params.
        net_sale is independent of entry price (set by market, not purchase cost).
        """
        ref = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        return ref.net_sale / ((1 + target_roi) * params.cost_multiplier)

    @staticmethod
    def roi_at_price(asset: Asset, params: ScenarioParams, entry_price: float) -> float:
        return ScenarioResult(asset=asset, params=params, entry_price=entry_price).roi

    @staticmethod
    def run_all_scenarios(
        asset: Asset,
        entry_price: float | None = None,
    ) -> Dict[str, ScenarioResult]:
        ep = entry_price if entry_price is not None else asset.catalog_base_uf
        return {
            name: ScenarioResult(asset=asset, params=params, entry_price=ep)
            for name, params in SCENARIOS.items()
        }

    @staticmethod
    def breakeven_price(asset: Asset, params: ScenarioParams) -> float:
        """Entry price at which ROI = 0 (profit = 0)."""
        ref = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        return ref.net_sale / params.cost_multiplier

    @staticmethod
    def build_bid_table(asset: Asset, params: ScenarioParams) -> list[dict]:
        rows = []
        for roi in sorted(BID_ROI_TARGETS, reverse=True):
            bid = FinancialEngine.max_bid(asset, params, roi)
            key, level_name, description = BID_LEVEL_META[roi]
            rows.append({
                "roi_target": roi,
                "max_bid_uf": bid,
                "uf_per_m2": bid / asset.m2,
                "buffer_uf": bid - asset.catalog_base_uf,
                "buffer_pct": (bid - asset.catalog_base_uf) / asset.catalog_base_uf,
                "level_key": key,
                "level_name": level_name,
                "description": description,
            })
        return rows
