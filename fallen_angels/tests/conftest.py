"""Shared fixtures. Everything here runs offline -- no Bloomberg required."""

from __future__ import annotations

import pytest

from fallen_angels.config import TradeConfig, load_config


@pytest.fixture(scope="session")
def cfg() -> TradeConfig:
    """The shipped CNC config, used as a representative validated TradeConfig."""
    return load_config("CNC")
