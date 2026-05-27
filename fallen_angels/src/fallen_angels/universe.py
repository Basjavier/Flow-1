"""Bond-universe construction and scoring.

Consumes the raw static universe from :func:`fallen_angels.data_pull.get_bond_universe`,
applies the config selection filters (maturity band, minimum size, seniority,
currency), and scores the survivors on four factors:

* **amount_outstanding** -- larger issues are more liquid/easier to source
* **seasoning**          -- older bonds are the ones IG mandates are forced to dump
* **liquidity**          -- tighter bid/ask scores higher (optional input)
* **spread_vs_curve**    -- bonds trading wide of the issuer's own fitted curve
                            are cheap (attractive for the long leg)

Each factor is min-max normalized to [0, 1] and combined with the weights in
``config.scoring``, renormalized over whichever factors are actually available.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from fallen_angels.config import TradeConfig

logger = logging.getLogger(__name__)

# Standard Bloomberg static mnemonics, lower-cased as xbbg returns them.
COL_CUSIP = "id_cusip"
COL_DESC = "security_des"
COL_MATURITY = "maturity"
COL_COUPON = "cpn"
COL_AMT_OUT = "amt_outstanding"
COL_RANK = "payment_rank"
COL_ISSUE = "issue_dt"
COL_CRNCY = "crncy"


def _require_columns(df: pd.DataFrame, columns: list[str], context: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise KeyError(
            f"{context}: missing column(s) {missing}. Available: {list(df.columns)}. "
            f"Add the corresponding mnemonic to config.bloomberg.static_fields."
        )


def filter_universe(
    df: pd.DataFrame,
    cfg: TradeConfig,
    *,
    asof: date | None = None,
) -> pd.DataFrame:
    """Apply the config selection screen and attach derived tenor columns.

    Filters on maturity band, minimum amount outstanding, currency, and
    seniority (the last two are skipped with a warning if their columns are
    absent). Adds ``years_to_maturity`` and ``age_years`` (seasoning).

    Args:
        df: Raw static universe (one row per bond, ``security`` + mnemonics).
        cfg: Trade config supplying ``universe`` filters.
        asof: Reference date for tenor/age (defaults to today).

    Returns:
        Filtered copy with normalized column names and derived columns.
    """
    asof = asof or date.today()
    asof_ts = pd.Timestamp(asof)
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    _require_columns(out, [COL_MATURITY, COL_AMT_OUT], "filter_universe")

    out[COL_MATURITY] = pd.to_datetime(out[COL_MATURITY], errors="coerce")
    out[COL_AMT_OUT] = pd.to_numeric(out[COL_AMT_OUT], errors="coerce")
    if COL_ISSUE in out.columns:
        out[COL_ISSUE] = pd.to_datetime(out[COL_ISSUE], errors="coerce")

    out["years_to_maturity"] = (out[COL_MATURITY] - asof_ts).dt.days / 365.25
    if COL_ISSUE in out.columns:
        out["age_years"] = (asof_ts - out[COL_ISSUE]).dt.days / 365.25
    else:
        out["age_years"] = np.nan
        logger.warning("No '%s' column; seasoning factor will be unavailable.", COL_ISSUE)

    n0 = len(out)
    mask = (
        (out[COL_MATURITY] >= pd.Timestamp(cfg.universe.maturity_min))
        & (out[COL_MATURITY] <= pd.Timestamp(cfg.universe.maturity_max))
        & (out[COL_AMT_OUT] >= cfg.universe.min_amount_outstanding_usd)
    )

    if COL_CRNCY in out.columns:
        mask &= out[COL_CRNCY].astype(str).str.upper() == cfg.universe.currency.upper()
    else:
        logger.warning("No '%s' column; currency filter skipped.", COL_CRNCY)

    if COL_RANK in out.columns:
        allowed = {s.strip().lower() for s in cfg.universe.seniority}
        mask &= out[COL_RANK].astype(str).str.strip().str.lower().isin(allowed)
    else:
        logger.warning("No '%s' column; seniority filter skipped.", COL_RANK)

    filtered = out.loc[mask].reset_index(drop=True)
    logger.info("filter_universe: %d -> %d bonds after screen", n0, len(filtered))
    return filtered


def minmax(series: pd.Series) -> pd.Series:
    """Min-max normalize to [0, 1]. Constant/all-NaN series map to 0.5."""
    values = pd.to_numeric(series, errors="coerce")
    lo, hi = np.nanmin(values), np.nanmax(values)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return pd.Series(0.5, index=series.index)
    return (values - lo) / (hi - lo)


def spread_vs_curve(
    df: pd.DataFrame,
    *,
    spread_col: str,
    maturity_col: str = "years_to_maturity",
    security_col: str = "security",
    degree: int = 2,
) -> pd.Series:
    """Residual of each bond's spread vs the issuer's own fitted spread curve.

    Fits a degree-``degree`` polynomial of spread on years-to-maturity and
    returns ``actual - fitted`` (bps), indexed by ``security``. Positive =
    trades wide of the curve = cheap. Falls back to a lower degree (or the mean)
    when there are too few bonds.

    Args:
        df: Bonds with a spread column, tenor column, and security id.
        spread_col: Column holding the spread level (e.g. a Z-spread snapshot).
        maturity_col: Column holding years-to-maturity.
        security_col: Column holding the security identifier (used as the index).
        degree: Polynomial degree for the fitted curve.

    Returns:
        Residual Series in spread units, indexed by security.
    """
    sub = df[[security_col, maturity_col, spread_col]].copy()
    sub[maturity_col] = pd.to_numeric(sub[maturity_col], errors="coerce")
    sub[spread_col] = pd.to_numeric(sub[spread_col], errors="coerce")
    sub = sub.dropna(subset=[maturity_col, spread_col])
    if sub.empty:
        return pd.Series(dtype=float, name="spread_residual")

    x = sub[maturity_col].to_numpy(dtype=float)
    y = sub[spread_col].to_numpy(dtype=float)
    eff_degree = min(degree, max(0, len(sub) - 1))
    coeffs = np.polyfit(x, y, eff_degree)
    fitted = np.polyval(coeffs, x)
    residual = pd.Series(y - fitted, index=sub[security_col].to_numpy(), name="spread_residual")
    return residual


def score_universe(
    df: pd.DataFrame,
    cfg: TradeConfig,
    *,
    liquidity: pd.Series | None = None,
    spread_residual: pd.Series | None = None,
    security_col: str = "security",
) -> pd.DataFrame:
    """Score and rank the filtered universe.

    Computes available sub-scores (each in [0, 1]) and a weighted composite
    ``score`` using ``cfg.scoring`` renormalized over the present factors.
    Factors requiring optional inputs are skipped if those inputs are omitted.

    Args:
        df: Filtered universe from :func:`filter_universe`.
        cfg: Trade config supplying scoring weights.
        liquidity: Optional Series indexed by security; *lower is more liquid*
            (e.g. bid/ask in bps). Scored as ``minmax(-liquidity)``.
        spread_residual: Optional Series indexed by security (from
            :func:`spread_vs_curve`); higher (wider/cheaper) scores higher.
        security_col: Security id column, used to align optional inputs.

    Returns:
        Copy of ``df`` with per-factor ``score_*`` columns and a composite
        ``score``, sorted descending by ``score``.
    """
    out = df.copy()
    sec = out[security_col]
    subscores: dict[str, pd.Series] = {}

    if COL_AMT_OUT in out.columns:
        subscores["amount_outstanding"] = minmax(out[COL_AMT_OUT])
    if "age_years" in out.columns and out["age_years"].notna().any():
        subscores["seasoning"] = minmax(out["age_years"])
    if liquidity is not None:
        liq = sec.map(liquidity)
        subscores["liquidity"] = minmax(-liq)
    if spread_residual is not None:
        resid = sec.map(spread_residual)
        subscores["spread_vs_curve"] = minmax(resid)

    if not subscores:
        raise ValueError(
            "score_universe: no scorable factors. Ensure the universe has "
            "amount_outstanding/issue_dt, or pass liquidity/spread_residual."
        )

    weights = cfg.scoring.as_dict()
    present = {k: weights.get(k, 0.0) for k in subscores}
    total_w = sum(present.values())
    if total_w <= 0:
        # All present factors carry zero weight in config; fall back to equal.
        present = {k: 1.0 for k in subscores}
        total_w = float(len(present))
    logger.info(
        "score_universe: factors=%s (weights renormalized over %.0f%% of config)",
        list(subscores), 100 * total_w / max(sum(weights.values()), 1e-9),
    )

    composite = pd.Series(0.0, index=out.index)
    for name, sub in subscores.items():
        out[f"score_{name}"] = sub.to_numpy()
        composite += present[name] / total_w * sub.to_numpy()
    out["score"] = composite
    return out.sort_values("score", ascending=False).reset_index(drop=True)
