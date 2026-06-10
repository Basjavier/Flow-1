"""Position tracking and daily P&L decomposition.

A trade has two leg types: senior unsecured bonds (long) and an ETF hedge
(short HYG). Position state lives in a :class:`Portfolio`, serialized to/from
YAML under ``config/positions/<name>.yaml``. The daily decomposition splits
each bond's pnl into:

* ``carry``         -- coupon accrual: ``coupon_pct * face * days / 365.25``
* ``spread_pnl``    -- linear DV01 attribution of the Z-spread change
* ``benchmark_pnl`` -- residual price change (= ``price_pnl - spread_pnl``)
* ``price_pnl``     -- total clean-price move in USD on the bond
* ``hedge_pnl``     -- share-price move on the ETF leg
* ``total_pnl``     -- ``carry + price_pnl`` for bonds, ``hedge_pnl`` otherwise

The linear approximation is shared with :mod:`fallen_angels.risk` so scenario
shocks and live attribution use the same formulas.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Iterable, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(_Strict):
    """One leg of the trade.

    Bonds: ``quantity`` is USD face, prices are per 100 par, ``coupon_pct``
    drives carry, ``modified_duration`` drives spread attribution.

    Hedge: ``quantity`` is shares, prices are per share, coupon is 0 (any
    distributions show up via the TR field upstream).

    ``side`` ("long"/"short") is authoritative; ``quantity`` is always positive.
    """

    security: str
    kind: Literal["bond", "hedge"]
    side: Literal["long", "short"]
    quantity: float = Field(gt=0)
    entry_date: date
    entry_price: float
    entry_spread_bps: float | None = None  # bonds only
    coupon_pct: float = 0.0
    modified_duration: float | None = None  # years
    label: str | None = None

    @property
    def side_sign(self) -> int:
        return 1 if self.side == "long" else -1

    @property
    def display(self) -> str:
        return self.label or self.security


class PortfolioConfig(_Strict):
    """Top-level shape of a positions YAML file."""

    name: str
    issuer: str
    positions: list[Position]


class Portfolio:
    """Container for one trade's positions with daily-P&L methods."""

    def __init__(
        self,
        positions: Iterable[Position] | None = None,
        *,
        name: str = "default",
        issuer: str = "",
    ) -> None:
        self.name = name
        self.issuer = issuer
        self.positions: list[Position] = list(positions or [])

    def add(self, position: Position) -> None:
        self.positions.append(position)

    def by_kind(self, kind: str) -> list[Position]:
        return [p for p in self.positions if p.kind == kind]

    def labels(self) -> list[str]:
        return [p.display for p in self.positions]

    # --------------------------------------------------------------------- #
    # Serialization
    # --------------------------------------------------------------------- #
    @classmethod
    def from_yaml(cls, path: Path) -> "Portfolio":
        if not path.exists():
            raise FileNotFoundError(
                f"Portfolio YAML not found: {path}. See config/positions/ for "
                f"the PortfolioConfig schema (name/issuer/positions)."
            )
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            cfg = PortfolioConfig.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(
                f"{path} failed validation against PortfolioConfig:\n{exc}"
            ) from exc
        logger.info(
            "Loaded portfolio %s (%s): %d positions from %s",
            cfg.name, cfg.issuer, len(cfg.positions), path,
        )
        return cls(cfg.positions, name=cfg.name, issuer=cfg.issuer)

    def to_yaml(self, path: Path) -> None:
        payload = {
            "name": self.name,
            "issuer": self.issuer,
            "positions": [p.model_dump(mode="json") for p in self.positions],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    # --------------------------------------------------------------------- #
    # Reporting
    # --------------------------------------------------------------------- #
    def market_value(self, marks: pd.DataFrame) -> pd.Series:
        """Per-position signed USD market value (long > 0, short < 0)."""
        out: dict[str, float] = {}
        for p in self.positions:
            price = _lookup_price(marks, p)
            out[p.display] = position_market_value(p, price)
        return pd.Series(out, name="market_value")

    def reconcile(self, marks: pd.DataFrame) -> pd.DataFrame:
        """Mark each position against entry: price drift + unrealized USD."""
        rows = []
        for p in self.positions:
            price = _lookup_price(marks, p)
            unrealized = position_market_value(p, price) - position_market_value(p, p.entry_price)
            rows.append({
                "position": p.display,
                "security": p.security,
                "kind": p.kind,
                "side": p.side,
                "quantity": p.quantity,
                "entry_price": p.entry_price,
                "curr_price": price,
                "price_drift": price - p.entry_price,
                "unrealized_pnl": unrealized,
            })
        return pd.DataFrame(rows)

    def daily_pnl(
        self,
        marks_today: pd.DataFrame,
        marks_yesterday: pd.DataFrame,
        *,
        days: float = 1.0,
    ) -> pd.DataFrame:
        """Per-position P&L decomposition between two marks frames.

        Marks frames: index = security, columns ``price`` (required) and
        optionally ``zspread`` (bps; enables the spread attribution).
        """
        rows = [
            decompose_pnl(p, marks_yesterday, marks_today, days=days)
            for p in self.positions
        ]
        return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Marks lookup + pure P&L building blocks (reused by risk.py)
# --------------------------------------------------------------------------- #
def _lookup_price(marks: pd.DataFrame, position: Position) -> float:
    if position.security not in marks.index:
        sample = list(marks.index)[:5]
        raise KeyError(
            f"Marks frame missing security {position.security!r} for position "
            f"{position.display}. Available (sample): {sample}..."
        )
    value = marks.at[position.security, "price"]
    return float("nan") if pd.isna(value) else float(value)


def _lookup_spread(marks: pd.DataFrame, position: Position) -> float | None:
    if "zspread" not in marks.columns or position.security not in marks.index:
        return None
    value = marks.at[position.security, "zspread"]
    return None if pd.isna(value) else float(value)


def position_market_value(position: Position, price: float) -> float:
    """Signed USD market value (long > 0, short < 0)."""
    sign = position.side_sign
    if position.kind == "bond":
        return sign * price / 100.0 * position.quantity
    return sign * price * position.quantity


def position_carry(position: Position, days: float) -> float:
    """Coupon accrual in USD over ``days`` (0 for hedge / zero-coupon)."""
    if position.kind != "bond" or position.coupon_pct == 0.0:
        return 0.0
    return (
        position.side_sign
        * position.coupon_pct
        / 100.0
        * position.quantity
        * days
        / 365.25
    )


def bond_price_pnl(position: Position, prev_price: float, curr_price: float) -> float:
    """USD P&L on a bond from clean-price change (no carry)."""
    return position.side_sign * (curr_price - prev_price) / 100.0 * position.quantity


def hedge_price_pnl(position: Position, prev_price: float, curr_price: float) -> float:
    """USD P&L on a hedge (ETF) from share-price change."""
    return position.side_sign * (curr_price - prev_price) * position.quantity


def bond_spread_attribution(
    position: Position,
    prev_price: float,
    prev_spread: float,
    curr_spread: float,
) -> float:
    """Linear DV01 attribution: how much of price_pnl is the Z-spread move.

    ``dPrice ≈ -ModDur * dYield * Price``. Returns 0.0 silently when no
    duration is set so the decomposition still runs (benchmark_pnl absorbs
    the whole price move).
    """
    if position.modified_duration is None:
        return 0.0
    d_bps = curr_spread - prev_spread
    d_price_per_100 = -position.modified_duration * d_bps / 10000.0 * prev_price
    return position.side_sign * d_price_per_100 / 100.0 * position.quantity


def decompose_pnl(
    position: Position,
    marks_yesterday: pd.DataFrame,
    marks_today: pd.DataFrame,
    *,
    days: float = 1.0,
) -> dict[str, object]:
    """One decomposition row for ``position``. See :meth:`Portfolio.daily_pnl`."""
    prev_price = _lookup_price(marks_yesterday, position)
    curr_price = _lookup_price(marks_today, position)
    row: dict[str, object] = {
        "position": position.display,
        "security": position.security,
        "kind": position.kind,
        "side": position.side,
        "quantity": position.quantity,
        "prev_price": prev_price,
        "curr_price": curr_price,
        "carry": 0.0,
        "spread_pnl": 0.0,
        "benchmark_pnl": 0.0,
        "price_pnl": 0.0,
        "hedge_pnl": 0.0,
        "prev_spread": None,
        "curr_spread": None,
    }
    if position.kind == "bond":
        carry = position_carry(position, days)
        price_pnl = bond_price_pnl(position, prev_price, curr_price)
        prev_spread = _lookup_spread(marks_yesterday, position)
        curr_spread = _lookup_spread(marks_today, position)
        if prev_spread is not None and curr_spread is not None:
            spread_pnl = bond_spread_attribution(position, prev_price, prev_spread, curr_spread)
        else:
            spread_pnl = 0.0
        bench_pnl = price_pnl - spread_pnl
        row.update(
            carry=carry, spread_pnl=spread_pnl, benchmark_pnl=bench_pnl,
            price_pnl=price_pnl, total_pnl=carry + price_pnl,
            prev_spread=prev_spread, curr_spread=curr_spread,
        )
    else:
        hedge_pnl = hedge_price_pnl(position, prev_price, curr_price)
        row.update(hedge_pnl=hedge_pnl, total_pnl=hedge_pnl)
    return row


def marks_from_tidy(
    tidy: pd.DataFrame,
    *,
    asof: pd.Timestamp | None = None,
    price_field: str = "PX_LAST",
    spread_field: str | None = None,
) -> pd.DataFrame:
    """Build a marks frame (index=security, cols=price[,zspread]) from tidy ts.

    Picks the latest available row per security on or before ``asof`` (defaults
    to the maximum date present). Glue between
    :mod:`fallen_angels.data_pull` and :meth:`Portfolio.daily_pnl`.
    """
    if tidy.empty:
        raise ValueError(
            "marks_from_tidy: empty tidy frame. Did data_pull return zero "
            "rows? Re-pull with --refresh or widen the date range."
        )
    df = tidy.copy()
    df["date"] = pd.to_datetime(df["date"])
    if asof is not None:
        df = df[df["date"] <= pd.Timestamp(asof)]
    df = df.sort_values("date")

    def _latest(field: str) -> pd.Series:
        sub = df[df["field"].astype(str).str.upper() == field.upper()]
        return sub.groupby("security")["value"].last()

    prices = _latest(price_field).rename("price")
    if prices.empty:
        available = sorted(df["field"].astype(str).unique())
        raise ValueError(
            f"marks_from_tidy: no rows for price_field {price_field!r}. "
            f"Available fields: {available}."
        )
    out = prices.to_frame()
    if spread_field is not None:
        out["zspread"] = _latest(spread_field)
    return out
