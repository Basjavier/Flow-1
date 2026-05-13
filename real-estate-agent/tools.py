"""
Tool implementations for the real estate agent.

Each function maps 1:1 to a Claude tool definition and calls into the
existing remates analysis engine to produce a JSON-serialisable result.
"""

from __future__ import annotations
import json
import sys
import os

# Make sure the project root is importable when this module is run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.tier_a import TIER_A, TIER_A_BY_ID
from remates.engine import (
    COSMETICO, ESTANDAR, DETERIORADO, STRESS, SCENARIOS,
    FinancialEngine,
)
from remates.sensitivity import SensitivityAnalyzer
from remates.models import Asset, ScenarioParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCENARIO_MAP: dict[str, ScenarioParams] = SCENARIOS  # cosmetico/estandar/deteriorado/stress


def _resolve_scenario(name: str) -> ScenarioParams:
    key = name.lower().strip()
    if key not in _SCENARIO_MAP:
        raise ValueError(f"Escenario desconocido: '{name}'. Opciones: {list(_SCENARIO_MAP)}")
    return _SCENARIO_MAP[key]


def _asset_summary(a: Asset) -> dict:
    return {
        "id": a.id,
        "nombre": a.name,
        "direccion": a.address,
        "ciudad": a.city,
        "dormitorios": a.bedrooms,
        "banos": a.bathrooms,
        "estacionamientos": a.parking,
        "m2": a.m2,
        "valor_catalogo_uf": a.catalog_base_uf,
        "precio_mercado_uf_m2": a.market_uf_per_m2_raw,
        "precio_mercado_ajustado_uf_m2": round(a.market_uf_per_m2_adjusted, 2),
        "fair_value_uf": round(a.fair_value_adjusted, 1),
        "descuento_vs_catalogo_pct": round(a.discount_vs_catalog * 100, 1),
        "comparables": a.n_comparables,
        "confianza_comparables": a.comparable_confidence,
        "fecha_remate": a.auction_date.isoformat(),
        "dias_para_remate": a.days_to_auction,
        "urgente": a.is_urgent,
        "score": a.score,
        "notas": a.notes,
    }


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def list_assets() -> dict:
    """Return a summary list of all Tier-A assets."""
    return {
        "activos": [_asset_summary(a) for a in TIER_A],
        "total": len(TIER_A),
    }


def get_asset_detail(asset_id: str) -> dict:
    """Return detailed info for a single asset plus a sanity check on comparables."""
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado. IDs disponibles: {list(TIER_A_BY_ID)}"}
    asset = TIER_A_BY_ID[asset_id]
    detail = _asset_summary(asset)
    warning = FinancialEngine.comparables_sanity_check(asset)
    if warning:
        detail["alerta_comparables"] = {
            "uf_m2_activo": round(warning["asset_uf_m2"], 2),
            "benchmark_mercado_uf_m2": round(warning["benchmark_uf_m2"], 2),
            "brecha_pct": round(warning["gap_pct"] * 100, 1),
            "valor_implicito_uf": round(warning["implied_upside"], 1),
        }
    return detail


def analyze_asset(asset_id: str, scenario: str = "estandar", entry_price_uf: float | None = None) -> dict:
    """Run a full financial scenario for one asset.

    Parameters
    ----------
    asset_id:
        Remate asset ID (e.g. '77948').
    scenario:
        One of 'cosmetico', 'estandar', 'deteriorado', 'stress'.
    entry_price_uf:
        Override the entry price in UF.  If omitted, uses catalog_base_uf.
    """
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _resolve_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}

    asset = TIER_A_BY_ID[asset_id]
    result = FinancialEngine.run(asset, params, entry_price_uf)
    cb = FinancialEngine.cost_breakdown(asset, params, result.entry_price)

    return {
        "activo_id": asset_id,
        "escenario": scenario,
        "precio_entrada_uf": round(result.entry_price, 1),
        "fair_value_uf": round(result.fair_value, 1),
        "descuento_vs_fair_value_pct": round(result.discount_vs_fair * 100, 1),
        "venta_neta_uf": round(result.net_sale, 1),
        "costos_totales_uf": round(result.total_costs, 1),
        "desglose_costos": {
            "entrada_proporcional_uf": round(cb["tx_costs_entry"] + result.entry_price, 1),
            "costos_transaccion_entrada_uf": round(cb["tx_costs_entry"], 1),
            "costos_holding_uf": round(cb["holding_costs"], 1),
            "renovacion_uf": round(cb["renovation_uf"], 1),
            "renovacion_uf_m2": round(cb["renovation_per_m2"], 2),
            "deudas_ocultas_uf": round(cb["hidden_debts"], 1),
        },
        "utilidad_uf": round(result.profit, 1),
        "roi_pct": round(result.roi * 100, 1),
        "equity_multiple": round(result.equity_multiple, 2),
        "meses_holding": params.holding_months,
    }


