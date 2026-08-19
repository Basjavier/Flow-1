"""
Tool functions for the real estate agent — wraps the remates engine.
All functions return JSON-serializable dicts or lists.
"""

from __future__ import annotations
import json
import os
import sys

# Make project root importable despite the hyphen in the directory name
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_PROJECT_ROOT, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data.tier_a import TIER_A, TIER_A_BY_ID
from remates.engine import (
    FinancialEngine,
    SCENARIOS,
    COSMETICO, ESTANDAR, DETERIORADO, STRESS,
)
from remates.sensitivity import SensitivityAnalyzer
from remates.optimizer import BidOptimizer, CapitalAllocator
from remates.models import ScenarioResult


def _get_scenario(name: str):
    key = name.lower().strip()
    if key not in SCENARIOS:
        raise ValueError(f"Escenario desconocido: '{name}'. Opciones: {list(SCENARIOS.keys())}")
    return SCENARIOS[key]


def _asset_summary(a) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "city": a.city,
        "address": a.address,
        "bedrooms": a.bedrooms,
        "bathrooms": a.bathrooms,
        "parking": a.parking,
        "m2": a.m2,
        "catalog_base_uf": a.catalog_base_uf,
        "market_uf_per_m2_raw": a.market_uf_per_m2_raw,
        "market_uf_per_m2_adjusted": round(a.market_uf_per_m2_adjusted, 2),
        "fair_value_raw": round(a.fair_value_raw, 1),
        "fair_value_adjusted": round(a.fair_value_adjusted, 1),
        "bathroom_adjustment": a.bathroom_adjustment,
        "entry_uf_per_m2": round(a.entry_uf_per_m2, 2),
        "discount_vs_catalog": round(a.discount_vs_catalog, 4),
        "n_comparables": a.n_comparables,
        "comparable_confidence": a.comparable_confidence,
        "comparable_uncertainty_std": a.comparable_uncertainty_std,
        "auction_date": str(a.auction_date),
        "days_to_auction": a.days_to_auction,
        "is_urgent": a.is_urgent,
        "score": a.score,
        "notes": a.notes,
    }


def _result_summary(r: ScenarioResult) -> dict:
    return {
        "scenario": r.params.name,
        "entry_price": round(r.entry_price, 1),
        "entry_uf_per_m2": round(r.entry_uf_per_m2, 2),
        "fair_value": round(r.fair_value, 1),
        "gross_sale": round(r.gross_sale, 1),
        "net_sale": round(r.net_sale, 1),
        "cost_entry_proportional": round(r.cost_entry_proportional, 1),
        "cost_renovation": round(r.cost_renovation, 1),
        "cost_hidden_debts": round(r.cost_hidden_debts, 1),
        "total_costs": round(r.total_costs, 1),
        "profit": round(r.profit, 1),
        "roi": round(r.roi, 4),
        "roi_pct": f"{r.roi*100:.1f}%",
        "equity_multiple": round(r.equity_multiple, 3),
        "holding_months": r.params.holding_months,
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def list_assets() -> list:
    return [_asset_summary(a) for a in TIER_A]


def get_asset_detail(asset_id: str) -> dict:
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado. IDs disponibles: {list(TIER_A_BY_ID.keys())}"}
    a = TIER_A_BY_ID[asset_id]
    result = _asset_summary(a)
    check = FinancialEngine.comparables_sanity_check(a)
    result["comparables_sanity_check"] = check
    return result


def analyze_asset(asset_id: str, scenario: str = "estandar", entry_price_uf: float | None = None) -> dict:
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}
    a = TIER_A_BY_ID[asset_id]
    ep = entry_price_uf if entry_price_uf is not None else a.catalog_base_uf
    r = FinancialEngine.run(a, params, ep)
    return _result_summary(r)


def run_all_scenarios(asset_id: str, entry_price_uf: float | None = None) -> dict:
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    a = TIER_A_BY_ID[asset_id]
    ep = entry_price_uf if entry_price_uf is not None else a.catalog_base_uf
    results = FinancialEngine.run_all_scenarios(a, ep)
    return {name: _result_summary(r) for name, r in results.items()}


def get_bid_table(asset_id: str, scenario: str = "estandar") -> list:
    if asset_id not in TIER_A_BY_ID:
        return [{"error": f"Activo '{asset_id}' no encontrado."}]
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return [{"error": str(e)}]
    a = TIER_A_BY_ID[asset_id]
    rows = FinancialEngine.build_bid_table(a, params)
    for row in rows:
        row.pop("asset", None)
    return rows


