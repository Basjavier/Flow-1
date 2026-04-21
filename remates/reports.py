"""
Institutional-grade console reports using Rich.
"""

from __future__ import annotations
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box
from rich.text import Text
from rich.rule import Rule

from .models import Asset, ScenarioParams, ScenarioResult, MonteCarloResult
from .engine import FinancialEngine, BASE, CONSERVATIVE, STRESS, BID_LEVEL_META
from .sensitivity import SensitivityAnalyzer
from .optimizer import CapitalAllocator

console = Console()

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _roi_color(roi: float) -> str:
    if roi >= 0.40:
        return "bright_green"
    elif roi >= 0.30:
        return "green"
    elif roi >= 0.20:
        return "yellow"
    elif roi >= 0.0:
        return "dark_orange"
    return "red"


def _fmt_uf(val: float, sign: bool = False) -> str:
    s = "+" if sign and val >= 0 else ""
    return f"{s}{val:,.1f} UF"


def _fmt_pct(val: float, sign: bool = False) -> str:
    s = "+" if sign and val >= 0 else ""
    return f"{s}{val*100:.1f}%"


# ---------------------------------------------------------------------------
# Portfolio summary
# ---------------------------------------------------------------------------

def print_portfolio_summary(assets: List[Asset], params: ScenarioParams = CONSERVATIVE):
    console.print()
    console.print(Rule("[bold white]ANÁLISIS TIER A — REMATES INMOBILIARIOS CHILE[/bold white]", style="dim white"))
    console.print()

    t = Table(
        title="[bold]Resumen Ejecutivo de Portafolio[/bold]",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        border_style="dim white",
        min_width=110,
    )

    t.add_column("#", style="dim", width=6)
    t.add_column("Activo", min_width=22)
    t.add_column("Ciudad", width=12)
    t.add_column("Config", width=9)
    t.add_column("m²", justify="right", width=5)
    t.add_column("Base UF", justify="right", width=8)
    t.add_column("Fair Value", justify="right", width=10)
    t.add_column("Desc.%", justify="right", width=7)
    t.add_column("ROI Base", justify="right", width=9)
    t.add_column("ROI Cons.", justify="right", width=10)
    t.add_column("Max Bid 30%", justify="right", width=11)
    t.add_column("Comps", justify="right", width=7)
    t.add_column("Días", justify="right", width=6)
    t.add_column("Alerta", width=10)

    for asset in assets:
        base_r = ScenarioResult(asset=asset, params=BASE, entry_price=asset.catalog_base_uf)
        cons_r = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        max_bid = FinancialEngine.max_bid(asset, params, 0.30)

        urgency = "[bold red]URGENTE[/bold red]" if asset.is_urgent else "[green]OK[/green]"
        bath_flag = "[yellow]*1b[/yellow]" if asset.bathroom_adjustment < 1 else ""
        comp_color = "green" if asset.n_comparables >= 8 else "yellow" if asset.n_comparables >= 5 else "red"

        t.add_row(
            asset.id,
            asset.name,
            asset.city,
            f"{asset.bedrooms}d/{asset.bathrooms}b/{asset.parking}e{bath_flag}",
            str(int(asset.m2)),
            _fmt_uf(asset.catalog_base_uf),
            _fmt_uf(asset.fair_value_adjusted),
            _fmt_pct(asset.discount_vs_catalog),
            Text(_fmt_pct(base_r.roi), style=_roi_color(base_r.roi)),
            Text(_fmt_pct(cons_r.roi), style=_roi_color(cons_r.roi)),
            f"[bold]{_fmt_uf(max_bid)}[/bold]",
            Text(str(asset.n_comparables), style=comp_color),
            str(asset.days_to_auction),
            urgency,
        )

    console.print(t)
    console.print()


# ---------------------------------------------------------------------------
# Full asset report
# ---------------------------------------------------------------------------

