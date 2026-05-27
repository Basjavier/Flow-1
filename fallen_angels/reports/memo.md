# Investment Memo — Centene (CNC) Fallen-Angel Long/Short

**Status:** Draft (Phase 1) · **As of:** 2026-05-27 · **Horizon:** 12–18 months

## Thesis
S&P's April 2026 downgrade of Centene to **BB+** forces index- and mandate-driven
selling by IG-only holders. This mechanical, price-insensitive selling has opened
an estimated **80–150 bps** Z-spread dislocation in CNC senior unsecured bonds
versus comparable **BB+ healthcare** credits. The dislocation is a function of
flows, not of a deterioration in fundamentals beyond the one-notch crossover.
We expect a **slow re-rating** as the forced-selling overhang clears and crossover/HY
buyers reprice the credit toward its peer group.

## Structure
- **Long:** basket of CNC senior unsecured bonds across the curve, 2027–2032
  maturities, amount outstanding > $500M each (liquidity + curve coverage).
- **Short:** **HYG** ETF at **50–70%** of long notional as a systemic/beta hedge,
  isolating the idiosyncratic convergence from broad HY spread direction.

## Edge
Forced selling from IG mandates is price-insensitive and time-bounded. The
resulting spread gap versus BB+ healthcare peers is the carry-plus-convergence
opportunity. Edge decays as the technical clears, hence the 12–18 month window.

## Entry
Enter when the **CNC weighted-average Z-spread minus the peer weighted-average
Z-spread** exceeds **+1.5σ** over a trailing 24-month (504-day) window.

## Exit
1. **Convergence:** differential narrows below **+0.5σ** (take profit).
2. **Stop:** spread widens **>100 bps** from entry (thesis broken / fundamentals).
3. **Rating signal:** S&P outlook revision toward upgrade (re-rating underway).
4. **Time:** **18 months** elapsed.

## Peer Basket (proposed — confirm in Phase 2)
HCA, Humana (HUM), Molina (MOH), Bausch Health (BHC). Managed-care names (HUM,
MOH) anchor the business-model comparison; HCA adds a deep, liquid crossover
curve; BHC diversifies beyond managed care. Final weights set in `comparables.py`.

## Key Risks
- **Fundamental deterioration:** Medicaid redetermination / rate pressure widens
  CNC on credit, not technicals — addressed by the 100 bps stop.
- **Hedge basis:** HYG beta to CNC is imperfect; hedge ratio is a tunable band.
- **Liquidity:** off-the-run bonds may be hard to source/exit — universe screen
  enforces size and seasoning.
- **Catalyst timing:** re-rating may lag the 18-month window — time stop caps it.

## Open Items
- Confirm exact S&P downgrade date and any outlook qualifier (RATC<GO>).
- Confirm Bloomberg chain field for the CNC bond universe (see `data_pull.py`).
- Validate the fallen-angel factor historically before sizing (Phase 3 backtest).