def get_max_bid(asset_id: str, scenario: str = "estandar", target_roi_pct: float = 20.0) -> dict:
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}
    a = TIER_A_BY_ID[asset_id]
    roi = target_roi_pct / 100.0
    bid = FinancialEngine.max_bid(a, params, roi)
    return {
        "asset_id": asset_id,
        "scenario": scenario,
        "target_roi_pct": target_roi_pct,
        "max_bid_uf": round(bid, 1),
        "max_bid_uf_per_m2": round(bid / a.m2, 2) if bid > 0 else 0,
        "catalog_base_uf": a.catalog_base_uf,
        "buffer_uf": round(bid - a.catalog_base_uf, 1),
        "buffer_pct": round((bid - a.catalog_base_uf) / a.catalog_base_uf * 100, 1) if a.catalog_base_uf else 0,
        "viable": bid >= a.catalog_base_uf * 0.50,
    }


def get_recommended_bid(asset_id: str, scenario: str = "estandar") -> dict:
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}
    a = TIER_A_BY_ID[asset_id]
    table = FinancialEngine.build_bid_table(a, params)
    by_roi = {r["roi_target"]: r for r in table}
    def _fmt(row):
        return {
            "roi_target_pct": f"{row['roi_target']*100:.0f}%",
            "max_bid_uf": round(row["max_bid_uf"], 1),
            "uf_per_m2": round(row["uf_per_m2"], 2),
            "buffer_uf": round(row["buffer_uf"], 1),
            "level": row["level_name"],
            "description": row["description"],
        }
    return {
        "asset_id": asset_id,
        "scenario": scenario,
        "open_bid_30pct_roi": _fmt(by_roi[0.30]) if 0.30 in by_roi else None,
        "target_bid_25pct_roi": _fmt(by_roi[0.25]) if 0.25 in by_roi else None,
        "acceptable_bid_20pct_roi": _fmt(by_roi[0.20]) if 0.20 in by_roi else None,
        "minimum_bid_15pct_roi": _fmt(by_roi[0.15]) if 0.15 in by_roi else None,
    }


def run_monte_carlo(asset_id: str, scenario: str = "estandar", entry_price_uf: float | None = None, n_simulations: int = 5000) -> dict:
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}
    a = TIER_A_BY_ID[asset_id]
    ep = entry_price_uf if entry_price_uf is not None else a.catalog_base_uf
    mc = SensitivityAnalyzer.monte_carlo(a, params, ep, n_simulations=n_simulations)
    return {
        "asset_id": asset_id,
        "scenario": scenario,
        "entry_price_uf": ep,
        "n_simulations": mc.n_simulations,
        "mean_roi_pct": round(mc.mean_roi * 100, 1),
        "median_roi_pct": round(mc.median_roi * 100, 1),
        "std_roi_pct": round(mc.std_roi * 100, 1),
        "p5_roi_pct": round(mc.p5_roi * 100, 1),
        "p25_roi_pct": round(mc.p25_roi * 100, 1),
        "p75_roi_pct": round(mc.p75_roi * 100, 1),
        "p95_roi_pct": round(mc.p95_roi * 100, 1),
        "prob_positive_pct": round(mc.prob_positive * 100, 1),
        "prob_above_15pct_roi": round(mc.prob_above_15pct * 100, 1),
        "prob_above_20pct_roi": round(mc.prob_above_20pct * 100, 1),
        "prob_above_30pct_roi": round(mc.prob_above_30pct * 100, 1),
        "prob_loss_pct": round(mc.prob_loss * 100, 1),
    }


def tornado_analysis(asset_id: str, scenario: str = "estandar", entry_price_uf: float | None = None) -> list:
    if asset_id not in TIER_A_BY_ID:
        return [{"error": f"Activo '{asset_id}' no encontrado."}]
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return [{"error": str(e)}]
    a = TIER_A_BY_ID[asset_id]
    ep = entry_price_uf if entry_price_uf is not None else a.catalog_base_uf
    rows = SensitivityAnalyzer.tornado(a, params, ep)
    return [
        {
            "rank": i + 1,
            "variable": r["variable"],
            "label": r["label"],
            "base_roi_pct": round(r["base_roi"] * 100, 1),
            "roi_high_pct": round(r["roi_high"] * 100, 1),
            "roi_low_pct": round(r["roi_low"] * 100, 1),
            "impact_high_pp": round(r["impact_high"] * 100, 1),
            "impact_low_pp": round(r["impact_low"] * 100, 1),
            "total_swing_pp": round(r["total_swing"] * 100, 1),
        }
        for i, r in enumerate(rows)
    ]


