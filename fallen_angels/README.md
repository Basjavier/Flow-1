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

## Verify Phase 1 (data layer)
With the Terminal running and logged in:
```bash
uv run python -m fallen_angels.data_pull --issuer CNC
```
This loads `config/cnc.yaml`, resolves and prints the CNC bond universe, and
pulls a trailing sample of the benchmark index series. Useful flags:
- `--refresh` — bypass the on-disk cache and re-pull from Bloomberg
- `--days N` — trailing days of index history to sample (default 30)
- `--log-level DEBUG` — verbose logging

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
├── config/<issuer>.yaml        # per-trade params (validated by config.py)
├── src/fallen_angels/
│   ├── config.py               # pydantic schema + loader
│   └── data_pull.py            # Bloomberg pulls + parquet cache  [Phase 1]
├── data/                       # parquet cache (gitignored)
├── reports/memo.md             # investment thesis
└── tests/
```

## Roadmap
- **Phase 1 (done):** scaffolding + data layer
- **Phase 2:** `universe.py`, `comparables.py`, `signals.py`
- **Phase 3:** `backtest.py` fallen-angel factor study
- **Phase 4:** `portfolio.py`, `risk.py`, `alerts.py`
- **Phase 5:** `monitor.py` Streamlit dashboard + tests