def print_asset_report(
    asset: Asset,
    run_mc: bool = True,
    n_mc: int = 10_000,
):
    console.print()
    console.print(Rule(f"[bold cyan]ANÁLISIS DETALLADO — {asset.name.upper()}[/bold cyan]", style="cyan"))

    # --- Header panel ---
    urgency_str = f"[bold red]⚠ URGENTE — {asset.days_to_auction} días[/bold red]" if asset.is_urgent else f"[green]{asset.days_to_auction} días[/green]"
    bath_str = f"\n[yellow]⚠ Ajuste 1-baño aplicado: -{(1-asset.bathroom_adjustment)*100:.0f}% al fair value[/yellow]" if asset.bathroom_adjustment < 1 else ""
    comp_str = f"[{'green' if asset.n_comparables >= 8 else 'yellow' if asset.n_comparables >= 5 else 'red'}]Confianza comps: {asset.comparable_confidence} ({asset.n_comparables} comps)[/]"

    header = (
        f"[bold white]{asset.address} | {asset.city}[/bold white]\n"
        f"{asset.bedrooms}d / {asset.bathrooms}b / {asset.parking}e  |  {asset.m2}m²  |  Score: {asset.score}\n"
        f"Remate: [bold]{asset.auction_date.strftime('%d %b %Y')}[/bold]  |  {urgency_str}  |  {comp_str}"
        f"{bath_str}"
    )
    if asset.notes:
        header += f"\n[dim]{asset.notes}[/dim]"

    console.print(Panel(header, title="[bold]Ficha del Activo[/bold]", border_style="cyan"))
    console.print()

    # --- Escenarios ---
    _print_scenarios(asset)

    # --- Tabla de bid ---
    _print_bid_table(asset, CONSERVATIVE)

    # --- Sensibilidad tornado ---
    _print_tornado(asset, CONSERVATIVE)

    # --- Monte Carlo ---
    if run_mc:
        bid_30 = FinancialEngine.max_bid(asset, CONSERVATIVE, 0.30)
        mc = SensitivityAnalyzer.monte_carlo(asset, CONSERVATIVE, bid_30, n_simulations=n_mc)
        _print_monte_carlo(mc, bid_30)

    # --- Recomendación ---
    _print_recommendation(asset)

    console.print()


def _print_scenarios(asset: Asset):
    t = Table(
        title="[bold]Análisis de Escenarios[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold magenta",
        border_style="dim white",
    )
    t.add_column("Escenario", width=14)
    t.add_column("Holding", justify="right", width=9)
    t.add_column("Refacción", justify="right", width=10)
    t.add_column("% Mercado", justify="right", width=10)
    t.add_column("Aprec.", justify="right", width=9)
    t.add_column("Costos Total", justify="right", width=12)
    t.add_column("Venta Neta", justify="right", width=11)
    t.add_column("Utilidad", justify="right", width=10)
    t.add_column("ROI", justify="right", width=9)
    t.add_column("EM", justify="right", width=6)

    for params in [BASE, CONSERVATIVE, STRESS]:
        r = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        roi_col = _roi_color(r.roi)
        t.add_row(
            f"[bold]{params.name}[/bold]",
            f"{params.holding_months}m",
            _fmt_pct(params.renovation_pct),
            _fmt_pct(params.sale_pct_market),
            _fmt_pct(params.appreciation_annual),
            _fmt_uf(r.total_costs),
            _fmt_uf(r.net_sale),
            Text(_fmt_uf(r.profit, sign=True), style=roi_col),
            Text(_fmt_pct(r.roi), style=f"bold {roi_col}"),
            f"{r.equity_multiple:.2f}x",
        )

    console.print(t)
    console.print()


def _print_bid_table(asset: Asset, params: ScenarioParams):
    bid_rows = FinancialEngine.build_bid_table(asset, params)

    t = Table(
        title=f"[bold]Precio Máximo a Licitar — Escenario {params.name}[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        border_style="dim white",
    )
    t.add_column("ROI Objetivo", justify="right", width=12)
    t.add_column("Max Bid (UF)", justify="right", width=12)
    t.add_column("UF/m²", justify="right", width=8)
    t.add_column("Buffer vs Base", justify="right", width=14)
    t.add_column("Buffer %", justify="right", width=9)
    t.add_column("Nivel", width=20)
    t.add_column("Descripción", min_width=40)

    for row in bid_rows:
        roi = row["roi_target"]
        is_target = roi == 0.30
        is_open = roi == 0.35
        style = "bold yellow" if is_target else ("bold orange3" if is_open else "")

        t.add_row(
            Text(_fmt_pct(roi), style=style),
            Text(_fmt_uf(row["max_bid_uf"]), style=style),
            Text(f"{row['uf_per_m2']:.1f}", style=style),
            Text(_fmt_uf(row["buffer_uf"], sign=True), style=style),
            Text(_fmt_pct(row["buffer_pct"], sign=True), style=style),
            Text(row["level_name"], style=style),
            row["description"],
        )

    console.print(t)
    console.print()


