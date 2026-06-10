"""Scenario engine + tail-risk measures.

Two roles:

1. **Scenario P&L** -- apply parallel spread shocks (and matching hedge moves)
   to a :class:`fallen_angels.portfolio.Portfolio`, reusing the linear DV01
   approximation from ``portfolio.py``. Grids are parameterized by
   ``config.risk``.

2. **Tail measures** -- VaR / CVaR for any returns series, typically the
   ``excess`` column from :mod:`fallen_angels.backtest`, used to size the live
   trade against the historical distribution.
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from fallen_angels.config import TradeConfig
from fallen_angels.portfolio import (
    Portfolio,
    Position,
    bond_price_pnl,
    bond_spread_attribution,
    hedge_price_pnl,
    position_carry,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Shocks
# --------------------------------------------------------------------------- #
def shocked_bond_price(
    prev_price: float,
    modified_duration: float | None,
    spread_shock_bps: float,
) -> float:
    """Linear shocked clean price: ``P * (1 - ModDur * dSpread_bps / 10000)``."""
    if modified_duration is None:
        return prev_price
    return prev_price * (1.0 - modified_duration * spread_shock_bps / 10000.0)


def shock_position(
    position: Position,
    baseline_price: float,
    baseline_spread: float | None,
    *,
    spread_shock_bps: float = 0.0,
    hedge_shock_pct: float = 0.0,
    days: float = 0.0,
) -> dict[str, float]:
    """Single-position P&L under one (spread, hedge) shock.

    Bonds use the DV01 approximation; the hedge moves by ``hedge_shock_pct``.
    ``days > 0`` adds bond carry (instantaneous shocks: leave it at 0).
    """
    if position.kind == "bond":
        new_price = shocked_bond_price(
            baseline_price, position.modified_duration, spread_shock_bps
        )
        price_pnl = bond_price_pnl(position, baseline_price, new_price)
        if baseline_spread is not None:
            spread_pnl = bond_spread_attribution(
                position, baseline_price, baseline_spread, baseline_spread + spread_shock_bps,
            )
        else:
            spread_pnl = 0.0
        carry = position_carry(position, days)
        return {
            "carry": carry,
            "spread_pnl": spread_pnl,
            "benchmark_pnl": price_pnl - spread_pnl,
            "price_pnl": price_pnl,
            "hedge_pnl": 0.0,
            "total_pnl": carry + price_pnl,
        }
    new_price = baseline_price * (1.0 + hedge_shock_pct)
    hedge_pnl = hedge_price_pnl(position, baseline_price, new_price)
    return {
        "carry": 0.0,
        "spread_pnl": 0.0,
        "benchmark_pnl": 0.0,
        "price_pnl": 0.0,
        "hedge_pnl": hedge_pnl,
        "total_pnl": hedge_pnl,
    }


def scenario_pnl(
    portfolio: Portfolio,
    marks: pd.DataFrame,
    *,
    spread_shock_bps: float,
    hedge_shock_pct: float,
    days: float = 0.0,
) -> pd.DataFrame:
    """Per-position P&L for one (spread, hedge) scenario.

    Marks frame: index=security, ``price`` required, ``zspread`` optional
    (enables spread attribution on the bond legs).
    """
    rows = []
    for p in portfolio.positions:
        if p.security not in marks.index:
            raise KeyError(
                f"scenario_pnl: marks missing security {p.security!r}. Build the "
                f"marks frame via portfolio.marks_from_tidy first."
            )
        baseline = float(marks.at[p.security, "price"])
        baseline_spread = None
        if "zspread" in marks.columns:
            raw = marks.at[p.security, "zspread"]
            if not pd.isna(raw):
                baseline_spread = float(raw)
        components = shock_position(
            p, baseline, baseline_spread,
            spread_shock_bps=spread_shock_bps,
            hedge_shock_pct=hedge_shock_pct,
            days=days,
        )
        rows.append({
            "position": p.display, "kind": p.kind, "side": p.side,
            "spread_shock_bps": spread_shock_bps, "hedge_shock_pct": hedge_shock_pct,
            **components,
        })
    return pd.DataFrame(rows)


def _hedge_pct_from_spread(
    portfolio: Portfolio, spread_shock_bps: float, beta: float
) -> float:
    """Translate an issuer spread shock to HYG % move using the hedge's duration.

    ``HYG_OAS_move ≈ beta * issuer_spread_move``, then
    ``HYG_price_pct ≈ -hedge_dur * HYG_OAS_move / 10000``. Falls back to
    treating the shock directly as a % if no hedge duration is set.
    """
    hedges = portfolio.by_kind("hedge")
    hedge_dur = hedges[0].modified_duration if hedges else None
    if hedge_dur is None:
        return -beta * spread_shock_bps / 10000.0
    return -hedge_dur * beta * spread_shock_bps / 10000.0


def parallel_spread_grid(
    portfolio: Portfolio,
    marks: pd.DataFrame,
    cfg: TradeConfig,
    *,
    shocks_bps: Sequence[float] | None = None,
    hedge_beta: float | None = None,
    days: float = 0.0,
) -> pd.DataFrame:
    """P&L grid over a parallel-spread shock vector with auto-hedge co-movement.

    Returns one row per shock with aggregated P&L components (carry, spread,
    benchmark, price, hedge, total) ready for plotly waterfall / bar charts.
    """
    shocks = list(shocks_bps if shocks_bps is not None else cfg.risk.parallel_shocks_bps)
    beta = cfg.risk.hedge_beta_to_issuer if hedge_beta is None else hedge_beta

    rows = []
    for shock in shocks:
        hedge_pct = _hedge_pct_from_spread(portfolio, shock, beta)
        per_pos = scenario_pnl(
            portfolio, marks,
            spread_shock_bps=shock, hedge_shock_pct=hedge_pct, days=days,
        )
        rows.append({
            "scenario": f"spread_{int(shock):+d}bps",
            "spread_shock_bps": shock,
            "hedge_shock_pct": hedge_pct,
            "carry": per_pos["carry"].sum(),
            "spread_pnl": per_pos["spread_pnl"].sum(),
            "benchmark_pnl": per_pos["benchmark_pnl"].sum(),
            "price_pnl": per_pos["price_pnl"].sum(),
            "hedge_pnl": per_pos["hedge_pnl"].sum(),
            "total_pnl": per_pos["total_pnl"].sum(),
        })
    return pd.DataFrame(rows)


def joint_scenario_grid(
    portfolio: Portfolio,
    marks: pd.DataFrame,
    cfg: TradeConfig,
    *,
    spread_shocks_bps: Sequence[float] | None = None,
    hedge_shocks_pct: Sequence[float] = (-0.10, -0.05, 0.0, 0.05, 0.10),
    days: float = 0.0,
) -> pd.DataFrame:
    """Independent two-way stress: spread x hedge surface of total P&L."""
    shocks = list(spread_shocks_bps if spread_shocks_bps is not None else cfg.risk.parallel_shocks_bps)
    rows = []
    for s in shocks:
        for h in hedge_shocks_pct:
            per_pos = scenario_pnl(
                portfolio, marks, spread_shock_bps=s, hedge_shock_pct=h, days=days,
            )
            rows.append({
                "spread_shock_bps": s,
                "hedge_shock_pct": h,
                "total_pnl": per_pos["total_pnl"].sum(),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Tail measures
# --------------------------------------------------------------------------- #
def value_at_risk(returns: pd.Series | np.ndarray, alpha: float = 0.05) -> float:
    """Historical VaR (returns convention: losses are negative)."""
    arr = pd.Series(returns).dropna()
    if arr.empty:
        return float("nan")
    return float(arr.quantile(alpha))


def conditional_var(returns: pd.Series | np.ndarray, alpha: float = 0.05) -> float:
    """Historical CVaR: mean return in the worst ``alpha`` tail."""
    arr = pd.Series(returns).dropna()
    if arr.empty:
        return float("nan")
    cutoff = arr.quantile(alpha)
    tail = arr[arr <= cutoff]
    return float(tail.mean()) if not tail.empty else float("nan")


def tail_metrics(
    returns: pd.Series | np.ndarray,
    alphas: Iterable[float] = (0.05, 0.10),
) -> dict[str, float]:
    """VaR + CVaR at multiple alphas, plus n / mean / min for context."""
    arr = pd.Series(returns).dropna()
    out: dict[str, float] = {
        "n": float(len(arr)),
        "mean": float(arr.mean()) if not arr.empty else float("nan"),
        "min": float(arr.min()) if not arr.empty else float("nan"),
    }
    for a in alphas:
        out[f"var_{int(round(a * 100)):02d}"] = value_at_risk(arr, a)
        out[f"cvar_{int(round(a * 100)):02d}"] = conditional_var(arr, a)
    return out


def backtest_tail_metrics(
    results: pd.DataFrame,
    column: str = "excess",
    alphas: Iterable[float] = (0.05, 0.10),
) -> dict[str, float]:
    """Tail metrics on a backtest results frame (default: the ``excess`` column)."""
    if column not in results.columns:
        raise KeyError(
            f"backtest_tail_metrics: column {column!r} not in results "
            f"({list(results.columns)})."
        )
    return tail_metrics(results[column], alphas=alphas)
