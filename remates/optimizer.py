"""
Bid optimizer and capital allocator across multiple auction assets.
"""

from __future__ import annotations
from typing import List, Dict
from itertools import combinations

from .models import Asset, ScenarioParams, ScenarioResult
from .engine import FinancialEngine


class BidOptimizer:

    @staticmethod
    def bid_table(asset: Asset, params: ScenarioParams) -> list[dict]:
        return FinancialEngine.build_bid_table(asset, params)

    @staticmethod
    def recommended_bid(asset: Asset, params: ScenarioParams) -> dict:
        """Returns the 30% ROI row (objective price) and 35% ROI row (opening bid)."""
        table = FinancialEngine.build_bid_table(asset, params)
        by_roi = {r["roi_target"]: r for r in table}
        return {
            "open_bid": by_roi[0.35],
            "target_bid": by_roi[0.30],
            "max_bid": by_roi[0.25],
        }


class CapitalAllocator:
    """
    Given a total capital budget, find the optimal subset of assets to bid on.

    Strategy: maximize total expected profit under conservative scenario,
    subject to total capital <= budget.
    Uses exhaustive search for small N (<=10 assets), greedy for larger sets.
    """

    @staticmethod
    def allocate(
        assets: List[Asset],
        params: ScenarioParams,
        budget_uf: float,
        target_roi: float = 0.30,
    ) -> Dict:
        bids = {a.id: FinancialEngine.max_bid(a, params, target_roi) for a in assets}
        profits = {
            a.id: ScenarioResult(asset=a, params=params, entry_price=bids[a.id]).profit
            for a in assets
        }

        best_combo = []
        best_profit = 0.0

        n = len(assets)
        ids = [a.id for a in assets]

        for r in range(1, n + 1):
            for combo in combinations(ids, r):
                total_capital = sum(bids[aid] for aid in combo)
                if total_capital <= budget_uf:
                    total_profit = sum(profits[aid] for aid in combo)
                    if total_profit > best_profit:
                        best_profit = total_profit
                        best_combo = list(combo)

        asset_map = {a.id: a for a in assets}
        selected = [asset_map[aid] for aid in best_combo]
        total_deployed = sum(bids[aid] for aid in best_combo)

        return {
            "selected_ids": best_combo,
            "selected_assets": selected,
            "bids": {aid: bids[aid] for aid in best_combo},
            "profits": {aid: profits[aid] for aid in best_combo},
            "total_deployed_uf": total_deployed,
            "total_profit_uf": best_profit,
            "remaining_capital_uf": budget_uf - total_deployed,
            "portfolio_roi": best_profit / total_deployed if total_deployed > 0 else 0,
            "excluded_ids": [aid for aid in ids if aid not in best_combo],
        }

    @staticmethod
    def same_day_conflict(
        assets: List[Asset],
        params: ScenarioParams,
        budget_uf: float,
        target_roi: float = 0.30,
    ) -> Dict:
        """
        For assets auctioning on the same day, analyze capital conflict.
        Bidding on multiple same-day auctions requires full capital for each
        (you don't know which you'll win), so effective capital requirement
        is max(bids) not sum(bids) — assuming sequential resolution.
        """
        from collections import defaultdict
        by_date = defaultdict(list)
        for a in assets:
            by_date[a.auction_date].append(a)

        conflicts = {}
        for dt, group in by_date.items():
            if len(group) > 1:
                bids = {a.id: FinancialEngine.max_bid(a, params, target_roi) for a in group}
                max_single = max(bids.values())
                sum_all = sum(bids.values())
                conflicts[str(dt)] = {
                    "assets": group,
                    "bids": bids,
                    "capital_if_win_all": sum_all,
                    "capital_needed_to_bid_all": max_single,
                    "can_bid_all_with_budget": max_single <= budget_uf,
                    "can_win_all_with_budget": sum_all <= budget_uf,
                }
        return conflicts