def _print_tornado(asset: Asset, params: ScenarioParams):
    bid_30 = FinancialEngine.max_bid(asset, params, 0.30)
    rows = SensitivityAnalyzer.tornado(asset, params, bid_30)

    t = Table(
        title=f"[bold]Análisis de Sensibilidad (Tornado) — Entrada: {bid_30:.0f} UF[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white",
        border_style="dim white",
    )
    t.add_column("Variable", min_width=22)
    t.add_column("ROI base", justify="right", width=10)
    t.add_column("Escenario (+)", justify="right", width=13)
    t.add_column("Escenario (−)", justify="right", width=13)
    t.add_column("Swing total", justify="right", width=12)
    t.add_column("Ranking", width=10)

    for i, row in enumerate(rows):
        rank = ["1° CLAVE", "2°", "3°", "4°", "5°"][min(i, 4)]
        rank_style = "bold red" if i == 0 else "yellow" if i == 1 else "dim"
        t.add_row(
            row["label"],
            _fmt_pct(row["base_roi"]),
            Text(_fmt_pct(row["roi_high"], sign=True), style="green"),
            Text(_fmt_pct(row["roi_low"], sign=True), style="red"),
            Text(_fmt_pct(row["total_swing"]), style="bold"),
            Text(rank, style=rank_style),
        )

    console.print(t)
    console.print()


def _print_monte_carlo(mc: MonteCarloResult, entry_price: float):
    t = Table(
        title=f"[bold]Monte Carlo — {mc.n_simulations:,} simulaciones | Entrada: {entry_price:.0f} UF[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white",
        border_style="dim white",
    )
    t.add_column("Métrica", min_width=25)
    t.add_column("Valor", justify="right", width=12)

    rows_data = [
        ("ROI Medio (E[ROI])",        _fmt_pct(mc.mean_roi),       _roi_color(mc.mean_roi)),
        ("ROI Mediana",               _fmt_pct(mc.median_roi),      _roi_color(mc.median_roi)),
        ("Desv. estándar (σ)",        _fmt_pct(mc.std_roi),         "white"),
        ("P5  (tail izquierda)",      _fmt_pct(mc.p5_roi),          _roi_color(mc.p5_roi)),
        ("P25",                       _fmt_pct(mc.p25_roi),         _roi_color(mc.p25_roi)),
        ("P75",                       _fmt_pct(mc.p75_roi),         _roi_color(mc.p75_roi)),
        ("P95 (tail derecha)",        _fmt_pct(mc.p95_roi),         _roi_color(mc.p95_roi)),
        ("P(ROI > 0%)  — ganancia",   _fmt_pct(mc.prob_positive),   "green" if mc.prob_positive > 0.90 else "yellow"),
        ("P(ROI > 20%) — OK",         _fmt_pct(mc.prob_above_20pct),"green" if mc.prob_above_20pct > 0.75 else "yellow"),
        ("P(ROI > 30%) — objetivo",   _fmt_pct(mc.prob_above_30pct),"green" if mc.prob_above_30pct > 0.60 else "yellow"),
        ("P(ROI < 0%)  — pérdida",    _fmt_pct(mc.prob_loss),       "green" if mc.prob_loss < 0.05 else "red"),
    ]

    for label, val, color in rows_data:
        t.add_row(label, Text(val, style=color))

    console.print(t)
    console.print()


