"""
Sensitivity analysis and Monte Carlo simulation engine.
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
        """ROI as a function of market UF/m² deviation from base estimate."""
        results = []
        for pct in np.linspace(pct_range[0], pct_range[1], steps):
            mod = copy.copy(asset)
            mod.market_uf_per_m2_raw = asset.market_uf_per_m2_raw * (1 + pct)
            r = ScenarioResult(asset=mod, params=params, entry_price=entry_price)
            results.append({
                "market_pct_change": float(pct),
                "market_uf_m2": float(mod.market_uf_per_m2_raw),
                "fair_value": float(r.fair_value),
                "net_sale": float(r.net_sale),
                "roi": float(r.roi),
                "max_bid_30pct": float(FinancialEngine.max_bid(mod, params, 0.30)),
                "profitable": r.roi > 0,
            })
        return results

    @staticmethod
    def tornado(
        asset: Asset,
        params: ScenarioParams,
        entry_price: float,
    ) -> List[dict]:
        """
        Tornado analysis: impact of ±1 std deviation change in each key variable.
        Variables ranked by absolute ROI impact (highest first).
        """
        base = ScenarioResult(asset=asset, params=params, entry_price=entry_price)
        base_roi = base.roi

        variables = [
            ("market_uf_per_m2_raw", "UF/m² mercado",      0.20,  "asset"),
            ("holding_months",       "Meses de holding",   0.286, "params"),
            ("renovation_pct",       "Costo de refacción", 0.25,  "params"),
            ("sale_pct_market",      "% mercado en venta", 0.114, "params"),
            ("appreciation_annual",  "Apreciación anual",  1.0,   "params"),
        ]

        rows = []
        for var_key, label, swing, target in variables:
            mod_asset_hi = copy.copy(asset)
            mod_asset_lo = copy.copy(asset)
            mod_params_hi = copy.copy(params)
            mod_params_lo = copy.copy(params)

            if target == "asset":
                base_val = asset.market_uf_per_m2_raw  # only asset-level var currently
                mod_asset_hi.market_uf_per_m2_raw = base_val * (1 + swing)
                mod_asset_lo.market_uf_per_m2_raw = base_val * (1 - swing)
                roi_hi = ScenarioResult(asset=mod_asset_hi, params=params, entry_price=entry_price).roi
                roi_lo = ScenarioResult(asset=mod_asset_lo, params=params, entry_price=entry_price).roi
            else:
                base_val = getattr(params, var_key)
                if var_key == "holding_months":
                    # swing is fraction of base value, but months are integers
                    hi_val = max(1, int(base_val * (1 + swing)))
                    lo_val = max(1, int(base_val * (1 - swing)))
                elif var_key == "sale_pct_market":
                    hi_val = min(1.0, base_val * (1 + swing))
                    lo_val = max(0.0, base_val * (1 - swing))
                elif var_key == "appreciation_annual":
                    # base is 0%, swing in absolute terms
                    hi_val = 0.015
                    lo_val = -0.015
                else:
                    hi_val = base_val * (1 + swing)
                    lo_val = max(0.0, base_val * (1 - swing))
                setattr(mod_params_hi, var_key, hi_val)
                setattr(mod_params_lo, var_key, lo_val)
                roi_hi = ScenarioResult(asset=asset, params=mod_params_hi, entry_price=entry_price).roi
                roi_lo = ScenarioResult(asset=asset, params=mod_params_lo, entry_price=entry_price).roi

            rows.append({
                "variable": var_key,
                "label": label,
                "base_roi": base_roi,
                "roi_high": roi_hi,
                "roi_low": roi_lo,
                "impact_high": roi_hi - base_roi,
                "impact_low": roi_lo - base_roi,
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
        Monte Carlo simulation sampling:
          - Market price shock: N(0, sigma) where sigma depends on n_comparables
          - Holding extension: Uniform(-1, +3) additional months
          - Renovation overrun: N(0, 5%) additive shock to renovation_pct
          - Sale price discount: N(0, 4%) additive shock to sale_pct_market
        """
        rng = np.random.default_rng(seed)
        market_sigma = asset.comparable_uncertainty_std

        # Draw all shocks at once for speed
        market_shocks   = rng.normal(0, market_sigma, n_simulations)
        holding_extra   = rng.integers(-1, 4, n_simulations)      # -1 to +3 months
        reno_shocks     = rng.normal(0, 0.05, n_simulations)
        sale_shocks     = rng.normal(0, 0.04, n_simulations)

        rois = np.empty(n_simulations)

        for i in range(n_simulations):
            mod_a = copy.copy(asset)
            mod_p = copy.copy(params)

            mod_a.market_uf_per_m2_raw = asset.market_uf_per_m2_raw * (1 + market_shocks[i])
            mod_p.holding_months = max(1, params.holding_months + int(holding_extra[i]))
            mod_p.renovation_pct = max(0.0, params.renovation_pct + reno_shocks[i])
            mod_p.sale_pct_market = np.clip(params.sale_pct_market + sale_shocks[i], 0.60, 1.05)

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
            prob_above_20pct=float(np.mean(rois > 0.20)),
            prob_above_30pct=float(np.mean(rois > 0.30)),
            prob_loss=float(np.mean(rois < 0)),
            raw_rois=rois,
        )
