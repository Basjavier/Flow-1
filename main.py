"""
Remates CLI — Análisis institucional de activos en remate judicial.

Usage examples:
  python main.py                                    # full report, all assets
  python main.py --asset 77833                      # single asset
  python main.py --charts                           # generate PNG charts
  python main.py --monte-carlo                      # include Monte Carlo
  python main.py --capital 2500                     # capital allocation (UF)
  python main.py --asset 77948 --charts --mc        # specific asset + charts + MC
"""

from __future__ import annotations
import argparse
import os
import sys

from rich.console import Console

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Motor de análisis institucional — Remates Inmobiliarios Chile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--asset", "-a", metavar="ID", help="Analizar solo este activo (ej: 77833)")
    p.add_argument("--charts", "-c", action="store_true", help="Generar gráficos PNG en ./output/")
    p.add_argument("--monte-carlo", "--mc", action="store_true", dest="mc", help="Incluir simulación Monte Carlo")
    p.add_argument("--n-mc", type=int, default=10_000, metavar="N", help="Iteraciones Monte Carlo (default: 10000)")
    p.add_argument("--capital", "-k", type=float, metavar="UF", help="Capital disponible en UF para optimización de portafolio")
    p.add_argument("--target-roi", type=float, default=0.30, metavar="ROI", help="ROI objetivo para bid table (default: 0.30)")
    p.add_argument("--output", "-o", default="output", metavar="DIR", help="Directorio de salida para gráficos (default: output/)")
    p.add_argument("--summary-only", "-s", action="store_true", help="Solo resumen ejecutivo del portafolio")
    return p.parse_args()


def main():
    args = parse_args()

    # Imports here so errors are reported cleanly
    from data.tier_a import TIER_A, TIER_A_BY_ID
    from remates.engine import CONSERVATIVE
    from remates.reports import (
        print_portfolio_summary,
        print_asset_report,
        print_capital_allocation,
    )

    os.makedirs(args.output, exist_ok=True)

    # Select assets
    if args.asset:
        if args.asset not in TIER_A_BY_ID:
            console.print(f"[red]Activo '{args.asset}' no encontrado.[/red]")
            console.print(f"IDs disponibles: {list(TIER_A_BY_ID.keys())}")
            sys.exit(1)
        assets = [TIER_A_BY_ID[args.asset]]
    else:
        assets = TIER_A

    # Portfolio summary (always shown)
    print_portfolio_summary(assets, CONSERVATIVE)

    if args.summary_only:
        return

    # Per-asset detailed reports
    for asset in assets:
        print_asset_report(asset, run_mc=args.mc, n_mc=args.n_mc)

        if args.charts:
            from remates.charts import plot_asset_report, plot_tornado
            from remates.engine import FinancialEngine
            from remates.sensitivity import SensitivityAnalyzer

            bid_30 = FinancialEngine.max_bid(asset, CONSERVATIVE, 0.30)

            mc = None
            if args.mc:
                mc = SensitivityAnalyzer.monte_carlo(asset, CONSERVATIVE, bid_30, n_simulations=args.n_mc)

            safe_name = asset.city.replace(" ", "_")
            plot_asset_report(
                asset, CONSERVATIVE, bid_30, mc_result=mc,
                output_path=os.path.join(args.output, f"reporte_{asset.id}_{safe_name}.png"),
            )
            plot_tornado(
                asset, CONSERVATIVE, bid_30,
                output_path=os.path.join(args.output, f"tornado_{asset.id}_{safe_name}.png"),
            )

    # Portfolio charts (multi-asset only)
    if args.charts and len(assets) > 1:
        from remates.charts import plot_portfolio
        plot_portfolio(
            assets, CONSERVATIVE,
            target_roi=args.target_roi,
            output_path=os.path.join(args.output, "portfolio_comparison.png"),
        )

    # Capital allocation
    if args.capital:
        print_capital_allocation(TIER_A, CONSERVATIVE, args.capital, args.target_roi)


if __name__ == "__main__":
    main()