def market_price_sweep(asset_id: str, scenario: str = "estandar", entry_price_uf: float | None = None) -> dict:
    if asset_id not in TIER_A_BY_ID:
        return {"error": f"Activo '{asset_id}' no encontrado."}
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}
    a = TIER_A_BY_ID[asset_id]
    ep = entry_price_uf if entry_price_uf is not None else a.catalog_base_uf
    rows = SensitivityAnalyzer.market_price_sweep(a, params, ep)
    profitable = [r for r in rows if r["profitable"]]
    return {
        "asset_id": asset_id,
        "scenario": scenario,
        "entry_price_uf": ep,
        "base_market_uf_m2": a.market_uf_per_m2_raw,
        "n_profitable_scenarios": len(profitable),
        "n_total_scenarios": len(rows),
        "min_market_for_profit_uf_m2": profitable[0]["market_uf_m2"] if profitable else None,
        "sweep": [
            {
                "market_pct_change": f"{r['market_pct_change']*100:+.0f}%",
                "market_uf_m2": round(r["market_uf_m2"], 2),
                "roi_pct": round(r["roi"] * 100, 1),
                "max_bid_20pct": round(r["max_bid_20pct"], 1),
                "profitable": bool(r["profitable"]),
            }
            for r in rows
        ],
    }


def compare_assets(asset_ids: list, scenario: str = "estandar") -> list:
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return [{"error": str(e)}]
    results = []
    for aid in asset_ids:
        if aid not in TIER_A_BY_ID:
            results.append({"asset_id": aid, "error": "No encontrado"})
            continue
        a = TIER_A_BY_ID[aid]
        r = FinancialEngine.run(a, params)
        bid_20 = FinancialEngine.max_bid(a, params, 0.20)
        results.append({
            "asset_id": aid,
            "name": a.name,
            "city": a.city,
            "m2": a.m2,
            "catalog_base_uf": a.catalog_base_uf,
            "fair_value_adjusted": round(a.fair_value_adjusted, 1),
            "roi_at_catalog": round(r.roi * 100, 1),
            "max_bid_20pct_roi": round(bid_20, 1),
            "profit_at_catalog": round(r.profit, 1),
            "score": a.score,
            "auction_date": str(a.auction_date),
            "days_to_auction": a.days_to_auction,
            "comparable_confidence": a.comparable_confidence,
        })
    return results


def optimize_capital(budget_uf: float, scenario: str = "estandar", target_roi_pct: float = 20.0) -> dict:
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}
    roi = target_roi_pct / 100.0
    result = CapitalAllocator.allocate(TIER_A, params, budget_uf, roi)
    return {
        "budget_uf": budget_uf,
        "scenario": scenario,
        "target_roi_pct": target_roi_pct,
        "selected_asset_ids": result["selected_ids"],
        "excluded_asset_ids": result["excluded_ids"],
        "bids": {k: round(v, 1) for k, v in result["bids"].items()},
        "profits": {k: round(v, 1) for k, v in result["profits"].items()},
        "total_deployed_uf": round(result["total_deployed_uf"], 1),
        "total_profit_uf": round(result["total_profit_uf"], 1),
        "remaining_capital_uf": round(result["remaining_capital_uf"], 1),
        "portfolio_roi_pct": round(result["portfolio_roi"] * 100, 1),
    }


def analyze_same_day_conflict(budget_uf: float, scenario: str = "estandar", target_roi_pct: float = 20.0) -> dict:
    try:
        params = _get_scenario(scenario)
    except ValueError as e:
        return {"error": str(e)}
    roi = target_roi_pct / 100.0
    conflicts = CapitalAllocator.same_day_conflict(TIER_A, params, budget_uf, roi)
    output = {}
    for dt_str, c in conflicts.items():
        output[dt_str] = {
            "auction_date": dt_str,
            "assets": [{"id": a.id, "name": a.name, "city": a.city} for a in c["assets"]],
            "bids": {k: round(v, 1) for k, v in c["bids"].items()},
            "capital_if_win_all": round(c["capital_if_win_all"], 1),
            "capital_needed_to_bid_all": round(c["capital_needed_to_bid_all"], 1),
            "can_bid_all_with_budget": c["can_bid_all_with_budget"],
            "can_win_all_with_budget": c["can_win_all_with_budget"],
            "recommendation": (
                "Puedes licitar ambos. Si ganas solo uno, el capital queda libre para futuros remates."
                if c["can_bid_all_with_budget"] else
                "Capital insuficiente para licitar ambos simultáneamente. Debes priorizar uno."
            ),
        }
    if not output:
        return {"message": "No hay conflictos de fecha en los activos actuales.", "conflicts": {}}
    return {"conflicts": output}


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "list_assets":              list_assets,
    "get_asset_detail":         get_asset_detail,
    "analyze_asset":            analyze_asset,
    "run_all_scenarios":        run_all_scenarios,
    "get_bid_table":            get_bid_table,
    "get_max_bid":              get_max_bid,
    "get_recommended_bid":      get_recommended_bid,
    "run_monte_carlo":          run_monte_carlo,
    "tornado_analysis":         tornado_analysis,
    "market_price_sweep":       market_price_sweep,
    "compare_assets":           compare_assets,
    "optimize_capital":         optimize_capital,
    "analyze_same_day_conflict": analyze_same_day_conflict,
}


