"""Fallen-angel factor backtest: did IG->HY downgrades outperform HY beta?

Studies S&P downgrades from investment grade to high yield over 2015-2024.
For each event we measure the 12-month forward total return of the issuer's
senior bonds (clean price plus a straight-line coupon-accrual proxy) against
the hedge instrument (HYG), then stratify by sector and downgrade catalyst and
report hit rate, mean/median excess, cross-sectional Sharpe, and the drawdown
distribution of the per-event excess paths.

Event sourcing: Bloomberg exposes no clean API field that enumerates historical
rating migrations, so the canonical workflow is a RATC<GO> / SRCH<GO> screen on
the Terminal exported into ``config/fallen_angel_events.yaml``. A curated seed
list ships with the repo; every date is flagged for confirmation in the YAML.

Bloomberg pulls go through :mod:`fallen_angels.data_pull` under the
``"backtest"`` cache scope so the factor study never collides with live-trade
caches. Both fetchers are injectable, which keeps the math fully testable
offline (see ``tests/test_backtest.py``).

Run ``python -m fallen_angels.backtest --issuer CNC`` to execute the study
(the issuer config only supplies Bloomberg field names and the hedge ticker).
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from fallen_angels import data_pull as dp
from fallen_angels.config import TradeConfig, load_config, project_root

logger = logging.getLogger(__name__)

CACHE_SCOPE = "backtest"

# Catalyst taxonomy used for stratification. Unknown labels only warn, so the
# events file can grow new categories without a code change.
CATALYSTS = ("commodity_collapse", "covid_shock", "secular_decline", "leverage_event")

# ETF total-return field for the hedge leg. Price-only PX_LAST understates HY
# carry by the distribution yield (~5%/yr), which would flatter the factor.
# TODO alternatives if unavailable: "TOT_RETURN_INDEX_NET_DVDS", "PX_LAST".
HEDGE_TR_FIELD = "TOT_RETURN_INDEX_GROSS_DVDS"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DowngradeEvent(_Strict):
    """One S&P IG->HY rating migration (a fallen-angel event)."""

    issuer: str
    ticker: str
    equity_ticker: str
    sector: str
    catalyst: str
    downgrade_date: date
    from_rating: str
    to_rating: str
    agency: str = "S&P"
    # Explicit bond securities to use for the event. Leave empty to resolve the
    # issuer's chain at runtime -- but fill it in for issuers whose equity
    # anchor no longer resolves (taken private / merged, e.g. CLR).
    bonds: list[str] = []


# Injectable fetchers: (event, start, end) -> (wide clean prices, coupon by
# security); (start, end) -> hedge TR index. Defaults hit Bloomberg.
BondFetcher = Callable[[DowngradeEvent, date, date], tuple[pd.DataFrame, pd.Series]]
HedgeFetcher = Callable[[date, date], pd.Series]


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def load_events(path: Path | None = None) -> list[DowngradeEvent]:
    """Load and validate the downgrade-events file, sorted by event date.

    Args:
        path: Events YAML (defaults to ``config/fallen_angel_events.yaml``).

    Returns:
        Validated events, ascending by ``downgrade_date``.

    Raises:
        FileNotFoundError: If the events file does not exist.
        ValueError: If the file lacks a top-level ``events`` list or an entry
            fails schema validation.
    """
    path = path or (project_root() / "config" / "fallen_angel_events.yaml")
    if not path.exists():
        raise FileNotFoundError(
            f"Events file not found: {path}. Build it from a RATC<GO>/SRCH<GO> "
            f"export on the Terminal (see the module docstring) or restore the "
            f"seed file from the repo."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw.get("events") if isinstance(raw, dict) else None
    if not items:
        raise ValueError(
            f"{path} has no top-level 'events' list. Expected `events:` with one "
            f"mapping per downgrade (see DowngradeEvent fields)."
        )
    try:
        events = [DowngradeEvent.model_validate(item) for item in items]
    except ValidationError as exc:
        raise ValueError(
            f"{path} failed validation against DowngradeEvent. Check field "
            f"names/types:\n{exc}"
        ) from exc

    unknown = sorted({e.catalyst for e in events} - set(CATALYSTS))
    if unknown:
        logger.warning(
            "Events use catalyst labels outside the standard taxonomy %s: %s "
            "(stratification still works, just check for typos).",
            CATALYSTS, unknown,
        )
    return sorted(events, key=lambda e: e.downgrade_date)


# --------------------------------------------------------------------------- #
# Return math (pure, offline-testable)
# --------------------------------------------------------------------------- #
def total_return_proxy(prices: pd.Series, coupon_pct: float) -> pd.Series:
    """Total-return index (starting at 1.0) from clean prices + coupon accrual.

    Approximation: ``TR_t = (P_t + cpn * days/365.25) / P_0`` in price points on
    a par-100 quote basis. Ignores reinvestment and exact day counts -- adequate
    for a 12M factor study, not for P&L (portfolio.py handles that properly).

    Args:
        prices: Clean prices indexed by date (NaNs dropped).
        coupon_pct: Annual coupon in percent of par (e.g. ``5.25``).

    Returns:
        TR index Series indexed by date; empty if no valid prices.
    """
    clean = pd.to_numeric(prices, errors="coerce").dropna().sort_index()
    if clean.empty:
        return pd.Series(dtype=float, name="tr_index")
    idx = pd.to_datetime(clean.index)
    days = (idx - idx[0]).days.astype(float)
    carry = float(coupon_pct) * days / 365.25
    tr = (clean.to_numpy(dtype=float) + carry) / float(clean.iloc[0])
    return pd.Series(tr, index=idx, name="tr_index")


def _aligned_cum_returns(
    bond_tr: pd.Series, hedge_tr: pd.Series
) -> tuple[pd.Series, pd.Series]:
    """Inner-align two TR indices and rebase both to cumulative returns from 0.

    Rebasing at the first *common* date keeps the comparison fair when one leg
    starts quoting a few days later than the other.
    """
    df = pd.concat([bond_tr.rename("bond"), hedge_tr.rename("hedge")], axis=1).dropna()
    if df.empty:
        empty = pd.Series(dtype=float)
        return empty, empty
    bond = df["bond"] / df["bond"].iloc[0] - 1.0
    hedge = df["hedge"] / df["hedge"].iloc[0] - 1.0
    return bond, hedge


def excess_path(
    bond_tr: pd.Series, hedge_tr: pd.Series, hedge_ratio: float = 1.0
) -> pd.Series:
    """Cumulative excess-return path: bond minus ``hedge_ratio`` x hedge.

    Args:
        bond_tr: Bond TR index (from :func:`total_return_proxy`).
        hedge_tr: Hedge TR index.
        hedge_ratio: Hedge notional as a fraction of the long (1.0 = fully
            hedged; the live trade band is 0.5-0.7).

    Returns:
        Excess cumulative-return Series on the common dates (starts at 0.0).
    """
    bond, hedge = _aligned_cum_returns(bond_tr, hedge_tr)
    return (bond - hedge_ratio * hedge).rename("excess")


def max_drawdown(path: pd.Series) -> float:
    """Worst peak-to-trough decline of a cumulative-return path (<= 0, in return points)."""
    if path.empty:
        return float("nan")
    return float((path - path.cummax()).min())


# --------------------------------------------------------------------------- #
# Bond selection + default Bloomberg fetchers
# --------------------------------------------------------------------------- #
def select_event_bonds(
    universe: pd.DataFrame,
    *,
    horizon_end: date,
    max_bonds: int = 3,
    min_amount: int = 300_000_000,
) -> pd.DataFrame:
    """Pick the event's representative bonds from a static universe frame.

    Keeps bonds that survive the measurement window (maturity beyond
    ``horizon_end``) and meet ``min_amount``, then takes the ``max_bonds``
    largest by amount outstanding -- the issues forced sellers actually traded.

    Args:
        universe: Static frame with ``security``/``maturity``/``cpn``/
            ``amt_outstanding`` columns (case-insensitive).
        horizon_end: Last date of the forward window.
        max_bonds: Bonds to keep per event.
        min_amount: Minimum amount outstanding (USD). Lower than the live-trade
            screen on purpose: older events had smaller capital structures.

    Returns:
        The chosen rows, largest first.

    Raises:
        KeyError: If a required column is missing.
        RuntimeError: If no bond survives the screen.
    """
    out = universe.copy()
    out.columns = [str(c).lower() for c in out.columns]
    for col in ("security", "maturity", "cpn", "amt_outstanding"):
        if col not in out.columns:
            raise KeyError(
                f"select_event_bonds: missing column '{col}'. Available: "
                f"{list(out.columns)}. Add the mnemonic to the static fields "
                f"pulled for the event (config.bloomberg.static_fields)."
            )
    out["maturity"] = pd.to_datetime(out["maturity"], errors="coerce")
    out["amt_outstanding"] = pd.to_numeric(out["amt_outstanding"], errors="coerce")
    out["cpn"] = pd.to_numeric(out["cpn"], errors="coerce")

    mask = (out["maturity"] >= pd.Timestamp(horizon_end)) & (
        out["amt_outstanding"] >= min_amount
    )
    picked = (
        out.loc[mask]
        .sort_values("amt_outstanding", ascending=False)
        .head(max_bonds)
        .reset_index(drop=True)
    )
    if picked.empty:
        raise RuntimeError(
            f"No bonds survive the event window (maturity >= {horizon_end}, "
            f"amount >= {min_amount:,}). Lower min_amount or list explicit "
            f"`bonds:` for this event in the events YAML."
        )
    return picked


def make_bond_fetcher(
    cfg: TradeConfig,
    *,
    max_bonds: int = 3,
    min_amount: int = 300_000_000,
    refresh: bool = False,
    data_dir: Path | None = None,
) -> BondFetcher:
    """Build the default Bloomberg bond fetcher for :func:`run_backtest`.

    Uses the event's explicit ``bonds`` when given (static fields via ``bdp``),
    otherwise resolves the issuer's chain off its equity anchor. Prices are
    pulled as ``PX_LAST`` under the ``"backtest"`` cache scope.
    """

    def fetch(event: DowngradeEvent, start: date, end: date) -> tuple[pd.DataFrame, pd.Series]:
        if event.bonds:
            static = dp.get_bond_static(
                event.bonds,
                ["ID_CUSIP", "CPN", "MATURITY", "AMT_OUTSTANDING"],
                config=cfg,
                cache_scope=CACHE_SCOPE,
                refresh=refresh,
                data_dir=data_dir,
            )
        else:
            static = dp.get_bond_universe_for(
                event.equity_ticker,
                cfg.bloomberg.bond_chain_field,
                cfg.bloomberg.static_fields,
                cache_dir=CACHE_SCOPE,
                cache_label=f"chain_{event.ticker}",
                refresh=refresh,
                data_dir=data_dir,
            )
        picked = select_event_bonds(
            static, horizon_end=end, max_bonds=max_bonds, min_amount=min_amount
        )
        securities = picked["security"].tolist()
        tidy = dp.get_bond_timeseries(
            securities,
            start,
            end,
            ["PX_LAST"],
            config=cfg,
            cache_scope=CACHE_SCOPE,
            refresh=refresh,
            data_dir=data_dir,
        )
        if tidy.empty:
            raise RuntimeError(
                f"No PX_LAST history for {event.ticker} bonds {securities} over "
                f"{start}..{end}. Check each security resolves on DES<GO> and "
                f"was quoted in the window."
            )
        prices = tidy.pivot_table(
            index="date", columns="security", values="value", aggfunc="last"
        ).sort_index()
        coupons = picked.set_index("security")["cpn"]
        return prices, coupons

    return fetch


def make_hedge_fetcher(
    cfg: TradeConfig,
    *,
    field: str = HEDGE_TR_FIELD,
    refresh: bool = False,
    data_dir: Path | None = None,
) -> HedgeFetcher:
    """Build the default hedge fetcher: TR index for ``cfg.hedge.instrument``."""

    def fetch(start: date, end: date) -> pd.Series:
        tidy = dp.get_index_timeseries(
            [cfg.hedge.instrument],
            start,
            end,
            fields=[field],
            config=cfg,
            cache_scope=CACHE_SCOPE,
            refresh=refresh,
            data_dir=data_dir,
        )
        if tidy.empty:
            raise RuntimeError(
                f"No {field} history for {cfg.hedge.instrument} over "
                f"{start}..{end}. Try the alternatives noted on HEDGE_TR_FIELD."
            )
        wide = tidy.pivot_table(index="date", columns="security", values="value").sort_index()
        series = wide.iloc[:, 0].dropna()
        return (series / series.iloc[0]).rename("hedge_tr")

    return fetch


# --------------------------------------------------------------------------- #
# Backtest engine
# --------------------------------------------------------------------------- #
def run_backtest(
    events: list[DowngradeEvent],
    cfg: TradeConfig | None = None,
    *,
    horizon_days: int = 365,
    entry_lag_days: int = 0,
    hedge_ratio: float = 1.0,
    max_bonds: int = 3,
    min_amount: int = 300_000_000,
    bond_fetcher: BondFetcher | None = None,
    hedge_fetcher: HedgeFetcher | None = None,
    refresh: bool = False,
    data_dir: Path | None = None,
    return_paths: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Run the fallen-angel factor study over ``events``.

    Per event: equal-weight TR proxy across the selected bonds, hedge TR over
    the same window, excess path and summary metrics. A failing event is logged
    and recorded with NaN metrics plus an ``error`` string -- one bad chain
    never aborts the study, and coverage stays visible.

    Args:
        events: Downgrade events (see :func:`load_events`).
        cfg: Trade config supplying Bloomberg fields + hedge instrument.
            Required unless both fetchers are injected.
        horizon_days: Forward window in calendar days (365 = the 12M study).
        entry_lag_days: Days to wait after the downgrade before "entering";
            0 measures from the event itself, ~5 simulates realistic entry
            after the forced-selling rush.
        hedge_ratio: Hedge notional fraction (1.0 = beta-neutral study).
        max_bonds: Representative bonds per event.
        min_amount: Minimum amount outstanding for event bonds.
        bond_fetcher: Override the Bloomberg bond fetcher (tests/offline).
        hedge_fetcher: Override the hedge fetcher.
        refresh: Bypass parquet caches on the default fetchers.
        data_dir: Cache root override.
        return_paths: Also return ``{ticker: excess path}`` for charting.

    Returns:
        Results frame (one row per event), and the paths dict when
        ``return_paths`` is True.

    Raises:
        ValueError: If ``cfg`` is omitted while a default fetcher is needed.
    """
    if (bond_fetcher is None or hedge_fetcher is None) and cfg is None:
        raise ValueError(
            "run_backtest: pass cfg (for Bloomberg fields and the hedge "
            "instrument) or inject both bond_fetcher and hedge_fetcher."
        )
    if bond_fetcher is None:
        assert cfg is not None
        bond_fetcher = make_bond_fetcher(
            cfg, max_bonds=max_bonds, min_amount=min_amount,
            refresh=refresh, data_dir=data_dir,
        )
    if hedge_fetcher is None:
        assert cfg is not None
        hedge_fetcher = make_hedge_fetcher(cfg, refresh=refresh, data_dir=data_dir)

    rows: list[dict[str, object]] = []
    paths: dict[str, pd.Series] = {}
    for event in events:
        start = event.downgrade_date + timedelta(days=entry_lag_days)
        end = start + timedelta(days=horizon_days)
        base: dict[str, object] = {
            "issuer": event.issuer,
            "ticker": event.ticker,
            "sector": event.sector,
            "catalyst": event.catalyst,
            "event_date": pd.Timestamp(event.downgrade_date),
        }
        try:
            prices, coupons = bond_fetcher(event, start, end)
            hedge_tr = hedge_fetcher(start, end)
            tr_legs = [
                total_return_proxy(prices[sec], float(coupons.get(sec, 0.0)))
                for sec in prices.columns
            ]
            bond_tr = pd.concat(tr_legs, axis=1).mean(axis=1)
            bond_path, hedge_path = _aligned_cum_returns(bond_tr, hedge_tr)
            if bond_path.empty:
                raise RuntimeError("no overlapping dates between bond and hedge history")
            excess = (bond_path - hedge_ratio * hedge_path).rename("excess")
            rows.append(base | {
                "n_bonds": int(prices.shape[1]),
                "n_days": int(len(excess)),
                "bond_tr": float(bond_path.iloc[-1]),
                "hedge_tr": float(hedge_path.iloc[-1]),
                "excess": float(excess.iloc[-1]),
                "max_drawdown": max_drawdown(excess),
                "hit": bool(excess.iloc[-1] > 0),
                "error": None,
            })
            paths[event.ticker] = excess
        except Exception as exc:  # noqa: BLE001 - isolate per-event failures
            logger.warning(
                "Backtest event %s (%s) failed: %s", event.ticker, event.downgrade_date, exc
            )
            rows.append(base | {
                "n_bonds": 0, "n_days": 0,
                "bond_tr": np.nan, "hedge_tr": np.nan, "excess": np.nan,
                "max_drawdown": np.nan, "hit": None, "error": str(exc),
            })

    results = pd.DataFrame(rows)
    n_ok = int(results["error"].isna().sum())
    logger.info("run_backtest: %d/%d events measured", n_ok, len(results))
    return (results, paths) if return_paths else results


