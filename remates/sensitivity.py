"""
Sensitivity analysis and Monte Carlo — recalibrated for absolute cost model.
"""

from __future__ import annotations
import copy
from typing import List, Tuple

import numpy as np

from .models import Asset, ScenarioParams, ScenarioResult, MonteCarloResult
from .engine import FinancialEngine


class SensitivityAnalyzer:

    @staticmethod
    def market_price_sweep(
        asset: Asset,
        params: ScenarioParams,
        entry_price: float,
        pct_range: Tuple[float, float] = (-0.30, 0.30),
        steps: int = 25,
    ) -> List[dict]:
        results = []
        for pct in np.linspace(pct_range[0], pct_range[1], steps):
            mod = copy.copy(asset)
            mod.market_uf_per_m2_raw = asset.market_uf_per_m2_raw * (1 + pct)
            r = ScenarioResult(asset=mod, params=params, entry_price=entry_price)
            results.append({
                "market_pct_change": float(pct),
                "market_uf_m2":      float(mod.market_uf_per_m2_raw),
                "fair_value":        float(r.fair_value),
                "net_sale":          float(r.net_sale),
                "roi":               float(r.roi),
                "max_bid_20pct":     float(FinancialEngine.max_bid(mod, params, 0.20)),
                "profitable":        r.roi > 0,
            })
        return results

    @staticmethod
    def tornado(
        asset: Asset,
        params: ScenarioParams,
        entry_price: float,
    ) -> List[dict]:
        """
        Tornado: impact on ROI of ±1 std deviation in each key variable.
        Variables ranked by total swing (highest impact first).
        """
        base = ScenarioResult(asset=asset, params=params, entry_price=entry_price)
        base_roi = base.roi

        # (var_key, label, swing_magnitude, target, swing_mode)
        # swing_mode: "relative" or "absolute"
        variables = [
            ("market_uf_per_m2_raw", "UF/m² mercado",       0.20,  "asset",   "relative"),
            ("hidden_debts_uf",      "Deudas ocultas (UF)",  0.50,  "params",  "relative"),  # ±50%
            ("renovation_uf_per_m2", "Refacción (UF/m²)",   0.30,  "params",  "relative"),
            ("holding_months",       "Meses de holding",    0.375, "params",  "relative"),  # 8m ± 3m
            ("sale_pct_market",      "% mercado en venta",  0.08,  "params",  "absolute"),  # ±8pp
        ]

        rows = []
        for var_key, label, swing, target, mode in variables:
            mod_asset_hi = copy.copy(asset)
            mod_asset_lo = copy.copy(asset)
            mod_params_hi = copy.copy(params)
            mod_params_lo = copy.copy(params)

            if target == "asset":
                base_val = getattr(asset, var_key)
                if mode == "relative":
                    hi_val = base_val * (1 + swing)
                    lo_val = base_val * (1 - swing)
                else:
                    hi_val = base_val + swing
                    lo_val = base_val - swing
                setattr(mod_asset_hi, var_key, hi_val)
                setattr(mod_asset_lo, var_key, lo_val)
                roi_hi = ScenarioResult(asset=mod_asset_hi, params=params, entry_price=entry_price).roi
                roi_lo = ScenarioResult(asset=mod_asset_lo, params=params, entry_price=entry_price).roi
            else:
                base_val = getattr(params, var_key)
                if mode == "relative":
                    hi_val = base_val * (1 + swing)
                    lo_val = max(0.0, base_val * (1 - swing))
                else:
                    hi_val = min(1.0, base_val + swing)
                    lo_val = max(0.0, base_val - swing)

                if var_key == "holding_months":
                    hi_val = max(1, int(round(hi_val)))
                    lo_val = max(1, int(round(lo_val)))

                setattr(mod_params_hi, var_key, hi_val)
                setattr(mod_params_lo, var_key, lo_val)
                roi_hi = ScenarioResult(asset=asset, params=mod_params_hi, entry_price=entry_price).roi
                roi_lo = ScenarioResult(asset=asset, params=mod_params_lo, entry_price=entry_price).roi

            rows.append({
                "variable":    var_key,
                "label":       label,
                "base_roi":    base_roi,
                "roi_high":    roi_hi,
                "roi_low":     roi_lo,
                "impact_high": roi_hi - base_roi,
                "impact_low":  roi_lo - base_roi,
                "total_swing": abs(roi_hi - roi_lo),
            })

        return sorted(rows, key=lambda x: x["total_swing"], reverse=True)

    @staticmethod
    def monte_carlo(
        asset: Asset,
        params: ScenarioParams,
        entry_price: float,
        n_simulations: int = 10_000,
        seed: int = 42,
    ) -> MonteCarloResult:
        """
        Monte Carlo sampling:
          - Market price: N(0, sigma) proportional shock
          - Renovation overrun: N(0, 20%) proportional shock on UF/m²
          - Hidden debts: N(0, 40%) proportional shock
          - Holding extension: Uniform(-1, +4) additional months
          - Sale price: N(0, 5pp) additive shock on sale_pct_market
        """
        rng = np.random.default_rng(seed)
        market_sigma = asset.comparable_uncertainty_std

        market_shocks  = rng.normal(0, market_sigma, n_simulations)
        reno_shocks    = rng.normal(0, 0.20, n_simulations)
        debt_shocks    = rng.normal(0, 0.40, n_simulations)
        holding_extra  = rng.integers(-1, 5, n_simulations)
        sale_shocks    = rng.normal(0, 0.05, n_simulations)

        rois = np.empty(n_simulations)

        for i in range(n_simulations):
            mod_a = copy.copy(asset)
            mod_p = copy.copy(params)

            mod_a.market_uf_per_m2_raw = max(0.1, asset.market_uf_per_m2_raw * (1 + market_shocks[i]))
            mod_p.renovation_uf_per_m2 = max(1.0, params.renovation_uf_per_m2 * (1 + reno_shocks[i]))
            mod_p.hidden_debts_uf      = max(0.0, params.hidden_debts_uf * (1 + debt_shocks[i]))
            mod_p.holding_months       = max(1, params.holding_months + int(holding_extra[i]))
            mod_p.sale_pct_market      = float(np.clip(params.sale_pct_market + sale_shocks[i], 0.60, 1.05))

            rois[i] = ScenarioResult(asset=mod_a, params=mod_p, entry_price=entry_price).roi

        return MonteCarloResult(
            asset=asset,
            params=params,
            entry_price=entry_price,
            n_simulations=n_simulations,
            mean_roi=float(np.mean(rois)),
            median_roi=float(np.median(rois)),
            std_roi=float(np.std(rois)),
            p5_roi=float(np.percentile(rois, 5)),
            p25_roi=float(np.percentile(rois, 25)),
            p75_roi=float(np.percentile(rois, 75)),
            p95_roi=float(np.percentile(rois, 95)),
            prob_positive=float(np.mean(rois > 0)),
            prob_above_15pct=float(np.mean(rois > 0.15)),
            prob_above_20pct=float(np.mean(rois > 0.20)),
            prob_above_30pct=float(np.mean(rois > 0.30)),
            prob_loss=float(np.mean(rois < 0)),
            raw_rois=rois,
        )
