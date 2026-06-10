"""Offline tests for universe.py: screening, normalization, curve fit, scoring."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fallen_angels import universe as uni


@pytest.fixture()
def raw_universe() -> pd.DataFrame:
    return pd.DataFrame({
        "security": ["A Corp", "B Corp", "C Corp", "D Corp", "E Corp"],
        "id_cusip": ["111", "222", "333", "444", "555"],
        "security_des": ["a", "b", "c", "d", "e"],
        "maturity": ["2029-06-01", "2031-01-01", "2026-01-01", "2030-01-01", "2030-06-01"],
        "cpn": [5, 5, 5, 5, 5],
        "amt_outstanding": [800e6, 1.2e9, 900e6, 400e6, 1.0e9],
        "payment_rank": ["Sr Unsecured"] * 4 + ["Subordinated"],
        "issue_dt": ["2019-06-01", "2021-01-01", "2016-01-01", "2022-01-01", "2020-06-01"],
        "crncy": ["USD"] * 5,
    })


def test_filter_universe_applies_all_screens(cfg, raw_universe):
    filtered = uni.filter_universe(raw_universe, cfg, asof=date(2026, 5, 27))
    # C matures before maturity_min, D is below min size, E is subordinated.
    assert set(filtered["security"]) == {"A Corp", "B Corp"}
    assert {"years_to_maturity", "age_years"} <= set(filtered.columns)
    assert (filtered["years_to_maturity"] > 0).all()


def test_filter_universe_missing_required_column_raises(cfg, raw_universe):
    with pytest.raises(KeyError, match="static_fields"):
        uni.filter_universe(raw_universe.drop(columns=["maturity"]), cfg)


def test_minmax_constant_series_maps_to_half():
    assert (uni.minmax(pd.Series([3.0, 3.0, 3.0])) == 0.5).all()


def test_spread_vs_curve_residual_sign():
    df = pd.DataFrame({
        "security": ["S1", "S2", "S3"],
        "years_to_maturity": [2.0, 5.0, 8.0],
        "z_spread": [200.0, 300.0, 320.0],  # S2 sits above the S1-S3 line
    })
    resid = uni.spread_vs_curve(df, spread_col="z_spread", degree=1)
    assert resid["S2"] > 0  # wide of the fitted curve = cheap


def test_score_universe_ranks_and_renormalizes(cfg, raw_universe):
    filtered = uni.filter_universe(raw_universe, cfg, asof=date(2026, 5, 27))
    filtered["z_spread"] = [320.0, 240.0]  # A wide vs B
    resid = uni.spread_vs_curve(filtered, spread_col="z_spread", degree=1)
    ranked = uni.score_universe(filtered, cfg, spread_residual=resid)

    assert ranked["score"].is_monotonic_decreasing
    assert "score_liquidity" not in ranked.columns  # omitted factor -> absent
    assert {"score_amount_outstanding", "score_seasoning", "score_spread_vs_curve"} <= set(ranked.columns)


def test_score_universe_no_factors_raises(cfg):
    bare = pd.DataFrame({"security": ["A Corp"]})
    with pytest.raises(ValueError, match="no scorable factors"):
        uni.score_universe(bare, cfg)
