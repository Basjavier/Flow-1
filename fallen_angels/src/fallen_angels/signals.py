"""Spread analytics: weighted-average Z-spreads, differential, rolling z-score.

The trade signal is the rolling z-score of the **target weighted-average
Z-spread minus the peer weighted-average Z-spread**, over a ~24-month
(``config.thresholds.zscore_window_days``, default 504-day) window. Entry/exit
flags are derived from ``config.thresholds``.

Time series come in tidy long form (``date, security, field, value``) from
:mod:`fallen_angels.data_pull`; weights come as security->weight Series from
:mod:`fallen_angels.comparables`.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from fallen_angels.config import TradeConfig

logger = logging.getLogger(__name__)


def pivot_field(tidy: pd.DataFrame, field: str) -> pd.DataFrame:
    """Pivot a tidy long frame to wide (index=date, columns=security) for ``field``.

    Field matching is case-insensitive against the ``field`` column.

    Raises:
        ValueError: If no rows match ``field``.
    """
    mask = tidy["field"].astype(str).str.upper() == field.upper()
    sub = tidy.loc[mask]
    if sub.empty:
        available = sorted(tidy["field"].astype(str).unique())
        raise ValueError(
            f"pivot_field: no rows for field {field!r}. Available fields: {available}. "
            f"Check config.bloomberg.zspread_field and the pulled timeseries_fields."
        )
    wide = sub.pivot_table(index="date", columns="security", values="value", aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def weighted_average(wide: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Per-date weighted average across securities, renormalized over availables.

    On any date where some securities are NaN (no quote), the weights of the
    available securities are renormalized so the average stays well-defined.

    Args:
        wide: index=date, columns=security, values=spread levels.
        weights: security->weight (need not sum to 1; only ratios matter).

    Returns:
        Series indexed by date.
    """
    w = weights.reindex(wide.columns).fillna(0.0).to_numpy(dtype=float)
    values = wide.to_numpy(dtype=float)
    available = ~np.isnan(values)
    eff_w = available * w  # (dates, securities)
    denom = eff_w.sum(axis=1)
    numer = np.nansum(np.where(available, values, 0.0) * w, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        avg = np.where(denom > 0, numer / denom, np.nan)
    return pd.Series(avg, index=wide.index, name="wavg")


def rolling_zscore(
    series: pd.Series,
    window: int,
    *,
    min_periods: int | None = None,
) -> pd.Series:
    """Trailing rolling z-score: ``(x - mean) / std`` over ``window`` obs.

    Uses population std (``ddof=0``); zero-variance windows yield NaN. Defaults
    ``min_periods`` to a quarter of the window so the series populates earlier.
    """
    mp = min_periods if min_periods is not None else max(2, window // 4)
    mean = series.rolling(window, min_periods=mp).mean()
    std = series.rolling(window, min_periods=mp).std(ddof=0)
    z = (series - mean) / std.replace(0.0, np.nan)
    return z.rename("z_score")


def compute_signal(
    target_tidy: pd.DataFrame,
    target_weights: pd.Series,
    peer_tidy: pd.DataFrame,
    peer_weights: pd.Series,
    cfg: TradeConfig,
    *,
    field: str | None = None,
) -> pd.DataFrame:
    """Compute the daily spread differential and its rolling z-score + flags.

    Args:
        target_tidy: Tidy long Z-spread history for the target's bonds.
        target_weights: security->weight for the target basket.
        peer_tidy: Tidy long Z-spread history for the peer basket.
        peer_weights: security->weight for the peer basket.
        cfg: Trade config (window + thresholds + zspread field).
        field: Override the spread field (defaults to ``cfg.bloomberg.zspread_field``).

    Returns:
        DataFrame with columns: ``date``, ``target_wavg``, ``peer_wavg``,
        ``differential``, ``z_score``, ``entry``, ``exit_converge``.
    """
    field = field or cfg.bloomberg.zspread_field
    target_wide = pivot_field(target_tidy, field)
    peer_wide = pivot_field(peer_tidy, field)

    target_wavg = weighted_average(target_wide, target_weights)
    peer_wavg = weighted_average(peer_wide, peer_weights)

    df = pd.concat(
        [target_wavg.rename("target_wavg"), peer_wavg.rename("peer_wavg")], axis=1
    ).dropna()
    df["differential"] = df["target_wavg"] - df["peer_wavg"]
    df["z_score"] = rolling_zscore(df["differential"], cfg.thresholds.zscore_window_days)
    df["entry"] = df["z_score"] >= cfg.thresholds.entry_z
    df["exit_converge"] = df["z_score"] <= cfg.thresholds.exit_converge_z

    logger.info(
        "compute_signal: %d dates, latest differential=%.1f, z=%.2f",
        len(df),
        df["differential"].iloc[-1] if len(df) else float("nan"),
        df["z_score"].iloc[-1] if len(df) else float("nan"),
    )
    return df.reset_index().rename(columns={"index": "date"})


def latest_state(signal_df: pd.DataFrame, cfg: TradeConfig) -> dict[str, object]:
    """Summarize the most recent signal row into a plain dict for alerts/UI.

    The ``state`` is a coarse label: ``ENTER`` (z >= entry), ``EXIT`` (z <=
    converge), else ``HOLD`` (between) / ``FLAT`` (no valid z yet).
    """
    if signal_df.empty:
        return {"state": "FLAT", "reason": "no data"}
    row = signal_df.iloc[-1]
    z = row["z_score"]
    if pd.isna(z):
        state = "FLAT"
    elif z >= cfg.thresholds.entry_z:
        state = "ENTER"
    elif z <= cfg.thresholds.exit_converge_z:
        state = "EXIT"
    else:
        state = "HOLD"
    return {
        "date": row["date"],
        "differential_bps": float(row["differential"]),
        "z_score": None if pd.isna(z) else float(z),
        "entry_z": cfg.thresholds.entry_z,
        "exit_converge_z": cfg.thresholds.exit_converge_z,
        "state": state,
    }
