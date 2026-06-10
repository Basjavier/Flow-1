"""Offline tests for backtest.py: return math, engine with injected fetchers,
stratified summaries, and the shipped events file."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from fallen_angels import backtest as bt


def _event(ticker: str = "FA1", **overrides) -> bt.DowngradeEvent:
    base = dict(
        issuer="Test Issuer",
        ticker=ticker,
        equity_ticker=f"{ticker} US Equity",
        sector="Healthcare",
        catalyst="secular_decline",
        downgrade_date=date(2020, 3, 25),
        from_rating="BBB-",
        to_rating="BB+",
    )
    return bt.DowngradeEvent(**(base | overrides))


def _fake_bond_fetcher(event, start, end):
    dates = pd.bdate_range(start, end)
    prices = pd.DataFrame({"B1 Corp": np.linspace(90.0, 99.0, len(dates))}, index=dates)
    return prices, pd.Series({"B1 Corp": 5.0})


def _fake_hedge_fetcher(start, end):
    dates = pd.bdate_range(start, end)
    return pd.Series(np.linspace(1.0, 1.04, len(dates)), index=dates, name="hedge_tr")


# --------------------------------------------------------------------------- #
# Pure return math
# --------------------------------------------------------------------------- #
def test_total_return_proxy_flat_price_earns_coupon():
    prices = pd.Series([100.0, 100.0], index=pd.to_datetime(["2020-01-01", "2020-12-31"]))
    tr = bt.total_return_proxy(prices, coupon_pct=5.0)
    assert tr.iloc[0] == pytest.approx(1.0)
    assert tr.iloc[-1] == pytest.approx(1.05, abs=1e-3)  # ~one year of 5% carry


def test_total_return_proxy_empty_prices():
    assert bt.total_return_proxy(pd.Series(dtype=float), 5.0).empty


def test_excess_path_nets_hedge_ratio():
    dates = pd.to_datetime(["2020-01-01", "2020-06-30"])
    bond = pd.Series([1.0, 1.10], index=dates)
    hedge = pd.Series([1.0, 1.04], index=dates)
    full = bt.excess_path(bond, hedge, hedge_ratio=1.0)
    assert full.iloc[-1] == pytest.approx(0.06)
    partial = bt.excess_path(bond, hedge, hedge_ratio=0.5)
    assert partial.iloc[-1] == pytest.approx(0.08)


def test_max_drawdown():
    path = pd.Series([0.0, 0.05, 0.02, 0.08, 0.04])
    assert bt.max_drawdown(path) == pytest.approx(-0.04)
    assert np.isnan(bt.max_drawdown(pd.Series(dtype=float)))


# --------------------------------------------------------------------------- #
# Bond selection
# --------------------------------------------------------------------------- #
def test_select_event_bonds_filters_and_ranks():
    universe = pd.DataFrame({
        "security": ["S1", "S2", "S3", "S4"],
        "maturity": ["2021-01-01", "2027-01-01", "2028-01-01", "2029-01-01"],
        "cpn": [4.0, 5.0, 6.0, 7.0],
        "amt_outstanding": [2e9, 1e9, 2e8, 1.5e9],
    })
    picked = bt.select_event_bonds(universe, horizon_end=date(2021, 3, 25), max_bonds=2)
    # S1 matures inside the window, S3 is below min_amount -> S4, S2 by size.
    assert picked["security"].tolist() == ["S4", "S2"]


def test_select_event_bonds_missing_column_raises():
    with pytest.raises(KeyError, match="static_fields"):
        bt.select_event_bonds(pd.DataFrame({"security": ["S1"]}), horizon_end=date(2021, 1, 1))


def test_select_event_bonds_empty_screen_raises():
    universe = pd.DataFrame({
        "security": ["S1"], "maturity": ["2020-06-01"], "cpn": [5.0], "amt_outstanding": [1e9],
    })
    with pytest.raises(RuntimeError, match="explicit"):
        bt.select_event_bonds(universe, horizon_end=date(2021, 3, 25))


# --------------------------------------------------------------------------- #
# Engine + summaries (injected fetchers, no Bloomberg)
# --------------------------------------------------------------------------- #
def test_run_backtest_measures_excess_and_paths():
    results, paths = bt.run_backtest(
        [_event()], bond_fetcher=_fake_bond_fetcher, hedge_fetcher=_fake_hedge_fetcher,
        return_paths=True,
    )
    row = results.iloc[0]
    # Price 90->99 (+10%) plus ~5.5pts carry on 90 vs 4% hedge: excess well > 0.
    assert pd.isna(row["error"])
    assert row["hit"] is True or row["hit"] == True  # noqa: E712 - numpy bool
    assert row["excess"] > 0.08
    assert row["bond_tr"] > row["hedge_tr"]
    assert "FA1" in paths and paths["FA1"].iloc[0] == pytest.approx(0.0)


def test_run_backtest_isolates_failing_event():
    def exploding_fetcher(event, start, end):
        raise RuntimeError("chain did not resolve")

    results = bt.run_backtest(
        [_event("BAD"), _event("OK")],
        bond_fetcher=lambda e, s, x: (_fake_bond_fetcher(e, s, x) if e.ticker == "OK"
                                      else exploding_fetcher(e, s, x)),
        hedge_fetcher=_fake_hedge_fetcher,
    )
    bad = results[results["ticker"] == "BAD"].iloc[0]
    ok = results[results["ticker"] == "OK"].iloc[0]
    assert "chain did not resolve" in bad["error"]
    assert np.isnan(bad["excess"])
    assert pd.isna(ok["error"])


def test_run_backtest_requires_cfg_or_fetchers():
    with pytest.raises(ValueError, match="pass cfg"):
        bt.run_backtest([_event()])


def test_summarize_overall_and_stratified():
    results = bt.run_backtest(
        [_event("A", sector="Energy"), _event("B", sector="Media", catalyst="covid_shock")],
        bond_fetcher=_fake_bond_fetcher, hedge_fetcher=_fake_hedge_fetcher,
    )
    overall = bt.summarize(results)
    assert overall.loc["ALL", "n"] == 2
    assert overall.loc["ALL", "hit_rate"] == 1.0
    by_sector = bt.summarize(results, by="sector")
    assert set(by_sector.index) == {"Energy", "Media"}

    all_failed = results.assign(excess=np.nan)
    with pytest.raises(ValueError, match="no valid events"):
        bt.summarize(all_failed)


# --------------------------------------------------------------------------- #
# Shipped events file
# --------------------------------------------------------------------------- #
def test_shipped_events_file_loads_and_is_sane():
    events = bt.load_events()
    assert len(events) >= 12
    assert all(date(2015, 1, 1) <= e.downgrade_date <= date(2024, 12, 31) for e in events)
    assert events == sorted(events, key=lambda e: e.downgrade_date)
    assert {e.catalyst for e in events} <= set(bt.CATALYSTS)
    assert all(e.equity_ticker.endswith("Equity") for e in events)


def test_load_events_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="RATC"):
        bt.load_events(tmp_path / "nope.yaml")
