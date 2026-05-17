"""
Financial engine — calibrated to real Chilean flip/remate practitioners.

RESEARCH SOURCES (April 2025):
  lucasfinanzas.cl, fraccional.cl, gpremium.cl, buenainversion.cl,
  cronoshare.cl, chocale.cl (market data)

═══════════════════════════════════════════════════════════════════════
CALIBRACIÓN BASADA EN EJECUCIÓN REAL:

COSTOS DE ENTRADA (buyer in remate judicial):
  Martillero fee:    1–2%   (judicial; ~10% only in voluntary auctions)
  Notaría + CBR:     1–1.5%
  IVA sobre fees:    ~0.5%
  Alzamiento liens:  0.5–1%
  Legal/misc:        0.5%
  ──────────────────────────
  TOTAL ENTRY:       4–5%   ← model uses 4.5% (conservative)

RENOVACIÓN REAL (flip, not luxury — La Serena/Viña 2024-2025):
  Cosmética    (pintura, pisos, limpieza, accesorios):  3.5–4.5 UF/m²
  Estándar     (+ baños, cocina básica, elect.):        6–9 UF/m²
  Deteriorada  (+ instalaciones, problemas ocultos):    10–14 UF/m²
  Estructural  (todo + problemas graves):               15–20 UF/m²

  Model scenarios include 15–20% contingency baked into each level.
  Source: cronoshare.cl (250k–1M+ CLP/m²), fraccional.cl

DEUDAS OCULTAS (gastos comunes + contribuciones + servicios):
  Propiedades en remate suelen llevar 18–36 meses en morosidad.
  Gastos comunes:       ~3–5 UF/mes × 18 meses = 54–90 UF
  Contribuciones:       ~0.5%/año × FV × 2 años = ~14–28 UF
  Servicios básicos:    ~10–20 UF
  Interés legal arrears: hasta 34.71% anual (2.8%/mes)
  ──────────────────────────
  TOTAL DEUDAS:         80–220 UF (depende de tiempo en morosidad)

CICLO COMPLETO DE FLIP (industry benchmark: 10 meses):
  Adjudicación → inscripción CBR:  4–6 semanas
  Renovación:                       8–16 semanas
  Venta (listing → cierre):         4–12 semanas (Viña), 6–14 (La Serena)
  ──────────────────────────
  BEST CASE:     5–6 meses
  REALISTA:      9–11 meses
  CONSERVADOR:   12–14 meses
  STRESS:        15–18 meses

PRECIO DE SALIDA (post-renovación, propiedades usadas):
  Cosmética:   82–85% del comparable de mercado (se nota trabajo mínimo)
  Estándar:    87–90% (vendible a valor normal de usado renovado)
  Completa:    91–95% (compite con depto nuevo en muchos aspectos)
  Source: fraccional.cl, buenainversion.cl

ROI OBJETIVO REAL (flippers chilenos experimentados):
  Mínimo aceptable:   12–15%
  Buena operación:    18–22%
  Excelente:          25–35% (generalmente segunda subasta)
  Marketing claims:   40%+ (no típico)

PRECIO DE MERCADO (referencia para validar comparables):
  La Serena nuevo:   52.42 UF/m² (chocale.cl 2024)
  La Serena usado:   ~43 UF/m²  (–17.6% descuento)
  Viña del Mar:      60–75 UF/m² (nuevo), ~50–62 UF/m² (usado)

  ⚠ ALERTA COMPARABLES: Los activos actuales usan 28.83 UF/m² para
  La Serena y 33.31 UF/m² para Viña. Esto está 25–35% por debajo de
  los benchmarks de mercado. Posibles razones: ubicación secundaria,
  edificio antiguo, comparables de venta de necesidad.
  VERIFICAR antes de licitar.
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
from typing import Dict

from .models import Asset, ScenarioParams, ScenarioResult

# ---------------------------------------------------------------------------
# Scenario presets — calibrated to real Chilean flip practitioners
# ---------------------------------------------------------------------------

# ESCENARIO 1: Cosmético
# Propiedad solo necesita pintura, pisos, accesorios. Sin obra mayor.
# Ciclo rápido. Vende a descuento por ser "trabajo mínimo visible".
COSMETICO = ScenarioParams(
    name="Cosmético",
    holding_months=6,
    renovation_uf_per_m2=4.5,      # 3.5 base + 15% contingencia baked in
    hidden_debts_uf=80.0,           # ~15 meses morosidad: GC + contrib
    sale_pct_market=0.83,           # descuento por no ser renovación completa
    appreciation_annual=0.0,
    fixed_cost_rate=0.045,
    monthly_carrying_rate=0.0075,   # GC propios durante holding + seguro
    selling_costs_pct=0.04,         # corretaje 2% + IVA + notaría/CBR salida
)

# ESCENARIO 2: Estándar — ESCENARIO DE REFERENCIA
# Flip real: baños, cocina básica, pisos, pintura, electricidad.
# Calidad "renovado listo para habitar". 10 meses de ciclo.
ESTANDAR = ScenarioParams(
    name="Estándar",
    holding_months=10,
    renovation_uf_per_m2=8.0,       # 7 base + 15% contingencia
    hidden_debts_uf=120.0,          # ~20 meses morosidad + intereses
    sale_pct_market=0.88,           # precio normal usado-renovado
    appreciation_annual=0.0,
    fixed_cost_rate=0.045,
    monthly_carrying_rate=0.0075,
    selling_costs_pct=0.04,
)

# ESCENARIO 3: Deteriorado
# Propiedad en mal estado: instalaciones viejas, daños ocultos, posible
# ocupante. Renovación pesada, ciclo largo. Sale por debajo de ESTANDAR
# porque la obra mayor a veces deja "cicatrices" visibles.
DETERIORADO = ScenarioParams(
    name="Deteriorado",
    holding_months=13,
    renovation_uf_per_m2=13.0,      # 10–11 base + 20% contingencia
    hidden_debts_uf=170.0,          # 30+ meses morasidad + intereses legales
    sale_pct_market=0.85,           # aun con reno, el mercado descuenta
    appreciation_annual=0.0,
    fixed_cost_rate=0.045,
    monthly_carrying_rate=0.0075,
    selling_costs_pct=0.04,
)

# ESCENARIO 4: Stress — Todo sale mal
# Problemas estructurales, deuda máxima, ocupante con desahucio,
# mercado en corrección leve. Contingencia total.
STRESS = ScenarioParams(
    name="Stress",
    holding_months=17,
    renovation_uf_per_m2=18.0,      # problemas graves + 20% contingencia
    hidden_debts_uf=220.0,          # 3 años máx. legal + intereses
    sale_pct_market=0.80,           # venta de necesidad para salir
    appreciation_annual=-0.01,      # mercado leve corrección
    fixed_cost_rate=0.045,
    monthly_carrying_rate=0.0075,
    selling_costs_pct=0.045,        # costos extra si venta urgente
)

SCENARIOS: Dict[str, ScenarioParams] = {
    "cosmetico":   COSMETICO,
    "estandar":    ESTANDAR,
    "deteriorado": DETERIORADO,
    "stress":      STRESS,
}

# Bid table ROI targets — aligned to real practitioner targets
BID_ROI_TARGETS = [0.10, 0.15, 0.20, 0.25, 0.30]

BID_LEVEL_META = {
    0.10: ("MINIMO",    "Mínimo — 10%",    "Flujo apenas positivo. Solo si hay alta certeza y activo cosmético."),
    0.15: ("ACEPTABLE", "Aceptable — 15%", "Rentabilidad real de flip competitivo. Margen ajustado."),
    0.20: ("OBJETIVO",  "OBJETIVO — 20%",  "RECOMENDADO: buena operación con buffer ante imprevistos."),
    0.25: ("BUENO",     "Bueno — 25%",     "Exige entrada baja. Típico de segunda subasta con poca competencia."),
    0.30: ("EXCELENTE", "Excelente — 30%", "Excepcional. Solo segunda subasta o activo sin competencia."),
}

# Comparable market benchmark (from research, for sanity check)
MARKET_BENCHMARK_UF_M2 = {
    "La Serena":   {"new": 52.42, "used": 43.2},
    "Viña del Mar": {"new": 67.0,  "used": 60.0},   # validado: Sendero Norte 55 D.142 vendió a 3,000 UF (60 UF/m²)
    "Santiago":    {"new": 85.0,  "used": 70.0},
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
        Max entry price to hit target_roi under given params.

          net_sale = (1 + roi) × total_cost
          total_cost = entry × proportional_mult + reno_uf + hidden_uf
          entry = (net_sale/(1+roi) - reno_uf - hidden_uf) / proportional_mult

        Returns 0 if renovation + debts alone exceed the target budget.
        """
        ref = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        net_sale  = ref.net_sale
        reno_uf   = params.renovation_uf_per_m2 * asset.m2
        hidden    = params.hidden_debts_uf
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
        return {name: ScenarioResult(asset=asset, params=p, entry_price=ep) for name, p in SCENARIOS.items()}

    @staticmethod
    def breakeven_price(asset: Asset, params: ScenarioParams) -> float:
        ref      = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        numerator = ref.net_sale - params.renovation_uf_per_m2 * asset.m2 - params.hidden_debts_uf
        if numerator <= 0:
            return 0.0
        return numerator / params.proportional_multiplier

    @staticmethod
    def build_bid_table(asset: Asset, params: ScenarioParams) -> list[dict]:
        rows = []
        for roi in sorted(BID_ROI_TARGETS, reverse=True):
            bid    = FinancialEngine.max_bid(asset, params, roi)
            key, level_name, description = BID_LEVEL_META[roi]
            result = ScenarioResult(asset=asset, params=params, entry_price=bid)
            rows.append({
                "roi_target":  roi,
                "max_bid_uf":  bid,
                "uf_per_m2":   bid / asset.m2 if bid > 0 else 0.0,
                "buffer_uf":   bid - asset.catalog_base_uf,
                "buffer_pct":  (bid - asset.catalog_base_uf) / asset.catalog_base_uf if asset.catalog_base_uf else 0,
                "level_key":   key,
                "level_name":  level_name,
                "description": description,
                "total_costs": result.total_costs,
                "net_sale":    result.net_sale,
                "profit":      result.profit,
                "viable":      bid >= asset.catalog_base_uf * 0.50,
            })
        return rows

    @staticmethod
    def cost_breakdown(asset: Asset, params: ScenarioParams, entry_price: float) -> dict:
        r = ScenarioResult(asset=asset, params=params, entry_price=entry_price)
        return {
            "entry_price":       entry_price,
            "tx_costs_entry":    entry_price * params.fixed_cost_rate,
            "holding_costs":     entry_price * params.monthly_carrying_rate * params.holding_months,
            "renovation_uf":     r.cost_renovation,
            "renovation_per_m2": params.renovation_uf_per_m2,
            "hidden_debts":      r.cost_hidden_debts,
            "total_costs":       r.total_costs,
            "net_sale":          r.net_sale,
            "profit":            r.profit,
            "roi":               r.roi,
        }

    @staticmethod
    def comparables_sanity_check(asset: Asset) -> dict | None:
        """
        Flag if asset's market UF/m² is significantly below research benchmarks.
        Returns a warning dict if discrepancy > 25%, else None.
        """
        benchmarks = MARKET_BENCHMARK_UF_M2
        city_bench = None
        for city_key in benchmarks:
            if city_key.lower() in asset.city.lower():
                city_bench = benchmarks[city_key]["used"]
                break
        if city_bench is None:
            return None
        gap = (city_bench - asset.market_uf_per_m2_raw) / city_bench
        if gap > 0.20:
            return {
                "asset_uf_m2":      asset.market_uf_per_m2_raw,
                "benchmark_uf_m2":  city_bench,
                "gap_pct":          gap,
                "implied_upside":   asset.m2 * city_bench * asset.bathroom_adjustment,
            }
        return None
