"""
Institutional console reports — recalibrated cost model.
"""

from __future__ import annotations
from typing import List

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.text import Text
from rich.rule import Rule

from .models import Asset, ScenarioParams, ScenarioResult, MonteCarloResult
from .engine import (
    FinancialEngine, OPTIMISTA, REALISTA, CONSERVADOR, STRESS,
    BID_LEVEL_META,
)
from .sensitivity import SensitivityAnalyzer
from .optimizer import CapitalAllocator

console = Console()


def _roi_color(roi: float) -> str:
    if roi >= 0.25:  return "bright_green"
    if roi >= 0.15:  return "green"
    if roi >= 0.05:  return "yellow"
    if roi >= 0.0:   return "dark_orange"
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

def print_portfolio_summary(assets: List[Asset], params: ScenarioParams = REALISTA):
    console.print()
    console.print(Rule("[bold white]ANÁLISIS TIER A — REMATES INMOBILIARIOS CHILE[/bold white]", style="dim white"))
    console.print(
        f"[dim]  Escenario base: {params.name} | "
        f"Reno: {params.renovation_uf_per_m2} UF/m² | "
        f"Deudas ocultas: {params.hidden_debts_uf} UF | "
        f"Holding: {params.holding_months}m | "
        f"Venta: {params.sale_pct_market*100:.0f}% mercado[/dim]"
    )
    console.print()

    t = Table(
        title="[bold]Resumen Ejecutivo de Portafolio[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        border_style="dim white",
        min_width=120,
    )

    t.add_column("#",            style="dim",    width=7)
    t.add_column("Activo",       min_width=22)
    t.add_column("Ciudad",       width=12)
    t.add_column("Config",       width=11)
    t.add_column("m²",           justify="right", width=5)
    t.add_column("Base UF",      justify="right", width=9)
    t.add_column("Fair Value",   justify="right", width=11)
    t.add_column("Reno UF",      justify="right", width=9)
    t.add_column("ROI optim.",   justify="right", width=10)
    t.add_column("ROI real.",    justify="right", width=10)
    t.add_column("ROI stress",   justify="right", width=10)
    t.add_column("Max Bid 20%",  justify="right", width=11)
    t.add_column("Comps",        justify="right", width=6)
    t.add_column("Días",         justify="right", width=6)
    t.add_column("Alerta",       width=10)

    for asset in assets:
        opt_r    = ScenarioResult(asset=asset, params=OPTIMISTA,   entry_price=asset.catalog_base_uf)
        real_r   = ScenarioResult(asset=asset, params=REALISTA,    entry_price=asset.catalog_base_uf)
        stress_r = ScenarioResult(asset=asset, params=STRESS,      entry_price=asset.catalog_base_uf)
        max_bid  = FinancialEngine.max_bid(asset, REALISTA, 0.20)

        urgency    = "[bold red]URGENTE[/bold red]" if asset.is_urgent else "[green]OK[/green]"
        bath_flag  = "[yellow]*1b[/yellow]" if asset.bathroom_adjustment < 1 else ""
        comp_color = "green" if asset.n_comparables >= 8 else "yellow" if asset.n_comparables >= 5 else "red"
        reno_uf    = REALISTA.renovation_uf_per_m2 * asset.m2

        t.add_row(
            asset.id,
            asset.name,
            asset.city,
            f"{asset.bedrooms}d/{asset.bathrooms}b/{asset.parking}e{bath_flag}",
            str(int(asset.m2)),
            _fmt_uf(asset.catalog_base_uf),
            _fmt_uf(asset.fair_value_adjusted),
            _fmt_uf(reno_uf),
            Text(_fmt_pct(opt_r.roi),    style=_roi_color(opt_r.roi)),
            Text(_fmt_pct(real_r.roi),   style=_roi_color(real_r.roi)),
            Text(_fmt_pct(stress_r.roi), style=_roi_color(stress_r.roi)),
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

def print_asset_report(asset: Asset, run_mc: bool = True, n_mc: int = 10_000):
    console.print()
    console.print(Rule(f"[bold cyan]ANÁLISIS DETALLADO — {asset.name.upper()}[/bold cyan]", style="cyan"))

    urgency_str = (
        f"[bold red]⚠ URGENTE — {asset.days_to_auction} días[/bold red]"
        if asset.is_urgent
        else f"[green]{asset.days_to_auction} días[/green]"
    )
    bath_str  = (
        f"\n[yellow]⚠ Ajuste 1-baño: fair value reducido 12%[/yellow]"
        if asset.bathroom_adjustment < 1 else ""
    )
    comp_str  = (
        f"[{'green' if asset.n_comparables >= 8 else 'yellow' if asset.n_comparables >= 5 else 'red'}]"
        f"Confianza comps: {asset.comparable_confidence} ({asset.n_comparables} comps)[/]"
    )
    reno_real = REALISTA.renovation_uf_per_m2 * asset.m2

    header = (
        f"[bold white]{asset.address} | {asset.city}[/bold white]\n"
        f"{asset.bedrooms}d / {asset.bathrooms}b / {asset.parking}e  |  {asset.m2}m²  |  Score: {asset.score}\n"
        f"Remate: [bold]{asset.auction_date.strftime('%d %b %Y')}[/bold]  |  {urgency_str}  |  {comp_str}\n"
        f"Renovación mínima estimada: [bold]{_fmt_uf(reno_real)}[/bold] ({REALISTA.renovation_uf_per_m2} UF/m²)"
        f"{bath_str}"
    )
    if asset.notes:
        header += f"\n[dim]{asset.notes}[/dim]"

    console.print(Panel(header, title="[bold]Ficha del Activo[/bold]", border_style="cyan"))
    console.print()

    _print_cost_breakdown(asset)
    _print_scenarios(asset)
    _print_bid_table(asset, REALISTA)
    _print_tornado(asset, REALISTA)

    if run_mc:
        bid_20 = FinancialEngine.max_bid(asset, REALISTA, 0.20)
        mc = SensitivityAnalyzer.monte_carlo(asset, REALISTA, bid_20, n_simulations=n_mc)
        _print_monte_carlo(mc, bid_20)

    _print_recommendation(asset)
    console.print()


def _print_cost_breakdown(asset: Asset):
    """Show real cost structure for each scenario at catalog base price."""
    t = Table(
        title=f"[bold]Desglose Real de Costos — Entrada a precio base ({asset.catalog_base_uf} UF)[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold magenta",
        border_style="dim white",
    )
    t.add_column("Escenario",       width=13)
    t.add_column("Precio entrada",  justify="right", width=14)
    t.add_column("Tx entrada",      justify="right", width=11)
    t.add_column("Holding",         justify="right", width=9)
    t.add_column("Renovación",      justify="right", width=11)
    t.add_column("Deudas ocultas",  justify="right", width=14)
    t.add_column("TOTAL costos",    justify="right", width=12)
    t.add_column("Venta neta",      justify="right", width=11)
    t.add_column("Utilidad",        justify="right", width=10)
    t.add_column("ROI",             justify="right", width=8)

    for params in [OPTIMISTA, REALISTA, CONSERVADOR, STRESS]:
        ep = asset.catalog_base_uf
        r  = ScenarioResult(asset=asset, params=params, entry_price=ep)
        bd = FinancialEngine.cost_breakdown(asset, params, ep)
        rc = _roi_color(r.roi)

        t.add_row(
            f"[bold]{params.name}[/bold]",
            _fmt_uf(ep),
            _fmt_uf(bd["tx_costs_entry"]),
            _fmt_uf(bd["holding_costs"]),
            f"{_fmt_uf(bd['renovation_uf'])} ({params.renovation_uf_per_m2} UF/m²)",
            _fmt_uf(bd["hidden_debts"]),
            f"[bold]{_fmt_uf(r.total_costs)}[/bold]",
            _fmt_uf(r.net_sale),
            Text(_fmt_uf(r.profit, sign=True), style=rc),
            Text(_fmt_pct(r.roi), style=f"bold {rc}"),
        )

    console.print(t)
    console.print()


def _print_scenarios(asset: Asset):
    t = Table(
        title="[bold]Análisis de Escenarios — Parámetros y Resultados[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white",
        border_style="dim white",
    )
    t.add_column("Escenario",     width=13)
    t.add_column("Holding",       justify="right", width=8)
    t.add_column("Reno UF/m²",   justify="right", width=11)
    t.add_column("Deudas",        justify="right", width=9)
    t.add_column("% Mercado",     justify="right", width=10)
    t.add_column("Aprec.",        justify="right", width=8)
    t.add_column("Costos",        justify="right", width=10)
    t.add_column("Venta neta",    justify="right", width=11)
    t.add_column("Utilidad",      justify="right", width=10)
    t.add_column("ROI",           justify="right", width=8)
    t.add_column("EM",            justify="right", width=6)

    for params in [OPTIMISTA, REALISTA, CONSERVADOR, STRESS]:
        r  = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        rc = _roi_color(r.roi)
        t.add_row(
            f"[bold]{params.name}[/bold]",
            f"{params.holding_months}m",
            f"{params.renovation_uf_per_m2}",
            f"{params.hidden_debts_uf:.0f} UF",
            _fmt_pct(params.sale_pct_market),
            _fmt_pct(params.appreciation_annual),
            _fmt_uf(r.total_costs),
            _fmt_uf(r.net_sale),
            Text(_fmt_uf(r.profit, sign=True), style=rc),
            Text(_fmt_pct(r.roi), style=f"bold {rc}"),
            f"{r.equity_multiple:.2f}x",
        )

    console.print(t)
    console.print()


def _print_bid_table(asset: Asset, params: ScenarioParams):
    bid_rows = FinancialEngine.build_bid_table(asset, params)

    t = Table(
        title=f"[bold]Precio Máximo a Licitar — Escenario {params.name} "
              f"({params.renovation_uf_per_m2} UF/m² reno | {params.hidden_debts_uf:.0f} UF deudas)[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        border_style="dim white",
    )
    t.add_column("ROI Obj.",     justify="right", width=9)
    t.add_column("Max Bid",      justify="right", width=11)
    t.add_column("UF/m²",        justify="right", width=7)
    t.add_column("vs Base",      justify="right", width=11)
    t.add_column("Costos tot.",  justify="right", width=11)
    t.add_column("Utilidad",     justify="right", width=10)
    t.add_column("Nivel",        width=22)
    t.add_column("Nota",         min_width=38)

    for row in bid_rows:
        roi      = row["roi_target"]
        is_obj   = roi == 0.20
        is_open  = roi == 0.25
        style    = "bold yellow" if is_obj else ("bold orange3" if is_open else "")
        viable   = row["max_bid_uf"] > 0

        bid_str = _fmt_uf(row["max_bid_uf"]) if viable else "[red]NO VIABLE[/red]"
        upd_str = _fmt_uf(row["buffer_uf"], sign=True) if viable else "—"

        t.add_row(
            Text(_fmt_pct(roi), style=style),
            Text(bid_str, style=style),
            Text(f"{row['uf_per_m2']:.1f}" if viable else "—", style=style),
            Text(upd_str, style=style),
            _fmt_uf(row["total_costs"]) if viable else "—",
            Text(_fmt_uf(row["profit"], sign=True) if viable else "—",
                 style=_roi_color(roi) if viable else "dim"),
            Text(row["level_name"], style=style),
            row["description"],
        )

    console.print(t)
    console.print()


def _print_tornado(asset: Asset, params: ScenarioParams):
    bid_20 = FinancialEngine.max_bid(asset, params, 0.20)
    if bid_20 <= 0:
        console.print("[red]  ⚠ Activo no viable al ROI objetivo — tornado omitido[/red]\n")
        return

    rows = SensitivityAnalyzer.tornado(asset, params, bid_20)

    t = Table(
        title=f"[bold]Sensibilidad (Tornado) — Entrada: {bid_20:.0f} UF | ROI base: {rows[0]['base_roi']*100:.1f}%[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white",
        border_style="dim white",
    )
    t.add_column("Variable",        min_width=22)
    t.add_column("ROI base",        justify="right", width=10)
    t.add_column("Escenario (+)",   justify="right", width=14)
    t.add_column("Escenario (−)",   justify="right", width=14)
    t.add_column("Swing",           justify="right", width=10)
    t.add_column("Ranking",         width=10)

    for i, row in enumerate(rows):
        rank = ["1° CLAVE", "2°", "3°", "4°", "5°"][min(i, 4)]
        rank_style = "bold red" if i == 0 else "yellow" if i == 1 else "dim"
        t.add_row(
            row["label"],
            _fmt_pct(row["base_roi"]),
            Text(_fmt_pct(row["roi_high"], sign=True), style="green"),
            Text(_fmt_pct(row["roi_low"],  sign=True), style="red"),
            Text(_fmt_pct(row["total_swing"]), style="bold"),
            Text(rank, style=rank_style),
        )

    console.print(t)
    console.print()


def _print_monte_carlo(mc: MonteCarloResult, entry_price: float):
    t = Table(
        title=f"[bold]Monte Carlo — {mc.n_simulations:,} simulaciones | Entrada: {entry_price:.0f} UF (20% ROI)[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white",
        border_style="dim white",
    )
    t.add_column("Métrica",                min_width=28)
    t.add_column("Valor",                  justify="right", width=12)

    rows_data = [
        ("ROI Esperado E[ROI]",          _fmt_pct(mc.mean_roi),          _roi_color(mc.mean_roi)),
        ("ROI Mediana",                   _fmt_pct(mc.median_roi),        _roi_color(mc.median_roi)),
        ("Desv. estándar (σ)",            _fmt_pct(mc.std_roi),           "white"),
        ("P5   (tail izquierda)",         _fmt_pct(mc.p5_roi),            _roi_color(mc.p5_roi)),
        ("P25",                           _fmt_pct(mc.p25_roi),           _roi_color(mc.p25_roi)),
        ("P75",                           _fmt_pct(mc.p75_roi),           _roi_color(mc.p75_roi)),
        ("P95  (tail derecha)",           _fmt_pct(mc.p95_roi),           _roi_color(mc.p95_roi)),
        ("P(ROI > 0%)   — sin pérdida",   _fmt_pct(mc.prob_positive),    "green" if mc.prob_positive > 0.85 else "yellow"),
        ("P(ROI > 15%)  — aceptable",     _fmt_pct(mc.prob_above_15pct), "green" if mc.prob_above_15pct > 0.65 else "yellow"),
        ("P(ROI > 20%)  — objetivo",      _fmt_pct(mc.prob_above_20pct), "green" if mc.prob_above_20pct > 0.50 else "yellow"),
        ("P(ROI > 30%)  — excelente",     _fmt_pct(mc.prob_above_30pct), "green" if mc.prob_above_30pct > 0.35 else "dim"),
        ("P(ROI < 0%)   — pérdida",       _fmt_pct(mc.prob_loss),        "green" if mc.prob_loss < 0.10 else "red"),
    ]

    for label, val, color in rows_data:
        t.add_row(label, Text(val, style=color))

    console.print(t)
    console.print()


def _print_recommendation(asset: Asset):
    opt_r  = ScenarioResult(asset=asset, params=OPTIMISTA,   entry_price=asset.catalog_base_uf)
    real_r = ScenarioResult(asset=asset, params=REALISTA,    entry_price=asset.catalog_base_uf)
    cons_r = ScenarioResult(asset=asset, params=CONSERVADOR, entry_price=asset.catalog_base_uf)
    stress_r = ScenarioResult(asset=asset, params=STRESS,    entry_price=asset.catalog_base_uf)

    bid_25 = FinancialEngine.max_bid(asset, REALISTA, 0.25)
    bid_20 = FinancialEngine.max_bid(asset, REALISTA, 0.20)
    bid_15 = FinancialEngine.max_bid(asset, REALISTA, 0.15)

    has_bath_risk = asset.bathroom_adjustment < 1
    has_comp_risk = asset.n_comparables < 5

    if real_r.roi >= 0.20 and not has_comp_risk:
        verdict = "LICITAR"
        color   = "bold green"
    elif real_r.roi >= 0.10 or (real_r.roi >= 0.05 and not has_bath_risk and not has_comp_risk):
        verdict = "LICITAR CON CAUTELA"
        color   = "bold yellow"
    elif real_r.roi >= 0.0:
        verdict = "NEGOCIAR / SEGUNDA SUBASTA"
        color   = "bold orange3"
    else:
        verdict = "PASAR — ROI negativo en escenario realista"
        color   = "bold red"

    urgency_note = (
        "\n[bold red]⚠ DECISIÓN REQUERIDA ESTA SEMANA — remate inminente[/bold red]"
        if asset.is_urgent else ""
    )

    body = (
        f"Veredicto: [{color}]{verdict}[/{color}]"
        f"{urgency_note}\n\n"
        f"[bold]ROI a precio base catálogo ({asset.catalog_base_uf} UF):[/bold]\n"
        f"  Optimista:   {_fmt_pct(opt_r.roi)}  (reno 3 UF/m², 50 UF deudas)\n"
        f"  Realista:    {_fmt_pct(real_r.roi)}  (reno 4.5 UF/m², 100 UF deudas) ← escenario de referencia\n"
        f"  Conservador: {_fmt_pct(cons_r.roi)}  (reno 6 UF/m², 150 UF deudas)\n"
        f"  Stress:      {_fmt_pct(stress_r.roi)}  (reno 8 UF/m², 200 UF deudas)\n\n"
        f"[bold]Estrategia de bid — escenario realista:[/bold]\n"
        f"  Bid apertura  (25% ROI): [orange3]{_fmt_uf(bid_25) if bid_25 > 0 else 'NO VIABLE'}[/orange3]\n"
        f"  Precio objetivo (20%):   [bold yellow]{_fmt_uf(bid_20) if bid_20 > 0 else 'NO VIABLE'}[/bold yellow]  ← máximo recomendado\n"
        f"  Mínimo aceptable (15%):  [dim]{_fmt_uf(bid_15) if bid_15 > 0 else 'NO VIABLE'}[/dim]\n"
        f"  Base catálogo:           [dim]{_fmt_uf(asset.catalog_base_uf)}[/dim] ({_fmt_pct(asset.discount_vs_catalog)} vs fair)\n"
    )

    if has_bath_risk:
        body += f"\n[yellow]⚠ 3d/1b: fair value ajustado -12%. Los max bids ya incorporan este descuento.[/yellow]"
    if has_comp_risk:
        body += f"\n[red]⚠ Solo {asset.n_comparables} comp(s): incertidumbre alta en UF/m² de mercado. Verificar.[/red]"
    if asset.is_urgent:
        body += f"\n[red]⚠ Validar: comparables, sector, deudas (contribuciones + gastos comunes) ANTES del {asset.auction_date.strftime('%d/%m/%Y')}.[/red]"

    console.print(Panel(body, title="[bold]Recomendación Final[/bold]", border_style="yellow"))
    console.print()


# ---------------------------------------------------------------------------
# Capital allocation report
# ---------------------------------------------------------------------------

def print_capital_allocation(
    assets: List[Asset],
    params: ScenarioParams,
    budget_uf: float,
    target_roi: float = 0.20,
):
    console.print(Rule(f"[bold]OPTIMIZACIÓN DE CAPITAL — {budget_uf:,.0f} UF disponibles[/bold]", style="yellow"))

    result    = CapitalAllocator.allocate(assets, params, budget_uf, target_roi)
    conflicts = CapitalAllocator.same_day_conflict(assets, params, budget_uf, target_roi)

    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim white")
    t.add_column("Activo",          min_width=22)
    t.add_column("Ciudad",          width=12)
    t.add_column(f"Bid ({target_roi*100:.0f}% ROI)", justify="right", width=12)
    t.add_column("Utilidad est.",   justify="right", width=13)
    t.add_column("Seleccionado",    width=13)

    for a in assets:
        selected = a.id in result["selected_ids"]
        bid      = FinancialEngine.max_bid(a, params, target_roi)
        r        = ScenarioResult(asset=a, params=params, entry_price=bid)
        t.add_row(
            a.name, a.city,
            _fmt_uf(bid) if bid > 0 else "[red]NO VIABLE[/red]",
            _fmt_uf(r.profit, sign=True) if bid > 0 else "—",
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
        console.print("[bold yellow]⚠ Conflictos de capital — mismo día de remate:[/bold yellow]")
        for dt, c in conflicts.items():
            ids = [a.id for a in c["assets"]]
            console.print(
                f"  {dt}: Activos {ids}\n"
                f"    Capital para ganar ambos: {_fmt_uf(c['capital_if_win_all'])}\n"
                f"    Capital para licitar en ambos (ganas 1): {_fmt_uf(c['capital_needed_to_bid_all'])}\n"
                f"    ¿Puede licitar en ambos? "
                + ("[green]SÍ[/green]" if c["can_bid_all_with_budget"] else "[red]NO[/red]")
            )
    console.print()