def get_bid_table(asset_id: str, scenario: str = "estandar") -> dict:
    """Return a bid table with max bids at each ROI target (10–30 %)."""
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _resolve_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}

    asset = TIER_A_BY_ID[asset_id]
    rows = FinancialEngine.build_bid_table(asset, params)

    clean_rows = []
    for r in rows:
        clean_rows.append({
            "nivel": r["level_name"],
            "roi_objetivo_pct": round(r["roi_target"] * 100, 0),
            "max_oferta_uf": round(r["max_bid_uf"], 1),
            "max_oferta_uf_m2": round(r["uf_per_m2"], 1),
            "buffer_vs_catalogo_uf": round(r["buffer_uf"], 1),
            "buffer_vs_catalogo_pct": round(r["buffer_pct"] * 100, 1),
            "utilidad_estimada_uf": round(r["profit"], 1),
            "viable": r["viable"],
            "descripcion": r["description"],
        })

    breakeven = FinancialEngine.breakeven_price(asset, params)
    return {
        "activo_id": asset_id,
        "escenario": scenario,
        "precio_catalogo_uf": asset.catalog_base_uf,
        "fair_value_uf": round(asset.fair_value_adjusted, 1),
        "precio_breakeven_uf": round(breakeven, 1),
        "tabla_ofertas": clean_rows,
    }


def get_max_bid(asset_id: str, target_roi_pct: float = 20.0, scenario: str = "estandar") -> dict:
    """Compute the single max bid for a given ROI target."""
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _resolve_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}

    asset = TIER_A_BY_ID[asset_id]
    target = target_roi_pct / 100.0
    bid = FinancialEngine.max_bid(asset, params, target)

    return {
        "activo_id": asset_id,
        "escenario": scenario,
        "roi_objetivo_pct": target_roi_pct,
        "max_oferta_uf": round(bid, 1),
        "max_oferta_uf_m2": round(bid / asset.m2, 1) if bid > 0 else 0,
        "precio_catalogo_uf": asset.catalog_base_uf,
        "buffer_uf": round(bid - asset.catalog_base_uf, 1),
        "viable": bid >= asset.catalog_base_uf * 0.50,
    }


def run_monte_carlo(
    asset_id: str,
    scenario: str = "estandar",
    entry_price_uf: float | None = None,
    n_simulations: int = 5000,
) -> dict:
    """Run a Monte Carlo simulation and return percentile outcomes."""
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _resolve_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}

    asset = TIER_A_BY_ID[asset_id]
    bid = entry_price_uf if entry_price_uf else FinancialEngine.max_bid(asset, params, 0.20)
    if bid <= 0:
        return {"error": "No se pudo calcular una oferta válida para el precio de entrada."}

    mc = SensitivityAnalyzer.monte_carlo(asset, params, bid, n_simulations=n_simulations)

    return {
        "activo_id": asset_id,
        "escenario": scenario,
        "precio_entrada_uf": round(bid, 1),
        "n_simulaciones": mc.n_simulations,
        "roi_promedio_pct": round(mc.mean_roi * 100, 1),
        "roi_mediana_pct": round(mc.median_roi * 100, 1),
        "roi_std_pct": round(mc.std_roi * 100, 1),
        "percentiles": {
            "p5_pct": round(mc.p5_roi * 100, 1),
            "p25_pct": round(mc.p25_roi * 100, 1),
            "p75_pct": round(mc.p75_roi * 100, 1),
            "p95_pct": round(mc.p95_roi * 100, 1),
        },
        "probabilidades": {
            "positivo_pct": round(mc.prob_positive * 100, 1),
            "sobre_15pct_pct": round(mc.prob_above_15pct * 100, 1),
            "sobre_20pct_pct": round(mc.prob_above_20pct * 100, 1),
            "sobre_30pct_pct": round(mc.prob_above_30pct * 100, 1),
            "perdida_pct": round(mc.prob_loss * 100, 1),
        },
    }


def compare_assets(asset_ids: list[str], scenario: str = "estandar") -> dict:
    """Compare several assets side-by-side under the same scenario."""
    try:
        params = _resolve_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}

    results = []
    for aid in asset_ids:
        if aid not in TIER_A_BY_ID:
            results.append({"id": aid, "error": "no encontrado"})
            continue
        asset = TIER_A_BY_ID[aid]
        bid_20 = FinancialEngine.max_bid(asset, params, 0.20)
        res = FinancialEngine.run(asset, params, bid_20)
        results.append({
            "id": aid,
            "nombre": asset.name,
            "ciudad": asset.city,
            "m2": asset.m2,
            "precio_catalogo_uf": asset.catalog_base_uf,
            "fair_value_uf": round(asset.fair_value_adjusted, 1),
            "max_oferta_20pct_uf": round(bid_20, 1),
            "roi_con_max_oferta_pct": round(res.roi * 100, 1),
            "utilidad_estimada_uf": round(res.profit, 1),
            "dias_para_remate": asset.days_to_auction,
            "score": asset.score,
            "confianza_comparables": asset.comparable_confidence,
        })

    return {
        "escenario": scenario,
        "comparativa": results,
    }


