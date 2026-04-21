"""
Core data models for institutional-grade auction real estate analysis.

Financial model (reverse-engineered from empirical Chilean auction data):
  k           = 1 + renovation_pct + fixed_cost_rate + monthly_carrying_rate * months
  total_cost  = entry_price * k
  net_sale    = fair_value * appreciation_factor * sale_pct * (1 - selling_costs_pct)
  profit      = net_sale - total_cost
  roi         = profit / total_cost
  max_bid     = net_sale / ((1 + target_roi) * k)

Calibrated constants:
  fixed_cost_rate      = 0.0477   (~4.77% of entry: notaría, inscripción, IVA)
  monthly_carrying_rate = 0.0075  (0.75%/month: gastos comunes, contrib., seguro)
  selling_costs_pct    = 0.03     (3% exit: corretaje + notaría)
  bathroom_adjustment  = 0.88     (12% haircut for 3d+/1b vs comparable 2b set)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Asset:
    id: str
    name: str
    address: str
    city: str
    bedrooms: int
    bathrooms: int
    parking: int
    m2: float
    catalog_base_uf: float
    market_uf_per_m2_raw: float
    n_comparables: int
    auction_date: date
    score: int
    notes: str = ""

    # --- Derived: location ---

    @property
    def days_to_auction(self) -> int:
        return (self.auction_date - date.today()).days

    @property
    def is_urgent(self) -> bool:
        return self.days_to_auction <= 10

    # --- Derived: market quality ---

    @property
    def comparable_confidence(self) -> str:
        if self.n_comparables >= 8:
            return "ALTA"
        elif self.n_comparables >= 5:
            return "MEDIA"
        return "BAJA"

    @property
    def comparable_uncertainty_std(self) -> float:
        """Std dev for Monte Carlo market price shock, based on comp count."""
        if self.n_comparables >= 8:
            return 0.12
        elif self.n_comparables >= 5:
            return 0.16
        return 0.22

    # --- Derived: bathroom adjustment ---

    @property
    def bathroom_adjustment(self) -> float:
        """Structural discount: 3+ beds with only 1 bath underperforms comp set."""
        if self.bedrooms >= 3 and self.bathrooms == 1:
            return 0.88
        return 1.0

    @property
    def market_uf_per_m2_adjusted(self) -> float:
        return self.market_uf_per_m2_raw * self.bathroom_adjustment

    @property
    def fair_value_raw(self) -> float:
        return self.m2 * self.market_uf_per_m2_raw

    @property
    def fair_value_adjusted(self) -> float:
        return self.m2 * self.market_uf_per_m2_adjusted

    @property
    def discount_vs_catalog(self) -> float:
        """Discount of catalog base price vs adjusted fair value."""
        return 1.0 - self.catalog_base_uf / self.fair_value_adjusted

    @property
    def entry_uf_per_m2(self) -> float:
        return self.catalog_base_uf / self.m2

    def label(self) -> str:
        return f"#{self.id} {self.name} | {self.city} | {self.bedrooms}d/{self.bathrooms}b/{self.parking}e | {self.m2}m²"


@dataclass
class ScenarioParams:
    name: str
    holding_months: int
    renovation_pct: float
    sale_pct_market: float
    appreciation_annual: float
    selling_costs_pct: float = 0.03
    fixed_cost_rate: float = 0.0477
    monthly_carrying_rate: float = 0.0075
    use_adjusted_fair_value: bool = True

    @property
    def cost_multiplier(self) -> float:
        """k factor: total_cost = entry_price * k"""
        return (
            1
            + self.renovation_pct
            + self.fixed_cost_rate
            + self.monthly_carrying_rate * self.holding_months
        )


@dataclass
class ScenarioResult:
    asset: Asset
    params: ScenarioParams
    entry_price: float

    @property
    def fair_value(self) -> float:
        if self.params.use_adjusted_fair_value:
            return self.asset.fair_value_adjusted
        return self.asset.fair_value_raw

    @property
    def appreciation_factor(self) -> float:
        return 1 + self.params.appreciation_annual * self.params.holding_months / 12

    @property
    def gross_sale(self) -> float:
        return self.fair_value * self.appreciation_factor * self.params.sale_pct_market

    @property
    def net_sale(self) -> float:
        return self.gross_sale * (1 - self.params.selling_costs_pct)

    @property
    def total_costs(self) -> float:
        return self.entry_price * self.params.cost_multiplier

    @property
    def profit(self) -> float:
        return self.net_sale - self.total_costs

    @property
    def roi(self) -> float:
        if self.total_costs == 0:
            return 0.0
        return self.profit / self.total_costs

    @property
    def discount_vs_fair(self) -> float:
        return 1.0 - self.entry_price / self.fair_value

    @property
    def entry_uf_per_m2(self) -> float:
        return self.entry_price / self.asset.m2

    @property
    def equity_multiple(self) -> float:
        return self.net_sale / self.total_costs


@dataclass
class MonteCarloResult:
    asset: Asset
    params: ScenarioParams
    entry_price: float
    n_simulations: int
    mean_roi: float
    median_roi: float
    std_roi: float
    p5_roi: float
    p25_roi: float
    p75_roi: float
    p95_roi: float
    prob_positive: float
    prob_above_20pct: float
    prob_above_30pct: float
    prob_loss: float
    raw_rois: object  # np.ndarray
