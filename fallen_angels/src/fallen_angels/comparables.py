"""BB+ healthcare peer-basket construction for the spread benchmark.

The peer set is defined in ``config.peers`` (defaults: HCA, HUM, MOH, BHC --
rationale in ``config/cnc.yaml`` and the memo). For each peer we pull its bond
universe, apply the *same* selection screen used for the target issuer (so the
benchmark is maturity/seniority/size-comparable), and assign basket weights.
:mod:`fallen_angels.signals` then compares the target's weighted-average
Z-spread against this basket's.

Weighting schemes:
* ``equal_issuer`` (default) -- each peer name gets equal total weight, split
  within the name by amount outstanding (no single large issuer dominates)
* ``amount``                 -- global amount-outstanding weights
* ``equal_bond``             -- equal weight per bond
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from fallen_angels import data_pull as dp
from fallen_angels.config import Peer, TradeConfig
from fallen_angels.universe import COL_AMT_OUT, filter_universe

logger = logging.getLogger(__name__)


def peer_tickers(cfg: TradeConfig) -> list[str]:
    """Return the configured peer equity tickers."""
    return [p.ticker for p in cfg.peers]


def _peer_short(ticker: str) -> str:
    """Derive a short code from an equity ticker ('HCA US Equity' -> 'HCA')."""
    return ticker.split()[0].upper()


def fetch_peer_universe(
    peer: Peer,
    cfg: TradeConfig,
    *,
    refresh: bool = False,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Pull one peer's raw bond universe, tagged with peer name/ticker.

    Caches under the *target* issuer's data dir (label ``peer_<SHORT>``) so a
    trade's peer pulls live alongside it.
    """
    short = _peer_short(peer.ticker)
    df = dp.get_bond_universe_for(
        peer.ticker,
        cfg.bloomberg.bond_chain_field,
        cfg.bloomberg.static_fields,
        cache_dir=cfg.issuer.short,
        cache_label=f"peer_{short}",
        refresh=refresh,
        data_dir=data_dir,
    )
    df = df.copy()
    df["peer"] = peer.name
    df["peer_ticker"] = peer.ticker
    return df


def assign_basket_weights(
    df: pd.DataFrame,
    *,
    scheme: str = "equal_issuer",
    amt_col: str = COL_AMT_OUT,
    group_col: str = "peer",
) -> pd.Series:
    """Return per-row basket weights (summing to 1.0) under the chosen scheme.

    Args:
        df: Bonds to weight (row-aligned output).
        scheme: ``"equal_issuer"``, ``"amount"``, or ``"equal_bond"``.
        amt_col: Amount-outstanding column (for amount-based schemes).
        group_col: Grouping column for ``equal_issuer`` (e.g. ``"peer"``).

    Returns:
        Float Series aligned to ``df.index``, summing to 1.0.
    """
    n = len(df)
    if n == 0:
        return pd.Series(dtype=float)

    if scheme == "equal_bond":
        return pd.Series(1.0 / n, index=df.index)

    amt = pd.to_numeric(df[amt_col], errors="coerce").fillna(0.0)

    if scheme == "amount":
        total = amt.sum()
        if total <= 0:
            return pd.Series(1.0 / n, index=df.index)
        return amt / total

    if scheme == "equal_issuer":
        if group_col not in df.columns:
            raise KeyError(
                f"assign_basket_weights(scheme='equal_issuer') needs column "
                f"'{group_col}'. Present: {list(df.columns)}."
            )
        groups = df[group_col]
        n_groups = groups.nunique()
        weights = pd.Series(0.0, index=df.index)
        for _, idx in groups.groupby(groups).groups.items():
            g_amt = amt.loc[idx]
            g_total = g_amt.sum()
            within = g_amt / g_total if g_total > 0 else pd.Series(1.0 / len(idx), index=idx)
            weights.loc[idx] = within / n_groups
        return weights

    raise ValueError(
        f"Unknown weighting scheme {scheme!r}; use 'equal_issuer', 'amount', or 'equal_bond'."
    )


def build_comparables(
    cfg: TradeConfig,
    *,
    refresh: bool = False,
    data_dir: Path | None = None,
    weight_scheme: str = "equal_issuer",
) -> pd.DataFrame:
    """Build the screened, weighted peer bond basket.

    For each peer: pull its universe, apply the target's selection screen, tag
    it, then concatenate and assign basket weights.

    Returns:
        One row per surviving peer bond with the static columns plus ``peer``,
        ``peer_ticker``, and ``weight`` (weights sum to 1.0).

    Raises:
        RuntimeError: If no peer bonds survive the screen.
    """
    frames: list[pd.DataFrame] = []
    for peer in cfg.peers:
        raw = fetch_peer_universe(peer, cfg, refresh=refresh, data_dir=data_dir)
        screened = filter_universe(raw, cfg)
        if screened.empty:
            logger.warning("Peer %s: no bonds passed the universe screen.", peer.name)
            continue
        # filter_universe lower-cases columns and preserves the peer tags.
        frames.append(screened)

    if not frames:
        raise RuntimeError(
            "No peer bonds passed the screen. Check config.peers and the "
            "config.universe filters (maturity band / min size / seniority)."
        )

    combined = pd.concat(frames, ignore_index=True)
    combined["weight"] = assign_basket_weights(
        combined, scheme=weight_scheme, group_col="peer"
    ).to_numpy()
    logger.info(
        "build_comparables: %d bonds across %d peers (scheme=%s)",
        len(combined), combined["peer"].nunique(), weight_scheme,
    )
    return combined


def to_security_weights(
    df: pd.DataFrame,
    *,
    security_col: str = "security",
    weight_col: str = "weight",
) -> pd.Series:
    """Collapse a weighted basket to a security->weight Series summing to 1.0.

    Aggregates duplicate securities and renormalizes. This is the form
    :func:`fallen_angels.signals.compute_signal` expects.
    """
    if weight_col not in df.columns:
        raise KeyError(
            f"to_security_weights: no '{weight_col}' column. Assign weights first "
            f"(assign_basket_weights / build_comparables)."
        )
    grouped = df.groupby(security_col)[weight_col].sum()
    total = grouped.sum()
    if not np.isfinite(total) or total <= 0:
        raise ValueError("to_security_weights: weights sum to <= 0; nothing to normalize.")
    return (grouped / total).rename("weight")