# ---------------------------------------------------------------------------
# Tool schema definitions for the Claude API
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "list_assets",
        "description": (
            "Lista todos los activos inmobiliarios en remate disponibles en el portafolio Tier-A, "
            "con información básica de cada uno (ciudad, m², precio catálogo, fecha de remate, score)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_asset_detail",
        "description": (
            "Devuelve información detallada de un activo específico, incluyendo "
            "fair value, descuento vs catálogo, confianza en comparables y alertas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {
                    "type": "string",
                    "description": "ID numérico del activo (ej: '77948', '77833', '77947').",
                },
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "analyze_asset",
        "description": (
            "Ejecuta un análisis financiero completo para un activo bajo un escenario dado. "
            "Retorna desglose de costos, utilidad estimada y ROI. "
            "Escenarios disponibles: cosmetico, estandar, deteriorado, stress."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo."},
                "scenario": {
                    "type": "string",
                    "enum": ["cosmetico", "estandar", "deteriorado", "stress"],
                    "description": "Escenario de renovación y holding. Default: estandar.",
                },
                "entry_price_uf": {
                    "type": "number",
                    "description": "Precio de entrada en UF. Si se omite, usa catalog_base_uf.",
                },
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "get_bid_table",
        "description": (
            "Genera una tabla de ofertas máximas para distintos objetivos de ROI (10 % a 30 %). "
            "Muestra el precio máximo a licitar para cada nivel de rentabilidad objetivo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo."},
                "scenario": {
                    "type": "string",
                    "enum": ["cosmetico", "estandar", "deteriorado", "stress"],
                    "description": "Escenario de renovación. Default: estandar.",
                },
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "get_max_bid",
        "description": (
            "Calcula la oferta máxima para alcanzar un ROI objetivo específico. "
            "Útil para decisiones rápidas sobre el límite de puja."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo."},
                "target_roi_pct": {
                    "type": "number",
                    "description": "ROI objetivo en porcentaje (ej: 20 para 20 %). Default: 20.",
                },
                "scenario": {
                    "type": "string",
                    "enum": ["cosmetico", "estandar", "deteriorado", "stress"],
                    "description": "Escenario de renovación. Default: estandar.",
                },
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "run_monte_carlo",
        "description": (
            "Ejecuta una simulación Monte Carlo para estimar la distribución de ROI bajo incertidumbre "
            "en precio de mercado, tiempo de venta y costos de renovación. "
            "Retorna percentiles y probabilidades de distintos umbrales de rentabilidad."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo."},
                "scenario": {
                    "type": "string",
                    "enum": ["cosmetico", "estandar", "deteriorado", "stress"],
                    "description": "Escenario base. Default: estandar.",
                },
                "entry_price_uf": {
                    "type": "number",
                    "description": "Precio de entrada en UF. Si se omite, usa max_bid al 20 % ROI.",
                },
                "n_simulations": {
                    "type": "integer",
                    "description": "Número de simulaciones (default: 5000, máx recomendado: 20000).",
                },
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "compare_assets",
        "description": (
            "Compara múltiples activos en paralelo bajo el mismo escenario. "
            "Muestra fair value, oferta máxima al 20 % ROI, utilidad y días para el remate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de IDs de activos a comparar.",
                },
                "scenario": {
                    "type": "string",
                    "enum": ["cosmetico", "estandar", "deteriorado", "stress"],
                    "description": "Escenario de análisis. Default: estandar.",
                },
            },
            "required": ["asset_ids"],
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "list_assets": lambda inp: list_assets(),
    "get_asset_detail": lambda inp: get_asset_detail(**inp),
    "analyze_asset": lambda inp: analyze_asset(**inp),
    "get_bid_table": lambda inp: get_bid_table(**inp),
    "get_max_bid": lambda inp: get_max_bid(**inp),
    "run_monte_carlo": lambda inp: run_monte_carlo(**inp),
    "compare_assets": lambda inp: compare_assets(**inp),
}


def dispatch(tool_name: str, tool_input: dict) -> str:
    """Execute a tool by name and return its result as a JSON string."""
    fn = TOOL_FUNCTIONS.get(tool_name)
    if fn is None:
        return json.dumps({"error": f"Herramienta desconocida: {tool_name}"})
    try:
        result = fn(tool_input)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
