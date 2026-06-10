"""Offline tests for comparables.py: weighting schemes and basket collapse."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fallen_angels import comparables as comp


@pytest.fixture()
def basket() -> pd.DataFrame:
    return pd.DataFrame({
        "security": ["H1 Corp", "H2 Corp", "M1 Corp"],
        "peer": ["HCA", "HCA", "MOH"],
        "amt_outstanding": [1e9, 1e9, 2e9],
    })


def test_equal_issuer_weights_split_by_name_then_amount(basket):
    weights = comp.assign_basket_weights(basket, scheme="equal_issuer", group_col="peer")
    # HCA gets 0.5 split equally (equal amounts); MOH alone gets 0.5.
    assert np.allclose(weights.to_numpy(), [0.25, 0.25, 0.5])
    assert weights.sum() == pytest.approx(1.0)


def test_amount_and_equal_bond_schemes(basket):
    amount = comp.assign_basket_weights(basket, scheme="amount")
    assert np.allclose(amount.to_numpy(), [0.25, 0.25, 0.5])
    equal = comp.assign_basket_weights(basket, scheme="equal_bond")
    assert np.allclose(equal.to_numpy(), [1 / 3] * 3)


def test_unknown_scheme_raises(basket):
    with pytest.raises(ValueError, match="Unknown weighting scheme"):
        comp.assign_basket_weights(basket, scheme="bogus")


def test_to_security_weights_aggregates_and_normalizes(basket):
    basket["weight"] = comp.assign_basket_weights(basket, scheme="equal_issuer", group_col="peer").to_numpy()
    collapsed = comp.to_security_weights(basket)
    assert collapsed.sum() == pytest.approx(1.0)
    assert collapsed["M1 Corp"] == pytest.approx(0.5)


def test_to_security_weights_requires_weight_column(basket):
    with pytest.raises(KeyError, match="Assign weights first"):
        comp.to_security_weights(basket.drop(columns=["amt_outstanding"]))


def test_peer_tickers_reads_config(cfg):
    tickers = comp.peer_tickers(cfg)
    assert len(tickers) == len(cfg.peers)
    assert all(t.endswith("Equity") for t in tickers)
