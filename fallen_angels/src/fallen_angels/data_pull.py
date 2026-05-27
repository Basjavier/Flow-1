"""Bloomberg data pulls with on-disk parquet caching.

Three entry points:

* :func:`get_bond_universe`    -- static reference data for every bond of an issuer
* :func:`get_bond_timeseries`  -- historical fields for a list of bond securities
* :func:`get_index_timeseries` -- historical levels for ETFs / spread indices

Design rules honored here:

* Every Bloomberg call is wrapped so a missing terminal, bad field, or empty
  result raises a ``RuntimeError`` saying *what* failed and *what to check*.
* Every successful pull is written to ``data/<issuer>/<label>_<asof>.parquet``
  and re-served from disk on the next run (same calendar day) unless
  ``refresh=True``. The Bloomberg import is lazy, so this module imports (and
  caches can be read) on a machine without BLPAPI.
* Time series are returned in tidy long form -- columns
  ``[date, security, field, value]`` -- which round-trips cleanly through
  parquet (MultiIndex columns do not) and pivots to wide with one call.

Run ``python -m fallen_angels.data_pull --issuer CNC`` to verify the layer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from fallen_angels.config import TradeConfig, load_config, project_root

logger = logging.getLogger(__name__)

# Bare identifiers ending in one of these are already full Bloomberg securities.
_SECURITY_SUFFIXES = ("Corp", "Equity", "Index", "Govt", "Mtge")
_CUSIP_RE = re.compile(r"^[0-9A-Z]{9}$")


# --------------------------------------------------------------------------- #
# Bloomberg session (lazy)
# --------------------------------------------------------------------------- #
def _blp():
    """Import and return the xbbg ``blp`` handle, or raise an actionable error."""
    try:
        from xbbg import blp
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "xbbg could not be imported. Run `uv sync` to install dependencies, "
            "and confirm BLPAPI and a logged-in Bloomberg Terminal are available "
            "on this host."
        ) from exc
    return blp


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def _data_dir(data_dir: Path | None = None) -> Path:
    return data_dir or (project_root() / "data")


def _cache_path(issuer: str, label: str, asof: date, data_dir: Path | None = None) -> Path:
    """Build ``data/<issuer>/<label>_<asof>.parquet`` and ensure the dir exists."""
    folder = _data_dir(data_dir) / issuer.lower()
    folder.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")
    return folder / f"{safe_label}_{asof.isoformat()}.parquet"


def _read_cache(path: Path) -> pd.DataFrame | None:
    if path.exists():
        logger.info("Cache hit: %s", path)
        return pd.read_parquet(path)
    logger.debug("Cache miss: %s", path)
    return None


def _write_cache(df: pd.DataFrame, path: Path) -> None:
    df.to_parquet(path, index=False)
    logger.info("Cached %d rows -> %s", len(df), path)


def _hash_key(*parts: object) -> str:
    """Stable short hash of arbitrary query parameters, for cache labels."""
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


# --------------------------------------------------------------------------- #
# Identifier / argument normalization
# --------------------------------------------------------------------------- #
def _as_security(identifier: str) -> str:
    """Coerce a bond identifier into a Bloomberg security string.

    Already-qualified securities (``"... Corp"``) pass through. Bare 9-character
    CUSIPs are resolved via the ``/cusip/<id>`` namespace. Anything else is
    assumed to be a corp ticker and suffixed with ``" Corp"``.

    TODO: if the chain returns FIGIs, switch the fallback to ``/bbgid/<id>``;
    for ISINs use ``/isin/<id>``.
    """
    ident = str(identifier).strip()
    if ident.endswith(_SECURITY_SUFFIXES) or ident.startswith("/"):
        return ident
    if _CUSIP_RE.match(ident):
        return f"/cusip/{ident}"
    return f"{ident} Corp"


def _as_date_str(value: str | date | datetime) -> str:
    """Normalize a date-ish value to an ISO ``YYYY-MM-DD`` string for xbbg."""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _bdh_to_tidy(raw: pd.DataFrame | None) -> pd.DataFrame:
    """Convert an xbbg ``bdh`` frame (MultiIndex cols) to tidy long form.

    Output columns: ``date``, ``security``, ``field``, ``value``. NaNs (non
    trading days / unavailable fields) are dropped.
    """
    cols = ["date", "security", "field", "value"]
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=cols)
    frame = raw.copy()
    frame.index = pd.to_datetime(frame.index)
    frame.index.name = "date"
    if not isinstance(frame.columns, pd.MultiIndex):
        # Single (security, field) can collapse to a flat column index.
        frame.columns = pd.MultiIndex.from_product([["UNKNOWN"], list(frame.columns)])
    frame.columns = frame.columns.set_names(["security", "field"])
    tidy = (
        frame.stack(level=["security", "field"], future_stack=True)
        .rename("value")
        .reset_index()
    )
    tidy = tidy.dropna(subset=["value"]).reset_index(drop=True)
    return tidy[cols]


# --------------------------------------------------------------------------- #
# Public pulls
# --------------------------------------------------------------------------- #
def get_bond_universe_for(
    equity_ticker: str,
    chain_field: str,
    static_fields: list[str],
    *,
    cache_dir: str,
    cache_label: str,
    refresh: bool = False,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Return static reference data for every bond hanging off ``equity_ticker``.

    Config-free primitive: resolves the bond chain via
    ``bds(equity_ticker, chain_field)`` then pulls ``static_fields`` per member
    via ``bdp``. Used directly for peer issuers (``comparables.py``), which have
    no config file of their own.

    Args:
        equity_ticker: Bloomberg equity anchor for the bond-chain lookup.
        chain_field: ``bds`` field returning the chain members.
        static_fields: Static mnemonics to pull per bond.
        cache_dir: Subdirectory under ``data/`` to cache into (e.g. the target
            issuer's short code, so peer pulls live alongside it).
        cache_label: Cache file label (e.g. ``"universe"`` or ``"peer_HCA"``).
        refresh: If True, bypass the cache and re-pull from Bloomberg.
        data_dir: Cache root override (defaults to ``<root>/data``).

    Returns:
        One row per bond, with a ``security`` column plus the static fields
        (lower-cased mnemonics as returned by xbbg).

    Raises:
        RuntimeError: If the chain lookup or static pull fails or is empty.
    """
    asof = date.today()
    path = _cache_path(cache_dir, cache_label, asof, data_dir)
    if not refresh:
        cached = _read_cache(path)
        if cached is not None:
            return cached

    blp = _blp()
    try:
        chain = blp.bds(equity_ticker, chain_field)
    except Exception as exc:  # noqa: BLE001 - surface any BBG error with guidance
        raise RuntimeError(
            f"Bloomberg bds({equity_ticker!r}, {chain_field!r}) failed: {exc}. "
            f"Check the Terminal is logged in and the field is valid. "
            f"Alternatives to try for the chain field: 'CAPITAL_STRUCTURE', "
            f"'CURVE_MEMBERS'; or run CSHF<GO> on {equity_ticker} and export the curve."
        ) from exc

    members = _extract_chain_members(chain)
    if not members:
        raise RuntimeError(
            f"No bonds resolved for {equity_ticker!r} via field {chain_field!r}. "
            f"Check that field returns a member list (try 'CAPITAL_STRUCTURE' or "
            f"'CURVE_MEMBERS') and that {equity_ticker} has an active bond curve."
        )
    logger.info("Resolved %d bond-chain members for %s", len(members), equity_ticker)

    static = _pull_static(blp, members, static_fields)
    _write_cache(static, path)
    return static


