"""Offline tests for portfolio.py: positions, P&L decomposition, YAML round-trip."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fallen_angels import portfolio as pf
from fallen_angels.config import project_root


def _bond(**overrides) -> pf.Position:
    base = dict(
        security="CNC 5.25 2029 Corp", kind="bond", side="long",
        quantity=1_000_000, entry_date=date(2026, 4, 20), entry_price=90.0,
        entry_spread_bps=400.0, coupon_pct=5.0, modified_duration=4.0,
        label="CNC 5.25 2029",
    )
    return pf.Position(**(base | overrides))


def _hedge(**overrides) -> pf.Position:
    base = dict(
        security="HYG US Equity", kind="hedge", side="short", quantity=10_000,
        entry_date=date(2026, 4, 20), entry_price=78.5, coupon_pct=0.0,
        modified_duration=3.6, label="HYG hedge",
    )
    return pf.Position(**(base | overrides))


# --------------------------------------------------------------------------- #
# Position model
# --------------------------------------------------------------------------- #
def test_position_side_sign_and_display_fallback():
    assert _bond().side_sign == 1
    assert _hedge().side_sign == -1
    assert _bond(label=None).display == "CNC 5.25 2029 Corp"


def test_position_rejects_non_positive_quantity():
    with pytest.raises(Exception):
        _bond(quantity=0)
    with pytest.raises(Exception):
        _bond(quantity=-100)


def test_position_rejects_unknown_kind_or_side():
    with pytest.raises(Exception):
        _bond(kind="cds")
    with pytest.raises(Exception):
        _bond(side="flat")


# --------------------------------------------------------------------------- #
# P&L building blocks
# --------------------------------------------------------------------------- #
def test_position_market_value_signs():
    assert pf.position_market_value(_bond(), 100.0) == pytest.approx(1_000_000.0)
    assert pf.position_market_value(_hedge(), 80.0) == pytest.approx(-800_000.0)


def test_position_carry_accrues_only_for_bonds():
    carry = pf.position_carry(_bond(), days=30)
    assert carry == pytest.approx(0.05 * 1_000_000 * 30 / 365.25)
    assert pf.position_carry(_hedge(), days=30) == 0.0
    assert pf.position_carry(_bond(coupon_pct=0.0), days=30) == 0.0


def test_bond_price_pnl_signs_with_side():
    assert pf.bond_price_pnl(_bond(), 90.0, 92.0) == pytest.approx(20_000.0)
    assert pf.bond_price_pnl(_bond(side="short"), 90.0, 92.0) == pytest.approx(-20_000.0)


def test_hedge_price_pnl_short_profits_when_price_falls():
    assert pf.hedge_price_pnl(_hedge(), 78.5, 77.5) == pytest.approx(10_000.0)


def test_bond_spread_attribution_matches_dv01_formula():
    # ModDur=4, prev_price=90, dSpread = -20 bps, qty=1e6 face.
    # P&L = -4 * (-20/10000) * 90 * 1e6 / 100 = 7200
    assert pf.bond_spread_attribution(_bond(), 90.0, 400.0, 380.0) == pytest.approx(7_200.0)
    assert pf.bond_spread_attribution(_bond(modified_duration=None), 90.0, 400.0, 380.0) == 0.0


# --------------------------------------------------------------------------- #
# decompose_pnl + Portfolio.daily_pnl
# --------------------------------------------------------------------------- #
def _marks(price: dict, zspread: dict | None = None) -> pd.DataFrame:
    df = pd.DataFrame({"price": price})
    if zspread is not None:
        df["zspread"] = pd.Series(zspread)
    return df


def test_decompose_pnl_bond_balances_components():
    bond = _bond()
    prev = _marks({bond.security: 90.0}, {bond.security: 400.0})
    curr = _marks({bond.security: 92.0}, {bond.security: 380.0})

    row = pf.decompose_pnl(bond, prev, curr, days=30)

    assert row["carry"] == pytest.approx(0.05 * 1_000_000 * 30 / 365.25)
    assert row["price_pnl"] == pytest.approx(20_000.0)
    assert row["spread_pnl"] == pytest.approx(7_200.0)
    assert row["benchmark_pnl"] == pytest.approx(20_000.0 - 7_200.0)
    assert row["total_pnl"] == pytest.approx(row["carry"] + row["price_pnl"])


def test_decompose_pnl_bond_skips_attribution_without_spread_marks():
    bond = _bond()
    prev = _marks({bond.security: 90.0})  # no zspread column
    curr = _marks({bond.security: 92.0})

    row = pf.decompose_pnl(bond, prev, curr, days=1)
    assert row["spread_pnl"] == 0.0
    assert row["benchmark_pnl"] == pytest.approx(row["price_pnl"])


def test_decompose_pnl_hedge():
    hedge = _hedge()
    prev = _marks({hedge.security: 78.5})
    curr = _marks({hedge.security: 77.0})

    row = pf.decompose_pnl(hedge, prev, curr)
    assert row["carry"] == 0.0
    assert row["price_pnl"] == 0.0
    assert row["hedge_pnl"] == pytest.approx(15_000.0)  # short profits when HYG drops
    assert row["total_pnl"] == pytest.approx(15_000.0)


def test_decompose_pnl_missing_security_raises():
    bond = _bond()
    marks_empty = _marks({"NOT THIS Corp": 100.0})
    with pytest.raises(KeyError, match="Available"):
        pf.decompose_pnl(bond, marks_empty, marks_empty)


def test_portfolio_daily_pnl_concatenates_rows():
    book = pf.Portfolio([_bond(), _hedge()], name="cnc", issuer="CNC")
    prev = _marks(
        {"CNC 5.25 2029 Corp": 90.0, "HYG US Equity": 78.5},
        {"CNC 5.25 2029 Corp": 400.0},
    )
    curr = _marks(
        {"CNC 5.25 2029 Corp": 92.0, "HYG US Equity": 77.0},
        {"CNC 5.25 2029 Corp": 380.0},
    )
    df = book.daily_pnl(curr, prev, days=1)
    assert len(df) == 2
    bond_row = df[df["kind"] == "bond"].iloc[0]
    hedge_row = df[df["kind"] == "hedge"].iloc[0]
    assert bond_row["spread_pnl"] > 0
    assert hedge_row["hedge_pnl"] > 0


def test_portfolio_reconcile_unrealized():
    book = pf.Portfolio([_bond()], name="cnc", issuer="CNC")
    curr = _marks({"CNC 5.25 2029 Corp": 95.0})
    rec = book.reconcile(curr)
    # (95 - 90) / 100 * 1_000_000 = 50_000
    assert rec.iloc[0]["unrealized_pnl"] == pytest.approx(50_000.0)


# --------------------------------------------------------------------------- #
# Marks helper + YAML round-trip
# --------------------------------------------------------------------------- #
def test_marks_from_tidy_picks_latest_per_security():
    tidy = pd.DataFrame({
        "date": pd.to_datetime(["2026-04-01", "2026-04-02", "2026-04-02"]),
        "security": ["A Corp", "A Corp", "B Corp"],
        "field": ["PX_LAST", "PX_LAST", "PX_LAST"],
        "value": [100.0, 101.5, 99.0],
    })
    marks = pf.marks_from_tidy(tidy)
    assert marks.loc["A Corp", "price"] == 101.5
    assert marks.loc["B Corp", "price"] == 99.0


def test_marks_from_tidy_empty_raises():
    with pytest.raises(ValueError, match="empty tidy frame"):
        pf.marks_from_tidy(pd.DataFrame(columns=["date", "security", "field", "value"]))


def test_marks_from_tidy_unknown_price_field_raises():
    tidy = pd.DataFrame({
        "date": pd.to_datetime(["2026-04-01"]),
        "security": ["A Corp"], "field": ["PX_LAST"], "value": [100.0],
    })
    with pytest.raises(ValueError, match="Available fields"):
        pf.marks_from_tidy(tidy, price_field="OTHER")


def test_shipped_example_positions_yaml_loads():
    book = pf.Portfolio.from_yaml(project_root() / "config" / "positions" / "cnc_example.yaml")
    assert book.issuer == "CNC"
    assert len(book.positions) == 3
    assert {p.kind for p in book.positions} == {"bond", "hedge"}


def test_portfolio_yaml_roundtrip(tmp_path: Path):
    book = pf.Portfolio([_bond(), _hedge()], name="cnc_test", issuer="CNC")
    out = tmp_path / "book.yaml"
    book.to_yaml(out)
    restored = pf.Portfolio.from_yaml(out)
    assert restored.name == "cnc_test"
    assert restored.issuer == "CNC"
    assert len(restored.positions) == 2
    assert restored.positions[0].entry_date == date(2026, 4, 20)