def _print_recommendation(asset: Asset):
    """Print BUY / NEGOTIATE / PASS recommendation with rationale."""
    base_r = ScenarioResult(asset=asset, params=BASE, entry_price=asset.catalog_base_uf)
    cons_r = ScenarioResult(asset=asset, params=CONSERVATIVE, entry_price=asset.catalog_base_uf)
    stress_r = ScenarioResult(asset=asset, params=STRESS, entry_price=asset.catalog_base_uf)

    bid_35 = FinancialEngine.max_bid(asset, CONSERVATIVE, 0.35)
    bid_30 = FinancialEngine.max_bid(asset, CONSERVATIVE, 0.30)

    has_bath_risk = asset.bathroom_adjustment < 1
    has_comp_risk = asset.n_comparables < 5
    is_urgent = asset.is_urgent

    # Decision logic
    if cons_r.roi >= 0.60 and not has_comp_risk:
        verdict = "LICITAR"
        color = "bold green"
    elif cons_r.roi >= 0.40 or (cons_r.roi >= 0.30 and not has_bath_risk and not has_comp_risk):
        verdict = "LICITAR CON CAUTELA"
        color = "bold yellow"
    elif cons_r.roi >= 0.20:
        verdict = "NEGOCIAR"
        color = "bold orange3"
    else:
        verdict = "PASAR"
        color = "bold red"

    urgency_note = "\n[bold red]⚠ DECISIÓN REQUERIDA ESTA SEMANA — remate inminente[/bold red]" if is_urgent else ""

    body = (
        f"Veredicto: [{color}]{verdict}[/{color}]"
        f"{urgency_note}\n\n"
        f"[dim]ROI base:[/dim]          {_fmt_pct(base_r.roi)}\n"
        f"[dim]ROI conservador:[/dim]   {_fmt_pct(cons_r.roi)}\n"
        f"[dim]ROI stress:[/dim]        {_fmt_pct(stress_r.roi)}\n\n"
        f"[bold]Estrategia de bid (escenario conservador):[/bold]\n"
        f"  Bid apertura (35% ROI):    [orange3]{_fmt_uf(bid_35)}[/orange3]\n"
        f"  Precio objetivo (30% ROI): [bold yellow]{_fmt_uf(bid_30)}[/bold yellow]  ← máximo absoluto recomendado\n"
        f"  Base catálogo:             [dim]{_fmt_uf(asset.catalog_base_uf)}[/dim] ({_fmt_pct(asset.discount_vs_catalog)} descuento vs fair)\n"
    )

    if has_bath_risk:
        body += f"\n[yellow]⚠ Ajuste 1-baño: fair value reducido 12%. Max bid real ~{bid_30:.0f} UF (no usar cifras del CSV sin ajuste).[/yellow]"
    if has_comp_risk:
        body += f"\n[red]⚠ Solo {asset.n_comparables} comparable(s): alta incertidumbre en UF/m² de mercado. Verificar manualmente antes de licitar.[/red]"
    if is_urgent:
        body += f"\n[red]⚠ Validar: comparables, sector, contribuciones y gastos comunes atrasados ANTES del {asset.auction_date.strftime('%d/%m/%Y')}.[/red]"

    console.print(Panel(body, title="[bold]Recomendación Final[/bold]", border_style="yellow"))
    console.print()


# ---------------------------------------------------------------------------
# Capital allocation report
# ---------------------------------------------------------------------------

def print_capital_allocation(
    assets: List[Asset],
    params: ScenarioParams,
    budget_uf: float,
    target_roi: float = 0.30,
):
    console.print(Rule(f"[bold]OPTIMIZACIÓN DE CAPITAL — {budget_uf:,.0f} UF disponibles[/bold]", style="yellow"))

    result = CapitalAllocator.allocate(assets, params, budget_uf, target_roi)
    conflicts = CapitalAllocator.same_day_conflict(assets, params, budget_uf, target_roi)

    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim white")
    t.add_column("Activo", min_width=22)
    t.add_column("Ciudad", width=12)
    t.add_column("Bid (30%)", justify="right", width=11)
    t.add_column("Utilidad est.", justify="right", width=13)
    t.add_column("Seleccionado", width=13)

    asset_map = {a.id: a for a in assets}
    for a in assets:
        selected = a.id in result["selected_ids"]
        bid = FinancialEngine.max_bid(a, params, target_roi)
        r = ScenarioResult(asset=a, params=params, entry_price=bid)
        t.add_row(
            a.name,
            a.city,
            _fmt_uf(bid),
            _fmt_uf(r.profit, sign=True),
            Text("SI ✓", style="bold green") if selected else Text("NO", style="dim"),
        )

    console.print(t)

    console.print(
        f"\n  Capital desplegado:  [bold]{_fmt_uf(result['total_deployed_uf'])}[/bold] / {_fmt_uf(budget_uf)}\n"
        f"  Capital remanente:   [dim]{_fmt_uf(result['remaining_capital_uf'])}[/dim]\n"
        f"  Utilidad total est.: [bold green]{_fmt_uf(result['total_profit_uf'], sign=True)}[/bold green]\n"
        f"  ROI portafolio:      [bold]{_fmt_pct(result['portfolio_roi'])}[/bold]"
    )

    if conflicts:
        console.print()
        console.print("[bold yellow]⚠ Conflictos de capital (mismo día de remate):[/bold yellow]")
        for dt, c in conflicts.items():
            ids = [a.id for a in c["assets"]]
            console.print(
                f"  {dt}: Activos {ids}\n"
                f"    Capital para ganar ambos: {_fmt_uf(c['capital_if_win_all'])}\n"
                f"    Capital para licitar en ambos (solo necesitas ganar 1): {_fmt_uf(c['capital_needed_to_bid_all'])}\n"
                f"    ¿Puede licitar en ambos con {_fmt_uf(budget_uf)}? "
                + ("[green]SÍ[/green]" if c["can_bid_all_with_budget"] else "[red]NO[/red]")
            )

    console.print()