class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
        except ImportError:
            pass
        return super().default(obj)


def dispatch(tool_name: str, tool_input: dict) -> str:
    fn = TOOL_FUNCTIONS.get(tool_name)
    if fn is None:
        return json.dumps({"error": f"Herramienta desconocida: {tool_name}"})
    try:
        result = fn(**tool_input)
        return json.dumps(result, ensure_ascii=False, indent=2, cls=_NumpyEncoder)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Claude API tool definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "list_assets",
        "description": "Lista todos los activos inmobiliarios disponibles (Tier A) con sus datos principales: id, nombre, ciudad, m², precio catálogo, valor de mercado, fecha de remate, score y notas.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_asset_detail",
        "description": "Devuelve los datos completos de un activo específico incluyendo ajuste de baños, fair value, descuento vs catálogo, confianza de comparables y alerta de sanity check.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo (ej: '77833')"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "analyze_asset",
        "description": "Ejecuta el motor financiero para un activo bajo un escenario de renovación dado y retorna el desglose completo: costos, venta neta, profit y ROI.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"], "description": "Escenario de renovación (default: estandar)"},
                "entry_price_uf": {"type": "number", "description": "Precio de entrada en UF (default: precio base catálogo)"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "run_all_scenarios",
        "description": "Ejecuta los 4 escenarios de renovación (cosmetico, estandar, deteriorado, stress) para un activo y retorna resultados comparados de ROI y profit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo"},
                "entry_price_uf": {"type": "number", "description": "Precio de entrada en UF (default: precio base catálogo)"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "get_bid_table",
        "description": "Genera la tabla completa de ofertas máximas para distintos targets de ROI (10%, 15%, 20%, 25%, 30%) bajo un escenario dado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"], "description": "Escenario de renovación (default: estandar)"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "get_max_bid",
        "description": "Calcula la oferta máxima para alcanzar un ROI objetivo específico bajo un escenario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"]},
                "target_roi_pct": {"type": "number", "description": "ROI objetivo en porcentaje (ej: 20 para 20%). Default: 20"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "get_recommended_bid",
        "description": "Retorna la estrategia de licitación recomendada: oferta de apertura (30% ROI), objetivo (25% ROI), aceptable (20% ROI) y mínimo (15% ROI).",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"]},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "run_monte_carlo",
        "description": "Ejecuta simulación Monte Carlo para estimar la distribución de ROI bajo incertidumbre en precio de mercado, renovación, deudas ocultas, holding y precio de venta.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"]},
                "entry_price_uf": {"type": "number", "description": "Precio de entrada en UF"},
                "n_simulations": {"type": "integer", "description": "Número de simulaciones (default: 5000)"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "tornado_analysis",
        "description": "Análisis tornado: rankea las variables por su impacto en el ROI (±1 desviación estándar). Muestra qué factores son más críticos para la rentabilidad.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"]},
                "entry_price_uf": {"type": "number", "description": "Precio de entrada en UF"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "market_price_sweep",
        "description": "Barre el precio de mercado (UF/m²) entre -30% y +30% para mostrar cómo cambia el ROI y la oferta máxima a 20% ROI según el precio de mercado real.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID del activo"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"]},
                "entry_price_uf": {"type": "number", "description": "Precio de entrada en UF"},
            },
            "required": ["asset_id"],
        },
    },
    {
        "name": "compare_assets",
        "description": "Compara múltiples activos lado a lado: ROI al precio catálogo, oferta máxima a 20% ROI, profit, fecha de remate y score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de IDs de activos a comparar (ej: ['77833', '77948', '77947'])",
                },
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"]},
            },
            "required": ["asset_ids"],
        },
    },
    {
        "name": "optimize_capital",
        "description": "Dado un capital disponible en UF, encuentra el subconjunto óptimo de activos para maximizar profit total respetando el presupuesto.",
        "input_schema": {
            "type": "object",
            "properties": {
                "budget_uf": {"type": "number", "description": "Capital disponible total en UF"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"]},
                "target_roi_pct": {"type": "number", "description": "ROI objetivo para calcular ofertas (default: 20)"},
            },
            "required": ["budget_uf"],
        },
    },
    {
        "name": "analyze_same_day_conflict",
        "description": "Analiza conflictos de capital para activos que rematan el mismo día. Calcula el capital necesario para licitar ambos vs ganar ambos y da recomendaciones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "budget_uf": {"type": "number", "description": "Capital disponible en UF"},
                "scenario": {"type": "string", "enum": ["cosmetico", "estandar", "deteriorado", "stress"]},
                "target_roi_pct": {"type": "number", "description": "ROI objetivo (default: 20)"},
            },
            "required": ["budget_uf"],
        },
    },
]
