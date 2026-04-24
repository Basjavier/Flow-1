"""
Institutional console reports — calibrated to real Chilean flip practitioners.
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
    FinancialEngine,
    COSMETICO, ESTANDAR, DETERIORADO, STRESS,
    BID_LEVEL_META,
)
from .sensitivity import SensitivityAnalyzer
from .optimizer import CapitalAllocator

console = Console(width=max(160, Console().width))


def _roi_color(roi: float) -> str:
    if roi >= 0.22:  return "bright_green"
    if roi >= 0.15:  return "green"
    if roi >= 0.08:  return "yellow"
    if roi >= 0.0:   return "dark_orange"
    return "red"


def _fmt_uf(val: float, sign: bool = False) -> str:
    s = "+" if sign and val >= 0 else ""
    return f"{s}{val:,.1f} UF"


def _fmt_pct(val: float, sign: bool = False) -> str:
    s = "+" if sign and val >= 0 else ""
    return f"{s}{val*100:.1f}%"


def _viability_badge(roi: float) -> str:
    if roi >= 0.22:  return "[bright_green]●[/bright_green] EXCELENTE"
    if roi >= 0.18:  return "[green]●[/green] BUENO"
    if roi >= 0.12:  return "[yellow]●[/yellow] ACEPTABLE"
    if roi >= 0.00:  return "[dark_orange]●[/dark_orange] AJUSTADO"
    return "[red]●[/red] PÉRDIDA"


# ---------------------------------------------------------------------------
# Portfolio summary
# ---------------------------------------------------------------------------

def print_portfolio_summary(assets: List[Asset], params: ScenarioParams = ESTANDAR):
    console.print()
    console.print(Rule(
        "[bold white]ANÁLISIS TIER A — FLIP INMOBILIARIO REMATES CHILE[/bold white]",
        style="dim white"
    ))
    console.print(
        f"[dim]  Escenario referencia: {params.name} | "
        f"Reno: {params.renovation_uf_per_m2} UF/m² (contingencia incluida) | "
        f"Deudas ocultas: {params.hidden_debts_uf:.0f} UF | "
        f"Ciclo: {params.holding_months}m | "
        f"Salida: {params.sale_pct_market*100:.0f}% mercado | "
        f"Corretaje salida: {params.selling_costs_pct*100:.1f}%[/dim]"
    )
    console.print()

    t = Table(
        title="[bold]Resumen Ejecutivo de Portafolio[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        border_style="dim white",
        min_width=130,
    )

    t.add_column("#",            style="dim",      width=7)
    t.add_column("Activo",       min_width=20)
    t.add_column("Ciudad",       width=11)
    t.add_column("Config",       width=10)
    t.add_column("m²",           justify="right",  width=5)
    t.add_column("Base UF",      justify="right",  width=9)
    t.add_column("Fair Value",   justify="right",  width=11)
    t.add_column("Reno est.",    justify="right",  width=10)
    t.add_column("Cosmético",    justify="right",  width=11)
    t.add_column("Estándar",     justify="right",  width=11)
    t.add_column("Deteriorado",  justify="right",  width=12)
    t.add_column("Max Bid 20%",  justify="right",  width=11)
    t.add_column("Comps",        justify="right",  width=6)
    t.add_column("Días",         justify="right",  width=6)
    t.add_column("Alerta",       width=9)

    for asset in assets:
        cos_r  = ScenarioResult(asset=asset, params=COSMETICO,   entry_price=asset.catalog_base_uf)
        est_r  = ScenarioResult(asset=asset, params=ESTANDAR,    entry_price=asset.catalog_base_uf)
        det_r  = ScenarioResult(asset=asset, params=DETERIORADO, entry_price=asset.catalog_base_uf)
        max_20 = FinancialEngine.max_bid(asset, ESTANDAR, 0.20)

        urgency    = "[bold red]URGENTE[/bold red]" if asset.is_urgent else "[green]OK[/green]"
        bath_flag  = "[yellow]*1b[/yellow]" if asset.bathroom_adjustment < 1 else ""
        comp_color = "green" if asset.n_comparables >= 8 else "yellow" if asset.n_comparables >= 5 else "red"
        reno_uf    = ESTANDAR.renovation_uf_per_m2 * asset.m2

        t.add_row(
            asset.id,
            asset.name,
            asset.city,
            f"{asset.bedrooms}d/{asset.bathrooms}b/{asset.parking}e{bath_flag}",
            str(int(asset.m2)),
            _fmt_uf(asset.catalog_base_uf),
            _fmt_uf(asset.fair_value_adjusted),
            _fmt_uf(reno_uf),
            Text(_fmt_pct(cos_r.roi),  style=_roi_color(cos_r.roi)),
            Text(_fmt_pct(est_r.roi),  style=_roi_color(est_r.roi)),
            Text(_fmt_pct(det_r.roi),  style=_roi_color(det_r.roi)),
            f"[bold]{_fmt_uf(max_20) if max_20 > 0 else 'N/V'}[/bold]",
            Text(str(asset.n_comparables), style=comp_color),
            str(asset.days_to_auction),
            urgency,
        )

    console.print(t)

    # Comparables sanity check
    for asset in assets:
        warning = FinancialEngine.comparables_sanity_check(asset)
        if warning:
            console.print(
                f"  [yellow]⚠ #{asset.id} {asset.name}:[/yellow] "
                f"Comparable usado {warning['asset_uf_m2']:.1f} UF/m² vs "
                f"benchmark mercado {warning['benchmark_uf_m2']:.1f} UF/m² "
                f"({warning['gap_pct']*100:.0f}% brecha). "
                f"Si comps son correctos, potencial upside real: {_fmt_uf(warning['implied_upside'])}."
            )
    console.print()


# ---------------------------------------------------------------------------
# Full asset report
# ---------------------------------------------------------------------------

def print_asset_report(asset: Asset, run_mc: bool = True, n_mc: int = 10_000):
    console.print()
    console.print(Rule(
        f"[bold cyan]ANÁLISIS DETALLADO — {asset.name.upper()}[/bold cyan]",
        style="cyan"
    ))

    urgency_str = (
        f"[bold red]⚠ URGENTE — {asset.days_to_auction} días[/bold red]"
        if asset.is_urgent else f"[green]{asset.days_to_auction} días al remate[/green]"
    )
    bath_str = (
        "\n[yellow]⚠ Ajuste 1-baño: fair value -12% (3d+/1b vs comps 3d/2b)[/yellow]"
        if asset.bathroom_adjustment < 1 else ""
    )

    # Sanity check warning
    sanity = FinancialEngine.comparables_sanity_check(asset)
    sanity_str = ""
    if sanity:
        sanity_str = (
            f"\n[yellow]⚠ COMPARABLE GAP: {sanity['asset_uf_m2']:.1f} UF/m² vs benchmark "
            f"{sanity['benchmark_uf_m2']:.1f} UF/m² ({sanity['gap_pct']*100:.0f}% bajo mercado). "
            f"Verificar si los comps son correctos.[/yellow]"
        )

    reno_estandar = ESTANDAR.renovation_uf_per_m2 * asset.m2

    header = (
        f"[bold white]{asset.address} | {asset.city}[/bold white]\n"
        f"{asset.bedrooms}d / {asset.bathrooms}b / {asset.parking}e  |  {asset.m2}m²  |  Score: {asset.score}\n"
        f"Remate: [bold]{asset.auction_date.strftime('%d %b %Y')}[/bold]  |  {urgency_str}\n"
        f"Confianza comps: [{'green' if asset.n_comparables >= 8 else 'yellow' if asset.n_comparables >= 5 else 'red'}]"
        f"{asset.comparable_confidence} ({asset.n_comparables} comparables)[/]\n"
        f"Reno Estándar estimada: [bold]{_fmt_uf(reno_estandar)}[/bold] "
        f"({ESTANDAR.renovation_uf_per_m2} UF/m² · contingencia incluida)"
        f"{bath_str}{sanity_str}"
    )
    if asset.notes:
        header += f"\n[dim]{asset.notes}[/dim]"

    console.print(Panel(header, title="[bold]Ficha del Activo[/bold]", border_style="cyan"))
    console.print()

    _print_cost_breakdown(asset)
    _print_flip_phases(asset)
    _print_bid_table(asset, ESTANDAR)
    _print_tornado(asset, ESTANDAR)

    if run_mc:
        bid_20 = FinancialEngine.max_bid(asset, ESTANDAR, 0.20)
        entry  = bid_20 if bid_20 > 0 else asset.catalog_base_uf
        mc     = SensitivityAnalyzer.monte_carlo(asset, ESTANDAR, entry, n_simulations=n_mc)
        _print_monte_carlo(mc, entry)

    _print_recommendation(asset)
    console.print()


def _print_cost_breakdown(asset: Asset):
    """Real cost waterfall for each scenario at catalog base price."""
    t = Table(
        title=f"[bold]Waterfall de Costos Reales — Entrada base catálogo ({asset.catalog_base_uf:.0f} UF)[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold magenta",
        border_style="dim white",
    )
    t.add_column("Escenario",      width=12)
    t.add_column("Entrada",        justify="right", width=10)
    t.add_column("Tx entrada",     justify="right", width=10)
    t.add_column("Holding",        justify="right", width=9)
    t.add_column("Renovación",     justify="right", width=14)
    t.add_column("Deudas ocultas", justify="right", width=13)
    t.add_column("COSTO TOTAL",    justify="right", width=12)
    t.add_column("Venta neta",     justify="right", width=11)
    t.add_column("Utilidad",       justify="right", width=10)
    t.add_column("ROI",            justify="right", width=8)

    for params in [COSMETICO, ESTANDAR, DETERIORADO, STRESS]:
        ep  = asset.catalog_base_uf
        r   = ScenarioResult(asset=asset, params=params, entry_price=ep)
        bd  = FinancialEngine.cost_breakdown(asset, params, ep)
        rc  = _roi_color(r.roi)

        t.add_row(
            f"[bold]{params.name}[/bold]",
            _fmt_uf(ep),
            _fmt_uf(bd["tx_costs_entry"]),
            _fmt_uf(bd["holding_costs"]),
            f"{_fmt_uf(bd['renovation_uf'])}  ({params.renovation_uf_per_m2} UF/m²)",
            _fmt_uf(bd["hidden_debts"]),
            Text(_fmt_uf(r.total_costs), style=f"bold {rc}"),
            _fmt_uf(r.net_sale),
            Text(_fmt_uf(r.profit, sign=True), style=rc),
            Text(_fmt_pct(r.roi), style=f"bold {rc}"),
        )

    console.print(t)
    console.print()


def _print_flip_phases(asset: Asset):
    """Timeline and ROI by flip phase for each scenario."""
    t = Table(
        title="[bold]Ciclo del Flip — Parámetros por Escenario (entrada = precio base catálogo)[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white",
        border_style="dim white",
    )
    t.add_column("Escenario",     width=12)
    t.add_column("Holding",       justify="right", width=8)
    t.add_column("Reno UF/m²",   justify="right", width=10)
    t.add_column("Deudas",        justify="right", width=9)
    t.add_column("% Salida",      justify="right", width=9)
    t.add_column("Corretaje",     justify="right", width=9)
    t.add_column("Aprec.",        justify="right", width=8)
    t.add_column("Costo total",   justify="right", width=11)
    t.add_column("Venta neta",    justify="right", width=11)
    t.add_column("Utilidad",      justify="right", width=10)
    t.add_column("ROI",           justify="right", width=8)
    t.add_column("Viabilidad",    width=16)

    for params in [COSMETICO, ESTANDAR, DETERIORADO, STRESS]:
        r   = ScenarioResult(asset=asset, params=params, entry_price=asset.catalog_base_uf)
        rc  = _roi_color(r.roi)

        t.add_row(
            f"[bold]{params.name}[/bold]",
            f"{params.holding_months}m",
            f"{params.renovation_uf_per_m2}",
            f"{params.hidden_debts_uf:.0f} UF",
            _fmt_pct(params.sale_pct_market),
            _fmt_pct(params.selling_costs_pct),
            _fmt_pct(params.appreciation_annual),
            _fmt_uf(r.total_costs),
            _fmt_uf(r.net_sale),
            Text(_fmt_uf(r.profit, sign=True), style=rc),
            Text(_fmt_pct(r.roi), style=f"bold {rc}"),
            _viability_badge(r.roi),
        )

    console.print(t)
    console.print()


def _print_bid_table(asset: Asset, params: ScenarioParams):
    bid_rows = FinancialEngine.build_bid_table(asset, params)

    # Header note
    note = (
        f"Escenario {params.name} | "
        f"Reno {params.renovation_uf_per_m2} UF/m² ({params.renovation_uf_per_m2 * asset.m2:.0f} UF tot.) | "
        f"Deudas {params.hidden_debts_uf:.0f} UF | "
        f"Ciclo {params.holding_months}m | "
        f"Salida {params.sale_pct_market*100:.0f}% mercado"
    )

    t = Table(
        title=f"[bold]Precio Máximo a Licitar[/bold]\n[dim]{note}[/dim]",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        border_style="dim white",
    )
    t.add_column("ROI Obj.",     justify="right", width=9)
    t.add_column("Max Bid",      justify="right", width=11)
    t.add_column("UF/m²",        justify="right", width=7)
    t.add_column("vs Base",      justify="right", width=11)
    t.add_column("Costo total",  justify="right", width=11)
    t.add_column("Utilidad",     justify="right", width=10)
    t.add_column("Nivel",        width=18)
    t.add_column("Nota",         min_width=40)

    for row in bid_rows:
        roi     = row["roi_target"]
        is_obj  = roi == 0.20
        is_open = roi == 0.25
        style   = "bold yellow" if is_obj else ("bold orange3" if is_open else "")
        viable  = row["max_bid_uf"] > asset.catalog_base_uf * 0.5

        if not viable or row["max_bid_uf"] <= 0:
            t.add_row(
                Text(_fmt_pct(roi), style=style),
                Text("[red]NO VIABLE[/red]"),
                "—", "—", "—", "—",
                Text(row["level_name"], style="dim"),
                "[dim]Renovación+deudas consumen el spread[/dim]",
            )
            continue

        vs_base_style = "green" if row["buffer_uf"] >= 0 else "red"
        t.add_row(
            Text(_fmt_pct(roi), style=style),
            Text(_fmt_uf(row["max_bid_uf"]), style=style),
            Text(f"{row['uf_per_m2']:.1f}", style=style),
            Text(_fmt_uf(row["buffer_uf"], sign=True), style=vs_base_style),
            _fmt_uf(row["total_costs"]),
            Text(_fmt_uf(row["profit"], sign=True), style=_roi_color(roi)),
            Text(row["level_name"], style=style),
            row["description"],
        )

    console.print(t)
    console.print()


def _print_tornado(asset: Asset, params: ScenarioParams):
    bid_20 = FinancialEngine.max_bid(asset, params, 0.20)
    entry  = bid_20 if bid_20 > 0 else asset.catalog_base_uf
    rows   = SensitivityAnalyzer.tornado(asset, params, entry)

    t = Table(
        title=f"[bold]Sensibilidad (Tornado) — ¿Qué variable mueve más el ROI?[/bold]\n"
              f"[dim]Entrada: {entry:.0f} UF | ROI base: {rows[0]['base_roi']*100:.1f}%[/dim]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white",
        border_style="dim white",
    )
    t.add_column("Variable",        min_width=24)
    t.add_column("ROI base",        justify="right", width=10)
    t.add_column("Escenario (+)",   justify="right", width=14)
    t.add_column("Escenario (−)",   justify="right", width=14)
    t.add_column("Swing total",     justify="right", width=12)
    t.add_column("Riesgo",          width=12)

    for i, row in enumerate(rows):
        rank = ["1° CRÍTICO", "2° ALTO", "3° MEDIO", "4°", "5°"][min(i, 4)]
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
        title=f"[bold]Monte Carlo — {mc.n_simulations:,} simulaciones[/bold]\n"
              f"[dim]Entrada: {entry_price:.0f} UF | Escenario: {mc.params.name} | "
              f"Shocks: mercado ±{mc.asset.comparable_uncertainty_std*100:.0f}%, "
              f"reno ±20%, deudas ±40%, holding ±1–4m, salida ±5pp[/dim]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white",
        border_style="dim white",
    )
    t.add_column("Métrica",               min_width=30)
    t.add_column("Valor",                 justify="right", width=12)
    t.add_column("Interpretación",        min_width=30)

    rows_data = [
        ("ROI Esperado E[ROI]",         mc.mean_roi,         _roi_color(mc.mean_roi),
         "promedio de todos los escenarios simulados"),
        ("ROI Mediana (P50)",           mc.median_roi,        _roi_color(mc.median_roi),
         "ROI que supera la mitad de las simulaciones"),
        ("Desv. estándar (σ)",          mc.std_roi,           "white",
         "dispersión — mayor σ = mayor incertidumbre"),
        ("P5  — tail de pérdida",       mc.p5_roi,            _roi_color(mc.p5_roi),
         "1 de cada 20 ops sale peor que esto"),
        ("P25",                          mc.p25_roi,           _roi_color(mc.p25_roi),
         "25% de las operaciones quedan bajo esto"),
        ("P75",                          mc.p75_roi,           _roi_color(mc.p75_roi),
         "75% de las operaciones quedan bajo esto"),
        ("P95 — upside",                mc.p95_roi,           _roi_color(mc.p95_roi),
         "1 de cada 20 ops supera este ROI"),
        ("─────────────────────────", None, "dim", ""),
        ("P(ROI > 0%)  — sin pérdida",  mc.prob_positive,     "green" if mc.prob_positive > 0.85 else "yellow",
         "probabilidad de no perder dinero"),
        ("P(ROI > 15%) — aceptable",    mc.prob_above_15pct,  "green" if mc.prob_above_15pct > 0.60 else "yellow",
         "prob. de superar piso mínimo de flip"),
        ("P(ROI > 20%) — objetivo",     mc.prob_above_20pct,  "green" if mc.prob_above_20pct > 0.45 else "yellow",
         "prob. de alcanzar ROI objetivo"),
        ("P(ROI > 30%) — excelente",    mc.prob_above_30pct,  "green" if mc.prob_above_30pct > 0.25 else "dim",
         "prob. de lograr ROI excepcional"),
        ("P(ROI < 0%)  — pérdida",      mc.prob_loss,         "green" if mc.prob_loss < 0.12 else "red",
         "probabilidad de perder capital"),
    ]

    for item in rows_data:
        if item[1] is None:
            t.add_row(Text(item[0], style="dim"), "", "")
        else:
            t.add_row(item[0], Text(_fmt_pct(item[1]), style=item[2]), f"[dim]{item[3]}[/dim]")

    console.print(t)
    console.print()


def _print_recommendation(asset: Asset):
    cos_r  = ScenarioResult(asset=asset, params=COSMETICO,   entry_price=asset.catalog_base_uf)
    est_r  = ScenarioResult(asset=asset, params=ESTANDAR,    entry_price=asset.catalog_base_uf)
    det_r  = ScenarioResult(asset=asset, params=DETERIORADO, entry_price=asset.catalog_base_uf)
    str_r  = ScenarioResult(asset=asset, params=STRESS,      entry_price=asset.catalog_base_uf)

    bid_25 = FinancialEngine.max_bid(asset, ESTANDAR, 0.25)
    bid_20 = FinancialEngine.max_bid(asset, ESTANDAR, 0.20)
    bid_15 = FinancialEngine.max_bid(asset, ESTANDAR, 0.15)

    has_bath_risk = asset.bathroom_adjustment < 1
    has_comp_risk = asset.n_comparables < 5
    sanity        = FinancialEngine.comparables_sanity_check(asset)

    # Verdict logic based on ESTÁNDAR scenario (the realistic base)
    if est_r.roi >= 0.18 and not has_comp_risk:
        verdict = "LICITAR"
        color   = "bold green"
    elif est_r.roi >= 0.10 or (cos_r.roi >= 0.18 and not has_comp_risk):
        verdict = "LICITAR CON CAUTELA"
        color   = "bold yellow"
    elif cos_r.roi >= 0.10:
        verdict = "NEGOCIAR / SEGUNDA SUBASTA"
        color   = "bold orange3"
    else:
        verdict = "PASAR — spreads insuficientes"
        color   = "bold red"

    urgency_note = (
        "\n[bold red]⚠ DECISIÓN REQUERIDA ESTA SEMANA — remate inminente[/bold red]"
        if asset.is_urgent else ""
    )

    body = (
        f"Veredicto: [{color}]{verdict}[/{color}]"
        f"{urgency_note}\n\n"
        f"[bold]ROI a precio base catálogo ({asset.catalog_base_uf:.0f} UF):[/bold]\n"
        f"  Cosmético:   [bold]{_fmt_pct(cos_r.roi)}[/bold]  "
        f"(4.5 UF/m² · 80 UF deudas · ciclo 6m · salida 83%)  "
        f"{_viability_badge(cos_r.roi)}\n"
        f"  Estándar:    [bold]{_fmt_pct(est_r.roi)}[/bold]  "
        f"(8 UF/m² · 120 UF deudas · ciclo 10m · salida 88%)  "
        f"{_viability_badge(est_r.roi)}  ← referencia\n"
        f"  Deteriorado: [bold]{_fmt_pct(det_r.roi)}[/bold]  "
        f"(13 UF/m² · 170 UF deudas · ciclo 13m · salida 85%)  "
        f"{_viability_badge(det_r.roi)}\n"
        f"  Stress:      [bold]{_fmt_pct(str_r.roi)}[/bold]  "
        f"(18 UF/m² · 220 UF deudas · ciclo 17m · salida 80%)  "
        f"{_viability_badge(str_r.roi)}\n\n"
        f"[bold]Estrategia de bid — escenario Estándar:[/bold]\n"
        f"  Bid apertura  (25% ROI): [orange3]{_fmt_uf(bid_25) if bid_25 > 0 else 'NO VIABLE — primera subasta'}[/orange3]\n"
        f"  Precio objetivo (20%):   [bold yellow]{_fmt_uf(bid_20) if bid_20 > 0 else 'NO VIABLE'}[/bold yellow]"
        f"  ← máximo recomendado\n"
        f"  Piso mínimo (15%):       [dim]{_fmt_uf(bid_15) if bid_15 > 0 else 'NO VIABLE'}[/dim]\n"
        f"  Base catálogo:           [dim]{_fmt_uf(asset.catalog_base_uf)}[/dim] "
        f"({_fmt_pct(asset.discount_vs_catalog)} descuento vs fair)\n"
    )

    if has_bath_risk:
        body += f"\n[yellow]⚠ 3d/1b: fair value reducido 12%. Max bids ya incorporan el descuento.[/yellow]"
    if has_comp_risk:
        body += f"\n[red]⚠ Solo {asset.n_comparables} comp(s). Alta incertidumbre en UF/m² de mercado.[/red]"
    if asset.is_urgent:
        body += (
            f"\n[red]⚠ Due diligence urgente antes del {asset.auction_date.strftime('%d/%m/%Y')}:\n"
            f"   — Verificar comparables en Portalinmobiliario / Toctoc\n"
            f"   — Certificar deudas de gastos comunes y contribuciones (SII + administración)\n"
            f"   — Confirmar sector y acceso a la propiedad[/red]"
        )
    if sanity:
        body += (
            f"\n[yellow]⚠ COMPARABLE GAP: modelo usa {sanity['asset_uf_m2']:.1f} UF/m², "
            f"benchmark mercado {sanity['benchmark_uf_m2']:.1f} UF/m². "
            f"Si los comparables son correctos, los números son conservadores. "
            f"Si hay error en comps, el upside real puede ser significativamente mayor.[/yellow]"
        )

    console.print(Panel(body, title="[bold]Recomendación Final[/bold]", border_style="yellow"))
    console.print()


# ---------------------------------------------------------------------------
# Capital allocation
# ---------------------------------------------------------------------------

def print_capital_allocation(
    assets: List[Asset],
    params: ScenarioParams,
    budget_uf: float,
    target_roi: float = 0.20,
):
    console.print(Rule(
        f"[bold]OPTIMIZACIÓN DE CAPITAL — {budget_uf:,.0f} UF disponibles[/bold]",
        style="yellow"
    ))

    result    = CapitalAllocator.allocate(assets, params, budget_uf, target_roi)
    conflicts = CapitalAllocator.same_day_conflict(assets, params, budget_uf, target_roi)

    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim white")
    t.add_column("Activo",         min_width=22)
    t.add_column("Ciudad",         width=12)
    t.add_column(f"Bid ({target_roi*100:.0f}%)", justify="right", width=11)
    t.add_column("Utilidad est.",  justify="right", width=13)
    t.add_column("ROI efectivo",   justify="right", width=12)
    t.add_column("Seleccionado",   width=12)

    for a in assets:
        selected = a.id in result["selected_ids"]
        bid      = FinancialEngine.max_bid(a, params, target_roi)
        if bid <= 0:
            t.add_row(a.name, a.city, "[red]NO VIABLE[/red]", "—", "—",
                      Text("NO", style="dim"))
            continue
        r = ScenarioResult(asset=a, params=params, entry_price=bid)
        t.add_row(
            a.name, a.city,
            _fmt_uf(bid),
            _fmt_uf(r.profit, sign=True),
            Text(_fmt_pct(r.roi), style=_roi_color(r.roi)),
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
                f"    Capital para licitar en ambos: {_fmt_uf(c['capital_needed_to_bid_all'])}\n"
                f"    ¿Puede licitar en ambos? "
                + ("[green]SÍ[/green]" if c["can_bid_all_with_budget"] else "[red]NO[/red]")
            )
    console.print()
