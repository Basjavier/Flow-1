"""Threshold engine: signal state, P&L stops, time stops, data quality.

Each evaluator inspects current state (signal frame, entry vs current spread,
entry date, latest marks) and returns a list of :class:`Alert` objects. Alerts
are persisted as JSONL under ``data/<issuer>/alerts.jsonl`` (append-only) so a
daily run produces a tail-readable log. Email and Slack sinks are stubbed with
stable signatures, ready for a one-function wire-up.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from fallen_angels.config import TradeConfig, project_root

logger = logging.getLogger(__name__)

Severity = Literal["INFO", "WARN", "CRITICAL"]


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    issuer: str
    kind: str
    severity: Severity
    message: str
    payload: dict = Field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Evaluators
# --------------------------------------------------------------------------- #
def evaluate_signal(
    signal_df: pd.DataFrame | None,
    cfg: TradeConfig,
    *,
    issuer: str,
) -> list[Alert]:
    """Inspect the latest row of :func:`fallen_angels.signals.compute_signal`."""
    if signal_df is None or signal_df.empty:
        return [Alert(
            timestamp=_now(), issuer=issuer, kind="data_quality", severity="WARN",
            message="Signal frame is empty; cannot evaluate entry/exit thresholds.",
        )]
    row = signal_df.iloc[-1]
    z = row.get("z_score")
    diff = row.get("differential")
    payload = {
        "date": str(row.get("date")),
        "differential_bps": None if pd.isna(diff) else float(diff),
        "z_score": None if pd.isna(z) else float(z),
        "entry_z": cfg.thresholds.entry_z,
        "exit_converge_z": cfg.thresholds.exit_converge_z,
    }
    if pd.isna(z):
        return [Alert(
            timestamp=_now(), issuer=issuer, kind="signal_warmup", severity="INFO",
            message="Z-score not yet populated (rolling window not full).",
            payload=payload,
        )]
    if z >= cfg.thresholds.entry_z:
        return [Alert(
            timestamp=_now(), issuer=issuer, kind="signal_entry", severity="WARN",
            message=f"Differential z-score {z:.2f} >= entry {cfg.thresholds.entry_z}.",
            payload=payload,
        )]
    if z <= cfg.thresholds.exit_converge_z:
        return [Alert(
            timestamp=_now(), issuer=issuer, kind="signal_exit", severity="WARN",
            message=f"Differential z-score {z:.2f} <= exit {cfg.thresholds.exit_converge_z}.",
            payload=payload,
        )]
    return [Alert(
        timestamp=_now(), issuer=issuer, kind="signal_hold", severity="INFO",
        message=f"Holding: z-score {z:.2f} between exit and entry bands.",
        payload=payload,
    )]


def evaluate_spread_stop(
    entry_spread_bps: float,
    current_spread_bps: float,
    cfg: TradeConfig,
    *,
    issuer: str,
) -> list[Alert]:
    """CRITICAL if basket spread has widened more than ``thresholds.stop_widen_bps``."""
    widen = current_spread_bps - entry_spread_bps
    payload = {
        "entry_spread_bps": entry_spread_bps,
        "current_spread_bps": current_spread_bps,
        "widening_bps": widen,
        "stop_widen_bps": cfg.thresholds.stop_widen_bps,
    }
    if widen >= cfg.thresholds.stop_widen_bps:
        return [Alert(
            timestamp=_now(), issuer=issuer, kind="stop_loss_spread", severity="CRITICAL",
            message=(
                f"Spread widened {widen:.0f} bps since entry "
                f"(>= stop {cfg.thresholds.stop_widen_bps:.0f}). Exit trade."
            ),
            payload=payload,
        )]
    return []


def evaluate_time_stop(
    entry_date: date,
    cfg: TradeConfig,
    *,
    issuer: str,
    asof: date | None = None,
) -> list[Alert]:
    """CRITICAL when months-held >= max; WARN within one month of the stop."""
    asof = asof or date.today()
    days = (asof - entry_date).days
    months = days / 30.4375
    max_months = cfg.thresholds.max_hold_months
    payload = {
        "entry_date": entry_date.isoformat(),
        "asof": asof.isoformat(),
        "months_held": round(months, 2),
        "max_hold_months": max_months,
    }
    if months >= max_months:
        return [Alert(
            timestamp=_now(), issuer=issuer, kind="time_stop", severity="CRITICAL",
            message=f"Held {months:.1f} months (>= {max_months}). Exit trade.",
            payload=payload,
        )]
    if months >= max_months - 1:
        return [Alert(
            timestamp=_now(), issuer=issuer, kind="time_stop_warn", severity="WARN",
            message=f"Within 1 month of time stop ({months:.1f}/{max_months}).",
            payload=payload,
        )]
    return []


def evaluate_data_quality(marks: pd.DataFrame, *, issuer: str) -> list[Alert]:
    """WARN/INFO on missing marks needed for P&L and attribution."""
    alerts: list[Alert] = []
    if marks is None or marks.empty:
        alerts.append(Alert(
            timestamp=_now(), issuer=issuer, kind="data_quality", severity="WARN",
            message="Marks frame is empty.",
        ))
        return alerts
    if "price" in marks.columns and marks["price"].isna().any():
        missing = marks.index[marks["price"].isna()].tolist()
        alerts.append(Alert(
            timestamp=_now(), issuer=issuer, kind="data_quality", severity="WARN",
            message=f"Missing price for {len(missing)} securities.",
            payload={"securities": missing},
        ))
    if "zspread" in marks.columns and marks["zspread"].isna().any():
        missing = marks.index[marks["zspread"].isna()].tolist()
        alerts.append(Alert(
            timestamp=_now(), issuer=issuer, kind="data_quality", severity="INFO",
            message=f"Missing Z-spread for {len(missing)} securities (attribution disabled).",
            payload={"securities": missing},
        ))
    return alerts


# --------------------------------------------------------------------------- #
# Persistence + sinks
# --------------------------------------------------------------------------- #
def default_log_path(cfg: TradeConfig, data_dir: Path | None = None) -> Path:
    """``data/<issuer>/alerts.jsonl`` -- the default append target."""
    base = data_dir or (project_root() / "data")
    return base / cfg.issuer.short.lower() / "alerts.jsonl"


def write_log(alerts: list[Alert], path: Path) -> int:
    """Append alerts to ``path`` (JSONL). Returns rows written."""
    if not alerts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for a in alerts:
            fh.write(a.model_dump_json() + "\n")
    logger.info("Wrote %d alert(s) to %s", len(alerts), path)
    return len(alerts)


def read_log(path: Path) -> list[Alert]:
    """Load alerts back from a JSONL log."""
    if not path.exists():
        return []
    out: list[Alert] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(Alert.model_validate(json.loads(line)))
    return out


def email_sink(alerts: list[Alert], *, to: str, subject: str = "Fallen-Angels Alerts") -> None:
    """TODO wire to SMTP / SES. Signature is stable -- evaluators won't change."""
    raise NotImplementedError(
        "email_sink not wired. Implement SMTP/SES dispatch here; alerts.evaluate_* "
        "callers will not need to change."
    )