def summarize(results: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """Aggregate event results overall or stratified by a column.

    ``sharpe`` is cross-sectional: mean/std (ddof=1) of the per-event excess
    returns. The horizon is ~12 months, so the ratio is already on an annual
    basis and no further annualization is applied.

    Args:
        results: Output of :func:`run_backtest` (errored rows are dropped).
        by: Stratification column (``"sector"``, ``"catalyst"``) or None for a
            single ``ALL`` row.

    Returns:
        One row per group with n, hit_rate, excess stats, sharpe, and the
        drawdown distribution (median / worst).

    Raises:
        ValueError: If every event errored.
    """
    valid = results.dropna(subset=["excess"])
    if valid.empty:
        raise ValueError(
            "summarize: no valid events (all rows errored). Inspect "
            "results['error'] for the per-event failures."
        )

    def stats(group: pd.DataFrame) -> pd.Series:
        ex = group["excess"]
        std = ex.std(ddof=1)
        return pd.Series({
            "n": float(len(group)),
            "hit_rate": float((ex > 0).mean()),
            "excess_mean": float(ex.mean()),
            "excess_median": float(ex.median()),
            "excess_std": float(std) if len(group) > 1 else np.nan,
            "sharpe": float(ex.mean() / std) if len(group) > 1 and std > 0 else np.nan,
            "excess_worst": float(ex.min()),
            "excess_best": float(ex.max()),
            "dd_median": float(group["max_drawdown"].median()),
            "dd_worst": float(group["max_drawdown"].min()),
        })

    if by is None:
        return pd.DataFrame([stats(valid)], index=["ALL"])
    grouped = {key: stats(group) for key, group in valid.groupby(by)}
    return pd.DataFrame(grouped).T.rename_axis(by).sort_values("excess_mean", ascending=False)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the fallen-angel factor backtest (2015-2024 IG->HY downgrades)."
    )
    parser.add_argument(
        "--issuer", required=True,
        help="Issuer code whose config supplies Bloomberg fields + hedge (e.g. CNC)",
    )
    parser.add_argument("--events", default=None, help="Events YAML override")
    parser.add_argument("--horizon-days", type=int, default=365)
    parser.add_argument("--lag-days", type=int, default=0)
    parser.add_argument("--hedge-ratio", type=float, default=1.0)
    parser.add_argument("--refresh", action="store_true", help="Bypass parquet caches")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    dp.configure_logging(args.log_level)
    cfg = load_config(args.issuer)
    events = load_events(Path(args.events) if args.events else None)
    logger.info("Loaded %d downgrade events", len(events))

    results = run_backtest(
        events, cfg,
        horizon_days=args.horizon_days,
        entry_lag_days=args.lag_days,
        hedge_ratio=args.hedge_ratio,
        refresh=args.refresh,
    )

    print("\n=== Overall ===")
    print(summarize(results).round(3).to_string())
    print("\n=== By sector ===")
    print(summarize(results, by="sector").round(3).to_string())
    print("\n=== By catalyst ===")
    print(summarize(results, by="catalyst").round(3).to_string())

    failed = results[results["error"].notna()]
    if not failed.empty:
        print(f"\n=== Failed events ({len(failed)}) ===")
        print(failed[["ticker", "event_date", "error"]].to_string(index=False))

    out_dir = project_root() / "data" / CACHE_SCOPE
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"results_{date.today().isoformat()}.parquet"
    results.to_parquet(out_path, index=False)
    logger.info("Results written to %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
