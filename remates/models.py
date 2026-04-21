"""
Core data models — recalibrated for real Chilean remate execution.

KEY INSIGHT: Renovation is a function of m², not purchase price.
A 59m² apartment costs ~265 UF to renovate regardless of whether you
paid 700 UF or 900 UF for it. The old model (renovation = % of entry)
was underestimating costs by 3x and inflating ROI to unrealistic levels.

Revised cost structure:
  proportional  = entry_price × (1 + fixed_cost_rate + monthly_carrying_rate × months)
  renovation_uf = renovation_uf_per_m2 × asset.m2
  hidden_debts  = contribuciones + gastos_comunes atrasados (absolute UF)
  total_cost    = proportional + renovation_uf + hidden_debts

  net_sale      = fair_value × bath_adj × appreciation_factor × sale_pct × (1 - selling_costs_pct)
  profit        = net_sale - total_cost
  roi           = profit / total_cost

  max_bid(roi)  = (net_sale/(1+roi) - renovation_uf - hidden_debts) / (1 + fixed_rate + monthly_rate×months)

Calibrated constants (Chile 2025-2026):
  fixed_cost_rate       = 0.045   (4.5%: martillero 1.5% + notaría/CBR 2% + otros 1%)
  monthly_carrying_rate = 0.0075  (0.75%/month: gastos comunes + contrib. + seguro)
  selling_costs_pct     = 0.035   (3.5%: corretaje 2% + IVA 0.38% + notaría/CBR 1.12%)
  renovation_uf_per_m2  = 4.5     (UF/m²: renovación media para propiedad en remate)
  hidden_debts_uf       = 100     (UF: contrib. atrasadas + gastos comunes ~18 meses)
  bathroom_adjustment   = 0.88    (3d+/1b vs comp set 3d/2b)
"""

from __future__ import annotations
from dataclasses import dataclass
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

    @property
    def days_to_auction(self) -> int:
        return (self.auction_date - date.today()).days

    @property
    def is_urgent(self) -> bool:
        return self.days_to_auction <= 10

    @property
    def comparable_confidence(self) -> str:
        if self.n_comparables >= 8:
            return "ALTA"
        elif self.n_comparables >= 5:
            return "MEDIA"
        return "BAJA"

    @property
    def comparable_uncertainty_std(self) -> float:
        if self.n_comparables >= 8:
            return 0.12
        elif self.n_comparables >= 5:
            return 0.16
        return 0.22

    @property
    def bathroom_adjustment(self) -> float:
        """3d+/1b underperforms comparable 2-bath set by ~12%."""
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
        return 1.0 - self.catalog_base_uf / self.fair_value_adjusted

    @property
    def entry_uf_per_m2(self) -> float:
        return self.catalog_base_uf / self.m2

    def label(self) -> str:
        return (
            f"#{self.id} {self.name} | {self.city} | "
            f"{self.bedrooms}d/{self.bathrooms}b/{self.parking}e | {self.m2}m²"
        )


@dataclass
class ScenarioParams:
    name: str
    holding_months: int
    sale_pct_market: float
    appreciation_annual: float
    # Absolute costs (independent of entry price)
    renovation_uf_per_m2: float       # UF per m² — calibrated to Chilean market
    hidden_debts_uf: float            # gastos comunes + contribuciones atrasadas
    # Proportional costs (% of entry price)
    fixed_cost_rate: float = 0.045    # martillero + notaría + CBR at entry
    monthly_carrying_rate: float = 0.0075
    selling_costs_pct: float = 0.035  # corretaje + IVA + notaría/CBR at exit
    use_adjusted_fair_value: bool = True

    @property
    def proportional_multiplier(self) -> float:
        """Entry-proportional part of cost multiplier (excludes renovation + hidden debts)."""
        return 1 + self.fixed_cost_rate + self.monthly_carrying_rate * self.holding_months


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

    # --- Cost breakdown ---

    @property
    def cost_entry_proportional(self) -> float:
        """Entry price + transaction costs at entry (proportional to bid)."""
        return self.entry_price * self.params.proportional_multiplier

    @property
    def cost_renovation(self) -> float:
        return self.params.renovation_uf_per_m2 * self.asset.m2

    @property
    def cost_hidden_debts(self) -> float:
        return self.params.hidden_debts_uf

    @property
    def total_costs(self) -> float:
        return self.cost_entry_proportional + self.cost_renovation + self.cost_hidden_debts

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
        if self.total_costs == 0:
            return 0.0
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
    prob_above_15pct: float
    prob_above_20pct: float
    prob_above_30pct: float
    prob_loss: float
    raw_rois: object  # np.ndarray
