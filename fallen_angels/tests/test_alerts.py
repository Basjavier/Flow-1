"""Offline tests for alerts.py: evaluators, persistence, top-level wiring."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from fallen_angels import alerts


# --------------------------------------------------------------------------- #
# evaluate_signal
# --------------------------------------------------------------------------- #
def _signal_row(z: float, diff: float = 120.0) -> pd.DataFrame:
    return pd.DataFrame([{
        "date": pd.Timestamp("2026-05-27"),
        "target_wavg": 480.0, "peer_wavg": 360.0,
        "differential": diff, "z_score": z,
        "entry": z >= 1.5, "exit_converge": z <= 0.5,
    }])


def test_evaluate_signal_empty_frame_warns(cfg):
    out = alerts.evaluate_signal(pd.DataFrame(), cfg, issuer="CNC")
    assert len(out) == 1 and out[0].severity == "WARN"
    assert out[0].kind == "data_quality"


def test_evaluate_signal_emits_entry(cfg):
    out = alerts.evaluate_signal(_signal_row(z=1.8), cfg, issuer="CNC")
    assert out[0].kind == "signal_entry" and out[0].severity == "WARN"
    assert out[0].payload["z_score"] == pytest.approx(1.8)


def test_evaluate_signal_emits_exit(cfg):
    out = alerts.evaluate_signal(_signal_row(z=0.3), cfg, issuer="CNC")
    assert out[0].kind == "signal_exit"


def test_evaluate_signal_emits_hold_in_band(cfg):
    out = alerts.evaluate_signal(_signal_row(z=1.0), cfg, issuer="CNC")
    assert out[0].kind == "signal_hold" and out[0].severity == "INFO"


def test_evaluate_signal_warmup_on_nan_z(cfg):
    out = alerts.evaluate_signal(_signal_row(z=float("nan")), cfg, issuer="CNC")
    assert out[0].kind == "signal_warmup"


# --------------------------------------------------------------------------- #
# evaluate_spread_stop
# --------------------------------------------------------------------------- #
def test_evaluate_spread_stop_triggers_at_threshold(cfg):
    out = alerts.evaluate_spread_stop(380.0, 485.0, cfg, issuer="CNC")
    assert len(out) == 1 and out[0].severity == "CRITICAL"
    assert out[0].payload["widening_bps"] == pytest.approx(105.0)


def test_evaluate_spread_stop_quiet_when_within_band(cfg):
    assert alerts.evaluate_spread_stop(380.0, 470.0, cfg, issuer="CNC") == []


# --------------------------------------------------------------------------- #
# evaluate_time_stop
# --------------------------------------------------------------------------- #
def test_evaluate_time_stop_within_window_quiet(cfg):
    out = alerts.evaluate_time_stop(date(2026, 4, 20), cfg, issuer="CNC", asof=date(2026, 9, 20))
    assert out == []


def test_evaluate_time_stop_warn_near_limit(cfg):
    out = alerts.evaluate_time_stop(date(2026, 4, 20), cfg, issuer="CNC", asof=date(2027, 9, 25))
    assert len(out) == 1 and out[0].kind == "time_stop_warn"


def test_evaluate_time_stop_critical_at_breach(cfg):
    out = alerts.evaluate_time_stop(date(2026, 4, 20), cfg, issuer="CNC", asof=date(2027, 11, 1))
    assert len(out) == 1 and out[0].severity == "CRITICAL"


# --------------------------------------------------------------------------- #
# evaluate_data_quality
# --------------------------------------------------------------------------- #
def test_evaluate_data_quality_warns_on_missing_price():
    marks = pd.DataFrame({"price": [100.0, float("nan")]}, index=["A", "B"])
    out = alerts.evaluate_data_quality(marks, issuer="CNC")
    kinds = {a.kind for a in out}
    severities = {a.severity for a in out}
    assert "data_quality" in kinds
    assert "WARN" in severities


def test_evaluate_data_quality_info_on_missing_zspread():
    marks = pd.DataFrame({
        "price": [100.0, 99.0], "zspread": [400.0, float("nan")],
    }, index=["A", "B"])
    out = alerts.evaluate_data_quality(marks, issuer="CNC")
    assert any(a.severity == "INFO" and "Z-spread" in a.message for a in out)


def test_evaluate_data_quality_empty_frame():
    out = alerts.evaluate_data_quality(pd.DataFrame(), issuer="CNC")
    assert len(out) == 1 and out[0].severity == "WARN"


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_write_and_read_log_roundtrip(tmp_path: Path):
    now = datetime.now(timezone.utc)
    sample = [
        alerts.Alert(timestamp=now, issuer="CNC", kind="signal_entry",
                     severity="WARN", message="m1", payload={"z": 1.8}),
        alerts.Alert(timestamp=now, issuer="CNC", kind="signal_hold",
                     severity="INFO", message="m2"),
    ]
    log = tmp_path / "alerts.jsonl"
    assert alerts.write_log(sample, log) == 2
    restored = alerts.read_log(log)
    assert len(restored) == 2
    assert restored[0].kind == "signal_entry"
    assert restored[0].payload == {"z": 1.8}


def test_read_log_returns_empty_for_missing_file(tmp_path: Path):
    assert alerts.read_log(tmp_path / "absent.jsonl") == []


def test_write_log_noop_on_empty(tmp_path: Path):
    log = tmp_path / "alerts.jsonl"
    assert alerts.write_log([], log) == 0
    assert not log.exists()


# --------------------------------------------------------------------------- #
# Top-level evaluate_all
# --------------------------------------------------------------------------- #
def test_evaluate_all_runs_provided_evaluators(cfg, tmp_path: Path):
    log = tmp_path / "alerts.jsonl"
    out = alerts.evaluate_all(
        cfg, issuer="CNC",
        signal_df=_signal_row(z=1.8),
        entry_spread_bps=380.0, current_spread_bps=410.0,
        entry_date=date(2026, 4, 20),
        marks=pd.DataFrame({"price": [100.0]}, index=["A"]),
        log_path=log,
    )
    kinds = {a.kind for a in out}
    assert "signal_entry" in kinds
    assert log.exists() and len(alerts.read_log(log)) == len(out)


def test_evaluate_all_skips_when_inputs_missing(cfg, tmp_path: Path):
    log = tmp_path / "alerts.jsonl"
    out = alerts.evaluate_all(cfg, issuer="CNC", log_path=log, persist=False)
    assert out == []


def test_sinks_are_explicitly_unwired():
    sample = alerts.Alert(
        timestamp=datetime.now(timezone.utc), issuer="CNC",
        kind="signal_hold", severity="INFO", message="x",
    )
    with pytest.raises(NotImplementedError, match="email_sink"):
        alerts.email_sink([sample], to="x@y.com")
    with pytest.raises(NotImplementedError, match="slack_sink"):
        alerts.slack_sink([sample], webhook_url="https://example/hooks/x")