def get_bond_universe(
    issuer: str,
    *,
    refresh: bool = False,
    config: TradeConfig | None = None,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Return static reference data for every bond of ``issuer`` (config-driven).

    Thin wrapper over :func:`get_bond_universe_for` using the issuer's config.
    No universe filtering is applied here -- that happens in ``universe.py``.

    Args:
        issuer: Issuer code matching ``config/<issuer>.yaml``.
        refresh: If True, bypass the cache and re-pull from Bloomberg.
        config: Pre-loaded config (loaded from disk if omitted).
        data_dir: Cache root override (defaults to ``<root>/data``).

    Returns:
        One row per bond (see :func:`get_bond_universe_for`).
    """
    cfg = config or load_config(issuer)
    return get_bond_universe_for(
        cfg.issuer.equity_ticker,
        cfg.bloomberg.bond_chain_field,
        cfg.bloomberg.static_fields,
        cache_dir=cfg.issuer.short,
        cache_label="universe",
        refresh=refresh,
        data_dir=data_dir,
    )


def get_bond_timeseries(
    cusips: list[str],
    start: str | date,
    end: str | date,
    fields: list[str],
    *,
    issuer: str = "CNC",
    refresh: bool = False,
    config: TradeConfig | None = None,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Return historical ``fields`` for ``cusips`` over ``[start, end]``.

    Args:
        cusips: Bond identifiers (bare CUSIPs, or full ``"... Corp"`` securities).
        start: Inclusive start date (str ``YYYY-MM-DD`` or date/datetime).
        end: Inclusive end date.
        fields: Bloomberg timeseries mnemonics (e.g. ``["Z_SPRD_MID"]``).
        issuer: Issuer code, used only to scope the cache directory.
        refresh: If True, bypass cache and re-pull.
        config: Pre-loaded config (loaded from disk if omitted).
        data_dir: Cache root override.

    Returns:
        Tidy long frame: ``[date, security, field, value]``.

    Raises:
        RuntimeError: If the Bloomberg history pull fails.
    """
    cfg = config or load_config(issuer)
    asof = date.today()
    start_s, end_s = _as_date_str(start), _as_date_str(end)
    label = f"bonds_{_hash_key(sorted(cusips), sorted(fields), start_s, end_s)}"
    path = _cache_path(cfg.issuer.short, label, asof, data_dir)
    if not refresh:
        cached = _read_cache(path)
        if cached is not None:
            return cached

    blp = _blp()
    securities = [_as_security(c) for c in cusips]
    try:
        raw = blp.bdh(securities, fields, start_s, end_s)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Bloomberg bdh() history pull failed for {len(securities)} bonds "
            f"over {start_s}..{end_s}: {exc}. Check the identifiers resolve "
            f"(try '<CUSIP> Corp' form) and that fields {fields} are valid."
        ) from exc

    tidy = _bdh_to_tidy(raw)
    _write_cache(tidy, path)
    return tidy


