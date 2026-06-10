# Risk Log — Fallen-Angel Program

Running log of risk events, near-misses, and model caveats. Newest first.
One entry per dated item; never delete, only append corrections.

| Date | Trade | Type | Description | Action / Resolution |
|---|---|---|---|---|
| 2026-06-10 | CNC | model | Backtest event dates in `fallen_angel_events.yaml` are curated, not Terminal-confirmed (flagged TODO). Stratified stats indicative until verified via RATC<GO>. | Confirm dates before sizing off backtest output |
| 2026-06-10 | CNC | model | Bond TR in backtest is a price+accrual proxy (no reinvestment, no exact day count). Fine for factor read, not P&L. | portfolio.py (Phase 4) handles real P&L |
| 2026-06-10 | CNC | data | Bloomberg chain field (`CURVE_TICKERS`) and index tickers (`H0A0`, `H4A1`, `IBOXHYSE`) unconfirmed on Terminal. | Verify on first live pull; alternatives noted in code |
