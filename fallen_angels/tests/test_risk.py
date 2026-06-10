"""Offline tests for risk.py: shocks, scenario grids, VaR / CVaR."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from fallen_angels import portfolio as pf
from fallen_angels import risk


def _bond(**overrides) -> pf.Position:
    base = dict(
        security="CNC 2029 Corp", kind="bond", side="long",
        quantity=1_000_000, entry_date=date(2026, 4, 20), entry_price=100.0,
        entry_spread_bps=300.0, coupon_pct=5.0, modified_duration=5.0,
    )
    return pf.Position(**(base | overrides))


def _hedge(**overrides) -> pf.Position:
    base = dict(
        security="HYG US Equity", kind="hedge", side="short", quantity=10_000,
        entry_date=date(2026, 4, 20), entry_price=80.0, modified_duration=3.6,
    )
    return pf.Position(**(base | overrides))


def _marks() -> pd.DataFrame:
    return pd.DataFrame({
        "price": {"CNC 2029 Corp": 100.0, "HYG US Equity": 80.0},
        "zspread": {"CNC 2029 Corp": 300.0, "HYG US Equity": np.nan},
    })


# --------------------------------------------------------------------------- #
# Shocks
# --------------------------------------------------------------------------- #
def test_shocked_bond_price_matches_dv01_formula():
    assert risk.shocked_bond_price(100.0, 5.0, 50.0) == pytest.approx(97.5)
    assert risk.shocked_bond_price(100.0, 5.0, -50.0) == pytest.approx(102.5)
    assert risk.shocked_bond_price(100.0, None, 100.0) == 100.0


def test_shock_position_bond_widening_loses_money_for_long():
    bond = _bond()
    out = risk.shock_position(bond, baseline_price=100.0, baseline_spread=300.0,
                              spread_shock_bps=50.0)
    assert out["price_pnl"] == pytest.approx(-25_000.0)
    assert out["spread_pnl"] == pytest.approx(-25_000.0)
    assert out["benchmark_pnl"] == pytest.approx(0.0)
    assert out["total_pnl"] == pytest.approx(-25_000.0)


def test_shock_position_hedge_short_profits_on_negative_pct():
    hedge = _hedge()
    out = risk.shock_position(hedge, baseline_price=80.0, baseline_spread=None,
                              hedge_shock_pct=-0.05)
    # New price 76.0; short of 10_000 shares: (76-80) * 10_000 * -1 = 40_000
    assert out["hedge_pnl"] == pytest.approx(40_000.0)


# --------------------------------------------------------------------------- #
# Scenario grids
# --------------------------------------------------------------------------- #
def test_scenario_pnl_per_position(cfg):
    book = pf.Portfolio([_bond(), _hedge()], name="t", issuer="CNC")
    df = risk.scenario_pnl(book, _marks(), spread_shock_bps=100.0, hedge_shock_pct=-0.02)
    assert set(df["kind"]) == {"bond", "hedge"}
    bond_pnl = df.loc[df["kind"] == "bond", "total_pnl"].iloc[0]
    hedge_pnl = df.loc[df["kind"] == "hedge", "total_pnl"].iloc[0]
    # Bond loses ~5% (ModDur 5 * 100bp), hedge gains on -2% HYG move
    assert bond_pnl < 0
    assert hedge_pnl > 0


def test_scenario_pnl_missing_security_raises(cfg):
    book = pf.Portfolio([_bond()], name="t", issuer="CNC")
    marks = pd.DataFrame({"price": {"OTHER Corp": 100.0}})
    with pytest.raises(KeyError, match="marks_from_tidy"):
        risk.scenario_pnl(book, marks, spread_shock_bps=0.0, hedge_shock_pct=0.0)


def test_parallel_spread_grid_zero_shock_is_flat(cfg):
    book = pf.Portfolio([_bond(), _hedge()], name="t", issuer="CNC")
    grid = risk.parallel_spread_grid(book, _marks(), cfg)
    assert len(grid) == len(cfg.risk.parallel_shocks_bps)
    zero = grid[grid["spread_shock_bps"] == 0.0].iloc[0]
    assert zero["total_pnl"] == pytest.approx(0.0)
    # Symmetric: tighten 100 should mirror widen 100 for the linear engine
    plus = grid[grid["spread_shock_bps"] == 100.0]["total_pnl"].iloc[0]
    minus = grid[grid["spread_shock_bps"] == -100.0]["total_pnl"].iloc[0]
    assert plus == pytest.approx(-minus, abs=1.0)


def test_parallel_spread_grid_hedge_softens_loss(cfg):
    book = pf.Portfolio([_bond(), _hedge()], name="t", issuer="CNC")
    no_hedge = pf.Portfolio([_bond()], name="t", issuer="CNC")
    grid_h = risk.parallel_spread_grid(book, _marks(), cfg)
    grid_n = risk.parallel_spread_grid(no_hedge, _marks(), cfg)
    # At +100 bps, the hedged book loses less than the unhedged one.
    loss_h = grid_h[grid_h["spread_shock_bps"] == 100.0]["total_pnl"].iloc[0]
    loss_n = grid_n[grid_n["spread_shock_bps"] == 100.0]["total_pnl"].iloc[0]
    assert loss_h > loss_n  # less negative


def test_joint_scenario_grid_shape(cfg):
    book = pf.Portfolio([_bond(), _hedge()], name="t", issuer="CNC")
    grid = risk.joint_scenario_grid(
        book, _marks(), cfg,
        spread_shocks_bps=[-50.0, 0.0, 50.0], hedge_shocks_pct=[-0.05, 0.0, 0.05],
    )
    assert len(grid) == 9
    assert set(grid.columns) == {"spread_shock_bps", "hedge_shock_pct", "total_pnl"}


# --------------------------------------------------------------------------- #
# Tail measures
# --------------------------------------------------------------------------- #
def test_value_at_risk_picks_quantile():
    returns = pd.Series([-0.30, -0.20, -0.10, 0.00, 0.10, 0.20, 0.30])
    assert risk.value_at_risk(returns, alpha=0.10) == pytest.approx(returns.quantile(0.10))


def test_conditional_var_averages_tail():
    returns = pd.Series([-0.30, -0.20, -0.10, 0.00, 0.10, 0.20, 0.30])
    cvar_30 = risk.conditional_var(returns, alpha=0.30)
    # 30th percentile sits between -0.20 and -0.10; tail captures -0.30 and -0.20.
    assert cvar_30 == pytest.approx(-0.25)


def test_tail_metrics_empty_returns_nan():
    out = risk.tail_metrics(pd.Series([], dtype=float))
    assert out["n"] == 0.0
    assert np.isnan(out["mean"])
    assert np.isnan(out["var_05"])


def test_backtest_tail_metrics_reads_excess_column():
    results = pd.DataFrame({"excess": [-0.20, -0.10, 0.05, 0.15, 0.30]})
    out = risk.backtest_tail_metrics(results, alphas=(0.20,))
    assert out["n"] == 5
    assert "var_20" in out and "cvar_20" in out

    with pytest.raises(KeyError, match="not in results"):
        risk.backtest_tail_metrics(results, column="missing")
