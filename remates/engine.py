"""
Financial engine — recalibrated for real Chilean remate execution.

New scenario logic:
  OPTIMISTA     → light cosmetic reno (3 UF/m²), low hidden debts, fast exit
  REALISTA      → medium reno (4.5 UF/m²), 100 UF hidden debts, 8-month hold
  CONSERVADOR   → heavy reno (6 UF/m²), 150 UF hidden debts, 10-month hold
  STRESS        → structural reno (8 UF/m²), 200 UF hidden debts, 14-month hold,
                  market at -10%, forced sale at 80%

Why renovation is now absolute (UF/m²):
  A 59m² apartment costs ~265 UF to renovate at 4.5 UF/m².
  That cost is the same whether you paid 700 UF or 950 UF for it.
  Using renovation as % of purchase price was systematically understating
  real cost and overstating ROI by 2-3x.

Realistic renovation benchmarks (Chile 2025-2026, regiones):
  3.0 UF/m² — cosmética: pintura, pisos laminados, limpieza profunda
  4.5 UF/m² — media: baños básicos, cocina, instalaciones eléctricas
  6.0 UF/m² — completa: baños nuevos, cocina equipada, ventanas
  8.0 UF/m² — estructural: todo lo anterior + problemas ocultos, humedad

Hidden debts typical range (propiedad con 18-36 meses en morosidad):
  50 UF  — mínimo (contribuciones 1 año + gastos comunes 6 meses)
  100 UF — medio (contribuciones 2 años + gastos comunes 12 meses)
  150 UF — alto (contribuciones 2-3 años + gastos comunes 18-24 meses)
  200 UF — stress (máximo razonable antes de declarar no viable)
"""

from __future__ import annotations
from typing import Dict

from .models import Asset, ScenarioParams, ScenarioResult

# ---------------------------------------------------------------------------
# Scenario presets — recalibrated
# ---------------------------------------------------------------------------

OPTIMISTA = ScenarioParams(
    name="Optimista",
    holding_months=5,
    renovation_uf_per_m2=3.0,
    hidden_debts_uf=50.0,
    sale_pct_market=0.95,
    appreciation_annual=0.015,
    fixed_cost_rate=0.045,
    monthly_carrying_rate=0.0075,
    selling_costs_pct=0.035,
)

REALISTA = ScenarioParams(
    name="Realista",
    holding_months=8,
    renovation_uf_per_m2=4.5,
    hidden_debts_uf=100.0,
    sale_pct_market=0.88,
    appreciation_annual=0.00,
    fixed_cost_rate=0.045,
    monthly_carrying_rate=0.0075,
    selling_costs_pct=0.035,
)

CONSERVADOR = ScenarioParams(
    name="Conservador",
    holding_months=10,
    renovation_uf_per_m2=6.0,
    hidden_debts_uf=150.0,
    sale_pct_market=0.85,
    appreciation_annual=0.00,
    fixed_cost_rate=0.045,
    monthly_carrying_rate=0.0075,
    selling_costs_pct=0.035,
)

STRESS = ScenarioParams(
    name="Stress",
    holding_months=14,
    renovation_uf_per_m2=8.0,
    hidden_debts_uf=200.0,
    sale_pct_market=0.80,
    appreciation_annual=-0.02,
    fixed_cost_rate=0.045,
    monthly_carrying_rate=0.0075,
    selling_costs_pct=0.04,
)

SCENARIOS: Dict[str, ScenarioParams] = {
    "optimista":   OPTIMISTA,
    "realista":    REALISTA,
    "conservador": CONSERVADOR,
    "stress":      STRESS,
}

# ROI targets for bid table
BID_ROI_TARGETS = [0.10, 0.15, 0.20, 0.25, 0.30]

BID_LEVEL_META = {
    0.10: ("MINIMO",      "Mínimo — flujo positivo",   "Solo si tienes certeza total del comparable y sin competencia."),
    0.15: ("BAJO",        "Bajo — aceptable",          "Margen ajustado. Requiere ejecución sin desvíos."),
    0.20: ("OBJETIVO",    "PRECIO OBJETIVO",           "RECOMENDADO: ROI realista con buffer para imprevistos."),
    0.25: ("ROBUSTO",     "Robusto — exige bajo bid",  "Difícil en remates competitivos. Bid inicial ideal."),
    0.30: ("MUY_ROBUSTO", "Muy robusto — raro lograr", "Solo en segunda subasta o activos sin competencia."),
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
        Max entry price to achieve target_roi under given params.

        Derivation:
          net_sale = (1 + roi) × total_costs
          total_costs = entry × proportional_mult + reno_uf + hidden_uf
          → entry = (net_sale/(1+roi) - reno_uf - hidden_uf) / proportional_mult

        Returns 0 if the target is mathematically impossible (renovation + debts
        alone exceed net_sale / (1+roi) even at zero entry price).
        """
        ref = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        net_sale = ref.net_sale
        reno_uf = params.renovation_uf_per_m2 * asset.m2
        hidden = params.hidden_debts_uf
        numerator = net_sale / (1 + target_roi) - reno_uf - hidden
        if numerator <= 0:
            return 0.0
        return numerator / params.proportional_multiplier

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
            name: ScenarioResult(asset=asset, params=p, entry_price=ep)
            for name, p in SCENARIOS.items()
        }

    @staticmethod
    def breakeven_price(asset: Asset, params: ScenarioParams) -> float:
        """Entry price at which ROI = 0."""
        ref = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        numerator = ref.net_sale - params.renovation_uf_per_m2 * asset.m2 - params.hidden_debts_uf
        if numerator <= 0:
            return 0.0
        return numerator / params.proportional_multiplier

    @staticmethod
    def build_bid_table(asset: Asset, params: ScenarioParams) -> list[dict]:
        rows = []
        for roi in sorted(BID_ROI_TARGETS, reverse=True):
            bid = FinancialEngine.max_bid(asset, params, roi)
            key, level_name, description = BID_LEVEL_META[roi]
            result_at_bid = ScenarioResult(asset=asset, params=params, entry_price=bid)
            rows.append({
                "roi_target":   roi,
                "max_bid_uf":   bid,
                "uf_per_m2":    bid / asset.m2 if bid > 0 else 0,
                "buffer_uf":    bid - asset.catalog_base_uf,
                "buffer_pct":   (bid - asset.catalog_base_uf) / asset.catalog_base_uf if asset.catalog_base_uf else 0,
                "level_key":    key,
                "level_name":   level_name,
                "description":  description,
                "total_costs":  result_at_bid.total_costs,
                "net_sale":     result_at_bid.net_sale,
                "profit":       result_at_bid.profit,
            })
        return rows

    @staticmethod
    def cost_breakdown(asset: Asset, params: ScenarioParams, entry_price: float) -> dict:
        r = ScenarioResult(asset=asset, params=params, entry_price=entry_price)
        return {
            "entry_price":          entry_price,
            "tx_costs_entry":       entry_price * params.fixed_cost_rate,
            "holding_costs":        entry_price * params.monthly_carrying_rate * params.holding_months,
            "renovation_uf":        r.cost_renovation,
            "renovation_per_m2":    params.renovation_uf_per_m2,
            "hidden_debts":         r.cost_hidden_debts,
            "total_costs":          r.total_costs,
            "net_sale":             r.net_sale,
            "profit":               r.profit,
            "roi":                  r.roi,
        }
