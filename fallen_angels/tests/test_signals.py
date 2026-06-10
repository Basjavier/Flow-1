"""Offline tests for signals.py: pivots, weighted averages, z-score, signal."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fallen_angels import signals as sig


def _tidy(dates: pd.DatetimeIndex, levels: np.ndarray, weights: pd.Series) -> pd.DataFrame:
    rows = [
        (day, security, "Z_SPRD_MID", level + hash(security) % 5)
        for day, level in zip(dates, levels)
        for security in weights.index
    ]
    return pd.DataFrame(rows, columns=["date", "security", "field", "value"])


def test_pivot_field_unknown_field_lists_available():
    tidy = pd.DataFrame(
        {"date": ["2024-01-02"], "security": ["X Corp"], "field": ["Z_SPRD_MID"], "value": [100.0]}
    )
    with pytest.raises(ValueError, match="Available fields"):
        sig.pivot_field(tidy, "NOPE")


def test_weighted_average_renormalizes_over_missing_quotes():
    wide = pd.DataFrame(
        {"X Corp": [100.0, np.nan], "Y Corp": [200.0, 200.0]},
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )
    wavg = sig.weighted_average(wide, pd.Series({"X Corp": 0.5, "Y Corp": 0.5}))
    assert wavg.iloc[0] == pytest.approx(150.0)
    assert wavg.iloc[1] == pytest.approx(200.0)  # day 2 renormalizes to Y only


def test_rolling_zscore_flags_dislocation_and_handles_zero_variance():
    flat = pd.Series(np.full(50, 5.0))
    assert sig.rolling_zscore(flat, window=20).dropna().empty  # zero variance -> NaN

    noisy = pd.Series(np.sin(np.linspace(0, 20, 300)))
    spiked = noisy.copy()
    spiked.iloc[-1] += 10.0
    z = sig.rolling_zscore(spiked, window=100)
    assert z.iloc[-1] > 3.0


def test_compute_signal_trips_entry_on_planted_dislocation(cfg):
    dates = pd.bdate_range("2024-01-01", periods=600)
    rng = np.random.default_rng(0)
    peer_level = 250 + np.cumsum(rng.normal(0, 1, len(dates)))
    target_level = peer_level + 60 + rng.normal(0, 3, len(dates))
    target_level[-30:] += 90  # blow the differential out at the end

    target_w = pd.Series({"T1 Corp": 0.6, "T2 Corp": 0.4})
    peer_w = pd.Series({"P1 Corp": 0.5, "P2 Corp": 0.5})
    signal = sig.compute_signal(
        _tidy(dates, target_level, target_w), target_w,
        _tidy(dates, peer_level, peer_w), peer_w,
        cfg,
    )

    expected = {"date", "target_wavg", "peer_wavg", "differential", "z_score", "entry", "exit_converge"}
    assert expected <= set(signal.columns)
    assert signal["z_score"].iloc[-1] > cfg.thresholds.entry_z
    state = sig.latest_state(signal, cfg)
    assert state["state"] == "ENTER"
    assert state["differential_bps"] > 100


def test_latest_state_flat_on_empty(cfg):
    assert sig.latest_state(pd.DataFrame(), cfg)["state"] == "FLAT"
