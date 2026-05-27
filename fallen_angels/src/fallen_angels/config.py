"""Typed configuration for fallen-angel trades.

A trade is described entirely by a YAML file under ``config/`` named after the
issuer code (``config/cnc.yaml`` for issuer ``CNC``). This module validates
that file against a pydantic schema so the rest of the codebase relies on
typed, present fields and never hardcodes issuer-specific strings.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


def project_root() -> Path:
    """Return the project root (the directory containing ``config/``).

    ``config.py`` lives at ``src/fallen_angels/config.py``; the root is three
    parents up.
    """
    return Path(__file__).resolve().parents[2]


class _Strict(BaseModel):
    """Base model that rejects unknown keys so config typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class Issuer(_Strict):
    name: str
    short: str
    equity_ticker: str
    corp_ticker: str
    downgrade_date: date
    prior_rating: str
    new_rating: str
    rating_agency: str


class Universe(_Strict):
    maturity_min: date
    maturity_max: date
    min_amount_outstanding_usd: int
    seniority: list[str]
    currency: str


class Peer(_Strict):
    name: str
    ticker: str
    rationale: str


class Hedge(_Strict):
    instrument: str
    notional_ratio_low: float
    notional_ratio_high: float


class BloombergFields(_Strict):
    bond_chain_field: str
    static_fields: list[str]
    timeseries_fields: list[str]


class Thresholds(_Strict):
    entry_z: float
    exit_converge_z: float
    stop_widen_bps: float
    max_hold_months: int
    zscore_window_days: int


class Dates(_Strict):
    history_start: date
    lookback_years: int


class TradeConfig(_Strict):
    """Full validated configuration for one fallen-angel trade."""

    issuer: Issuer
    universe: Universe
    peers: list[Peer]
    hedge: Hedge
    indices: list[str]
    bloomberg: BloombergFields
    thresholds: Thresholds
    dates: Dates


def load_config(issuer: str, config_dir: Path | None = None) -> TradeConfig:
    """Load and validate the YAML config for ``issuer``.

    Args:
        issuer: Issuer code, matched case-insensitively to ``<issuer>.yaml``
            (e.g. ``"CNC"`` -> ``config/cnc.yaml``).
        config_dir: Directory holding configs. Defaults to ``<root>/config``.

    Returns:
        A validated :class:`TradeConfig`.

    Raises:
        FileNotFoundError: If no matching YAML file exists.
        ValueError: If the file exists but fails schema validation.
    """
    config_dir = config_dir or (project_root() / "config")
    path = config_dir / f"{issuer.lower()}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No config for issuer '{issuer}': expected {path}. "
            f"Check the issuer code matches a file in {config_dir} "
            f"(e.g. 'CNC' -> cnc.yaml)."
        )
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        cfg = TradeConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            f"Config {path} failed validation. "
            f"Check field names/types against fallen_angels.config.TradeConfig:\n{exc}"
        ) from exc
    logger.info("Loaded config for %s (%s) from %s", cfg.issuer.name, cfg.issuer.short, path)
    return cfg
