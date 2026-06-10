# fallen_angels

Operational research stack for **fallen-angel long/short credit trades**. The
first trade is Centene (CNC) after its April 2026 downgrade to BB+; the codebase
is parameterized by per-issuer YAML so it extends to future fallen angels
(Paramount, Ford, etc.) without code changes.

> Exploratory/personal research, not a production fund system.

## Requirements
- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for environment management
- A logged-in **Bloomberg Terminal** with **BLPAPI** installed (the C++ SDK ships
  with a standard Terminal install). `xbbg` is the Python wrapper used here.

`blpapi` is published on Bloomberg's own package index, not PyPI. `pyproject.toml`
already points `uv` at that index, so `uv sync` resolves it automatically. The
BLPAPI C++ SDK must already be present on the host.

## Setup
```bash
cd fallen_angels
uv sync          # creates .venv and installs deps (incl. blpapi from Bloomberg's index)
```

## Verify
With the Terminal running and logged in:
```bash
# Phase 1 — data layer (universe + index sample; --refresh / --days N / --log-level DEBUG)
uv run python -m fallen_angels.data_pull --issuer CNC

# Phase 2 — universe ranking + spread differential (notebooks/01_explore_universe.ipynb)
uv run jupyter lab

# Phase 3 — fallen-angel factor backtest (also notebooks/02_validate_backtest.ipynb)
uv run python -m fallen_angels.backtest --issuer CNC

# Offline unit tests (no Bloomberg needed)
uv run pytest
```

## Data layer behavior
`src/fallen_angels/data_pull.py` exposes three pulls, each cached to
`data/<issuer>/<label>_<asof>.parquet` and re-served on the next same-day run:

| Function | Purpose |
| --- | --- |
| `get_bond_universe(issuer)` | Static reference data for every bond of an issuer |
| `get_bond_timeseries(cusips, start, end, fields)` | Historical fields per bond |
| `get_index_timeseries(tickers, start, end)` | Historical ETF / spread-index levels |

Time series are returned in **tidy long** form (`date, security, field, value`)
so they round-trip through parquet and pivot to wide in one call. Every Bloomberg
call is wrapped to raise an actionable error if the Terminal/field is unavailable.

Some Bloomberg field names are marked `TODO` in `config/cnc.yaml` and
`data_pull.py` with alternatives to try — confirm them against your Terminal.

## Configuration
Each trade is one YAML file under `config/`, validated against the pydantic
schema in `src/fallen_angels/config.py`. To add an issuer, copy `cnc.yaml` to
`config/<code>.yaml` and edit. Nothing issuer-specific is hardcoded.

## Layout
```
fallen_angels/
├── config/
│   ├── <issuer>.yaml           # per-trade params (validated by config.py)
│   ├── fallen_angel_events.yaml# curated IG->HY downgrades for the backtest
│   └── positions/<book>.yaml   # live position state for portfolio.py [Phase 4]
├── src/fallen_angels/
│   ├── config.py               # pydantic schema + loader
│   ├── data_pull.py            # Bloomberg pulls + parquet cache   [Phase 1]
│   ├── universe.py             # bond screen + composite scoring   [Phase 2]
│   ├── comparables.py          # BB+ peer basket + weights         [Phase 2]
│   ├── signals.py              # spread differential + z-score     [Phase 2]
│   ├── backtest.py             # fallen-angel factor study         [Phase 3]
│   ├── portfolio.py            # positions + daily P&L decomposition [Phase 4]
│   ├── risk.py                 # scenario engine + VaR/CVaR          [Phase 4]
│   └── alerts.py               # threshold engine + JSONL log        [Phase 4]
├── notebooks/                  # 01 universe/signal, 02 backtest validation
├── data/                       # parquet cache (gitignored)
├── reports/                    # memo, weekly template, risk log
└── tests/                      # offline pytest suite (no Bloomberg)
```

## Roadmap
- **Phase 1 (done):** scaffolding + data layer
- **Phase 2 (done):** `universe.py`, `comparables.py`, `signals.py`
- **Phase 3 (done):** `backtest.py` fallen-angel factor study
- **Phase 4 (done):** `portfolio.py`, `risk.py`, `alerts.py`
- **Phase 5:** `monitor.py` Streamlit dashboard