def slack_sink(alerts: list[Alert], *, webhook_url: str, channel: str | None = None) -> None:
    """TODO wire to a Slack incoming webhook (POST JSON)."""
    raise NotImplementedError(
        "slack_sink not wired. POST to webhook_url with a chat.postMessage payload."
    )


# --------------------------------------------------------------------------- #
# Top-level convenience for monitor.py / cron
# --------------------------------------------------------------------------- #
def evaluate_all(
    cfg: TradeConfig,
    *,
    issuer: str,
    signal_df: pd.DataFrame | None = None,
    entry_spread_bps: float | None = None,
    current_spread_bps: float | None = None,
    entry_date: date | None = None,
    marks: pd.DataFrame | None = None,
    log_path: Path | None = None,
    persist: bool = True,
) -> list[Alert]:
    """Run every evaluator that has enough inputs; optionally persist to JSONL.

    Inputs are independent: pass what you have, the rest is skipped. This is
    what ``monitor.py`` and a daily cron will both call.
    """
    alerts: list[Alert] = []
    if signal_df is not None:
        alerts += evaluate_signal(signal_df, cfg, issuer=issuer)
    if entry_spread_bps is not None and current_spread_bps is not None:
        alerts += evaluate_spread_stop(entry_spread_bps, current_spread_bps, cfg, issuer=issuer)
    if entry_date is not None:
        alerts += evaluate_time_stop(entry_date, cfg, issuer=issuer)
    if marks is not None:
        alerts += evaluate_data_quality(marks, issuer=issuer)
    if persist:
        write_log(alerts, log_path or default_log_path(cfg))
    return alerts