def get_index_timeseries(
    tickers: list[str],
    start: str | date,
    end: str | date,
    *,
    fields: list[str] | None = None,
    issuer: str = "CNC",
    refresh: bool = False,
    config: TradeConfig | None = None,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Return historical levels for benchmark ``tickers`` (ETFs, spread indices).

    Args:
        tickers: Full Bloomberg securities (e.g. ``"HYG US Equity"``,
            ``"H0A0 Index"``).
        start: Inclusive start date.
        end: Inclusive end date.
        fields: Mnemonics to pull; defaults to ``["PX_LAST"]``.
        issuer: Issuer code, used only to scope the cache directory.
        refresh: If True, bypass cache and re-pull.
        config: Pre-loaded config (loaded from disk if omitted).
        data_dir: Cache root override.

    Returns:
        Tidy long frame: ``[date, security, field, value]``.

    Raises:
        RuntimeError: If the Bloomberg history pull fails.
    """
    cfg = config or load_config(issuer)
    fields = fields or ["PX_LAST"]
    asof = date.today()
    start_s, end_s = _as_date_str(start), _as_date_str(end)
    label = f"indices_{_hash_key(sorted(tickers), sorted(fields), start_s, end_s)}"
    path = _cache_path(cfg.issuer.short, label, asof, data_dir)
    if not refresh:
        cached = _read_cache(path)
        if cached is not None:
            return cached

    blp = _blp()
    try:
        raw = blp.bdh(tickers, fields, start_s, end_s)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Bloomberg bdh() history pull failed for indices {tickers} over "
            f"{start_s}..{end_s}: {exc}. Check each ticker is a valid security "
            f"(e.g. 'HYG US Equity', 'H0A0 Index') and fields {fields} exist."
        ) from exc

    tidy = _bdh_to_tidy(raw)
    _write_cache(tidy, path)
    return tidy


# --------------------------------------------------------------------------- #
# Internal helpers for the universe pull
# --------------------------------------------------------------------------- #
def _extract_chain_members(chain: pd.DataFrame | None) -> list[str]:
    """Pull the list of member security identifiers from a ``bds`` chain frame.

    ``bds`` returns the member identifiers in its first (often only) data
    column. Bare identifiers are normalized to full securities via
    :func:`_as_security`.

    TODO: if the chosen chain field returns multiple columns (e.g. id + weight),
    select the id column explicitly rather than positionally.
    """
    if chain is None or chain.empty:
        return []
    col = chain.columns[0]
    values = chain[col].dropna().astype(str).tolist()
    return [_as_security(v) for v in values]


def _pull_static(blp, securities: list[str], fields: list[str]) -> pd.DataFrame:
    """Pull static reference ``fields`` for ``securities`` via ``bdp``."""
    try:
        df = blp.bdp(securities, fields)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Bloomberg bdp() static pull failed for {len(securities)} "
            f"securities: {exc}. Check fields {fields} are valid mnemonics "
            f"(e.g. ID_CUSIP, MATURITY, AMT_OUTSTANDING)."
        ) from exc
    return df.rename_axis("security").reset_index()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def configure_logging(level: str = "INFO") -> None:
    """Initialize stdlib logging for CLI / notebook use."""
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Bloomberg data layer for a fallen-angel issuer."
    )
    parser.add_argument(
        "--issuer", required=True, help="Issuer code matching config/<issuer>.yaml (e.g. CNC)"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Bypass cache and re-pull from Bloomberg"
    )
    parser.add_argument(
        "--days", type=int, default=30, help="Trailing days of index history to sample"
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    cfg = load_config(args.issuer)

    universe = get_bond_universe(args.issuer, refresh=args.refresh, config=cfg)
    logger.info("Universe: %d bonds for %s", len(universe), cfg.issuer.short)
    print("\n=== Bond universe (head) ===")
    print(universe.head(20).to_string(index=False))

    end = date.today()
    start = end - timedelta(days=args.days)
    indices = get_index_timeseries(
        cfg.indices, start, end, refresh=args.refresh, config=cfg
    )
    n_series = indices["security"].nunique() if not indices.empty else 0
    logger.info("Index sample: %d rows across %d series", len(indices), n_series)
    print(f"\n=== Index sample (last {args.days}d, tail) ===")
    print(indices.tail(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
