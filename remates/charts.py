"""
Institutional-grade visualization module.
All charts use a dark, professional style consistent with PE/HF reporting.
"""

from __future__ import annotations
import os
from typing import List, Optional

import numpy as np
import matplotlib
matplotlib.use("Agg")  # headless rendering
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch
import matplotlib.ticker as mticker

from .models import Asset, ScenarioParams, ScenarioResult, MonteCarloResult
from .engine import FinancialEngine, BASE, CONSERVATIVE, STRESS, SCENARIOS
from .sensitivity import SensitivityAnalyzer

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

STYLE = {
    "bg": "#0d1117",
    "panel": "#161b22",
    "text": "#e6edf3",
    "grid": "#30363d",
    "green": "#3fb950",
    "yellow": "#d29922",
    "red": "#f85149",
    "blue": "#58a6ff",
    "purple": "#bc8cff",
    "orange": "#ffa657",
}


def _apply_style(fig, axes_flat):
    fig.patch.set_facecolor(STYLE["bg"])
    for ax in axes_flat:
        ax.set_facecolor(STYLE["panel"])
        ax.tick_params(colors=STYLE["text"], labelsize=8)
        ax.xaxis.label.set_color(STYLE["text"])
        ax.yaxis.label.set_color(STYLE["text"])
        ax.title.set_color(STYLE["text"])
        for spine in ax.spines.values():
            spine.set_edgecolor(STYLE["grid"])
        ax.grid(True, color=STYLE["grid"], alpha=0.5, linewidth=0.5)


def _save_or_show(fig, output_path: Optional[str]):
    plt.tight_layout()
    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        print(f"  [chart] Guardado: {output_path}")
    else:
        plt.show()
    plt.close(fig)


# ---------------------------------------------------------------------------
# Individual asset report (2x2)
# ---------------------------------------------------------------------------

def plot_asset_report(
    asset: Asset,
    params: ScenarioParams,
    entry_price: float,
    mc_result: Optional[MonteCarloResult] = None,
    output_path: Optional[str] = None,
):
    fig = plt.figure(figsize=(18, 12))
    fig.patch.set_facecolor(STYLE["bg"])

    gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.40, wspace=0.30)
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[1, 0])
    ax4 = fig.add_subplot(gs[1, 1])

    urgency = f"  ⚠ URGENTE — {asset.days_to_auction}d" if asset.is_urgent else f"  {asset.days_to_auction}d al remate"
    conf = f"Confianza comps: {asset.comparable_confidence} ({asset.n_comparables})"
    bath_adj = f"  | Ajuste 1-baño: {(1 - asset.bathroom_adjustment)*100:.0f}% descuento" if asset.bathroom_adjustment < 1 else ""

    fig.suptitle(
        f"{asset.label()}\n"
        f"Remate: {asset.auction_date.strftime('%d %b %Y')}{urgency}  |  Score: {asset.score}{bath_adj}  |  {conf}",
        fontsize=12, fontweight="bold", color=STYLE["text"], y=0.98,
    )

    _plot_roi_vs_price(ax1, asset, params, entry_price)
    _plot_scenarios_bar(ax2, asset, entry_price)
    _plot_market_sensitivity(ax3, asset, params, entry_price)
    _plot_monte_carlo(ax4, mc_result or SensitivityAnalyzer.monte_carlo(asset, params, entry_price))

    _apply_style(fig, [ax1, ax2, ax3, ax4])
    _save_or_show(fig, output_path)


def _plot_roi_vs_price(ax, asset: Asset, params: ScenarioParams, entry_price: float):
    breakeven = FinancialEngine.breakeven_price(asset, params)
    max_30 = FinancialEngine.max_bid(asset, params, 0.30)
    max_35 = FinancialEngine.max_bid(asset, params, 0.35)

    price_min = asset.catalog_base_uf * 0.85
    price_max = breakeven * 1.05
    prices = np.linspace(price_min, price_max, 200)
    rois = [FinancialEngine.roi_at_price(asset, params, p) * 100 for p in prices]

    ax.plot(prices, rois, color=STYLE["blue"], linewidth=2.0)
    ax.axhline(30, color=STYLE["yellow"], linestyle="--", linewidth=1.2, label="30% ROI objetivo")
    ax.axhline(0, color=STYLE["red"], linestyle="--", linewidth=1.0, alpha=0.7, label="Break-even")
    ax.axvline(entry_price, color=STYLE["green"], linestyle=":", linewidth=1.2, label=f"Precio base: {entry_price:.0f} UF")
    ax.axvline(max_35, color=STYLE["orange"], linestyle=":", linewidth=1.2, label=f"Bid inicial (35%): {max_35:.0f} UF")
    ax.axvline(max_30, color=STYLE["yellow"], linestyle=":", linewidth=1.2, label=f"Max bid (30%): {max_30:.0f} UF")

    ax.fill_between(prices, rois, 30, where=[r >= 30 for r in rois], alpha=0.12, color=STYLE["green"])
    ax.fill_between(prices, rois, 0, where=[r < 0 for r in rois], alpha=0.12, color=STYLE["red"])

    ax.set_xlabel("Precio de entrada (UF)")
    ax.set_ylabel("ROI (%)")
    ax.set_title("ROI vs Precio de Licitación")
    ax.legend(fontsize=7, framealpha=0.3, facecolor=STYLE["panel"], labelcolor=STYLE["text"])
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))


def _plot_scenarios_bar(ax, asset: Asset, entry_price: float):
    scenarios = [BASE, CONSERVATIVE, STRESS]
    names = [s.name for s in scenarios]
    rois = [ScenarioResult(asset=asset, params=s, entry_price=entry_price).roi * 100 for s in scenarios]
    profits = [ScenarioResult(asset=asset, params=s, entry_price=entry_price).profit for s in scenarios]
    colors = [STYLE["green"], STYLE["yellow"], STYLE["red"]]

    bars = ax.bar(names, rois, color=colors, alpha=0.85, edgecolor=STYLE["grid"], linewidth=0.5, width=0.5)
    ax.axhline(30, color=STYLE["yellow"], linestyle="--", linewidth=1.2, label="30% mínimo")
    ax.axhline(0, color=STYLE["red"], linestyle="--", linewidth=0.8, alpha=0.6)

    for bar, roi, profit in zip(bars, rois, profits):
        y = bar.get_height()
        sign = "+" if profit >= 0 else ""
        ax.text(
            bar.get_x() + bar.get_width() / 2.0, y + 1.5,
            f"{roi:.1f}%\n{sign}{profit:.0f} UF",
            ha="center", va="bottom", fontsize=9, fontweight="bold", color=STYLE["text"],
        )

    ax.set_ylabel("ROI (%)")
    ax.set_title("Comparación de Escenarios")
    ax.legend(fontsize=7, framealpha=0.3, facecolor=STYLE["panel"], labelcolor=STYLE["text"])
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))


def _plot_market_sensitivity(ax, asset: Asset, params: ScenarioParams, entry_price: float):
    data = SensitivityAnalyzer.market_price_sweep(asset, params, entry_price, steps=31)
    pct_chg = [d["market_pct_change"] * 100 for d in data]
    rois = [d["roi"] * 100 for d in data]

    ax.plot(pct_chg, rois, color=STYLE["blue"], linewidth=2.0)
    ax.axhline(30, color=STYLE["yellow"], linestyle="--", linewidth=1.2, label="30% ROI")
    ax.axhline(0, color=STYLE["red"], linestyle="--", linewidth=1.0, alpha=0.7, label="Break-even")
    ax.axvline(0, color=STYLE["grid"], linestyle="-", linewidth=0.8, alpha=0.5)

    ax.fill_between(pct_chg, rois, 30, where=[r >= 30 for r in rois], alpha=0.12, color=STYLE["green"])
    ax.fill_between(pct_chg, rois, 0, where=[r < 0 for r in rois], alpha=0.12, color=STYLE["red"])

    sigma = asset.comparable_uncertainty_std * 100
    ax.axvspan(-sigma, sigma, alpha=0.06, color=STYLE["purple"], label=f"±1σ comps ({sigma:.0f}%)")

    ax.set_xlabel("Variación UF/m² mercado (%)")
    ax.set_ylabel("ROI (%)")
    ax.set_title(f"Sensibilidad al Precio de Mercado\nBase: {asset.market_uf_per_m2_raw:.1f} UF/m² | {asset.n_comparables} comparables")
    ax.legend(fontsize=7, framealpha=0.3, facecolor=STYLE["panel"], labelcolor=STYLE["text"])
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))


def _plot_monte_carlo(ax, mc: MonteCarloResult):
    rois_pct = mc.raw_rois * 100

    ax.hist(rois_pct, bins=60, color=STYLE["blue"], alpha=0.65, edgecolor="none", density=True)
    ax.axvline(mc.mean_roi * 100, color=STYLE["blue"], linewidth=2.0, label=f"Media: {mc.mean_roi*100:.1f}%")
    ax.axvline(mc.p5_roi * 100, color=STYLE["red"], linestyle="--", linewidth=1.5, label=f"P5: {mc.p5_roi*100:.1f}%")
    ax.axvline(mc.p95_roi * 100, color=STYLE["green"], linestyle="--", linewidth=1.5, label=f"P95: {mc.p95_roi*100:.1f}%")
    ax.axvline(30, color=STYLE["yellow"], linestyle=":", linewidth=1.5, label="30% ROI")
    ax.axvline(0, color=STYLE["red"], linestyle=":", linewidth=1.0, alpha=0.5)

    ax.set_xlabel("ROI (%)")
    ax.set_ylabel("Densidad")
    ax.set_title(
        f"Monte Carlo — {mc.n_simulations:,} simulaciones\n"
        f"P(>30%): {mc.prob_above_30pct*100:.1f}%  |  P(pérdida): {mc.prob_loss*100:.1f}%  |  P(>20%): {mc.prob_above_20pct*100:.1f}%"
    )
    ax.legend(fontsize=7, framealpha=0.3, facecolor=STYLE["panel"], labelcolor=STYLE["text"])
    ax.yaxis.set_visible(False)


# ---------------------------------------------------------------------------
# Portfolio comparison (1x3)
# ---------------------------------------------------------------------------

def plot_portfolio(
    assets: List[Asset],
    params: ScenarioParams,
    target_roi: float = 0.30,
    output_path: Optional[str] = None,
):
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.patch.set_facecolor(STYLE["bg"])
    fig.suptitle("Comparación de Portafolio — Tier A", fontsize=14, fontweight="bold", color=STYLE["text"])

    labels = [f"#{a.id}\n{a.city[:7]}\n{a.bedrooms}d/{a.bathrooms}b" for a in assets]
    x = np.arange(len(assets))
    w = 0.35

    # --- ROI por escenario ---
    base_rois    = [ScenarioResult(asset=a, params=BASE, entry_price=a.catalog_base_uf).roi * 100 for a in assets]
    conserv_rois = [ScenarioResult(asset=a, params=CONSERVATIVE, entry_price=a.catalog_base_uf).roi * 100 for a in assets]
    stress_rois  = [ScenarioResult(asset=a, params=STRESS, entry_price=a.catalog_base_uf).roi * 100 for a in assets]

    axes[0].bar(x - w, base_rois, w, label="Base", color=STYLE["green"], alpha=0.8)
    axes[0].bar(x, conserv_rois, w, label="Conservador", color=STYLE["yellow"], alpha=0.8)
    axes[0].bar(x + w, stress_rois, w, label="Stress", color=STYLE["red"], alpha=0.8)
    axes[0].axhline(30, color=STYLE["yellow"], linestyle="--", linewidth=1.0)
    axes[0].axhline(0, color=STYLE["red"], linestyle="--", linewidth=0.8, alpha=0.6)
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(labels, fontsize=8)
    axes[0].set_ylabel("ROI (%)")
    axes[0].set_title("ROI por Escenario")
    axes[0].legend(fontsize=8, framealpha=0.3, facecolor=STYLE["panel"], labelcolor=STYLE["text"])
    axes[0].yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))

    # --- Max bid vs catálogo ---
    max_bids = [FinancialEngine.max_bid(a, params, target_roi) for a in assets]
    catalogs = [a.catalog_base_uf for a in assets]

    axes[1].bar(x, max_bids, 0.5, label=f"Max bid ({target_roi*100:.0f}% ROI)", color=STYLE["blue"], alpha=0.85)
    axes[1].bar(x, catalogs, 0.5, label="Base catálogo", color=STYLE["purple"], alpha=0.5)
    for xi, (bid, cat) in enumerate(zip(max_bids, catalogs)):
        axes[1].text(xi, bid + 10, f"{bid:.0f} UF", ha="center", fontsize=8, color=STYLE["text"], fontweight="bold")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(labels, fontsize=8)
    axes[1].set_ylabel("UF")
    axes[1].set_title(f"Max Bid vs Base Catálogo\n(supuesto conservador, {target_roi*100:.0f}% ROI objetivo)")
    axes[1].legend(fontsize=8, framealpha=0.3, facecolor=STYLE["panel"], labelcolor=STYLE["text"])

    # --- Matriz riesgo/retorno (confianza vs ROI conservador) ---
    sizes = [a.m2 * 8 for a in assets]
    comp_colors = [
        STYLE["green"] if a.n_comparables >= 8 else STYLE["yellow"] if a.n_comparables >= 5 else STYLE["red"]
        for a in assets
    ]
    for i, a in enumerate(assets):
        axes[2].scatter(a.n_comparables, conserv_rois[i], s=sizes[i], c=comp_colors[i],
                        alpha=0.85, edgecolors=STYLE["grid"], linewidth=0.8, zorder=3)
        axes[2].annotate(
            f"#{a.id}", (a.n_comparables, conserv_rois[i]),
            textcoords="offset points", xytext=(6, 4), fontsize=9,
            color=STYLE["text"], fontweight="bold",
        )
    axes[2].axhline(30, color=STYLE["yellow"], linestyle="--", linewidth=1.0, label="30% ROI mínimo")
    axes[2].axhline(0, color=STYLE["red"], linestyle="--", linewidth=0.8, alpha=0.5)
    axes[2].set_xlabel("N° comparables (confianza estadística)")
    axes[2].set_ylabel("ROI conservador (%)")
    axes[2].set_title("Matriz Riesgo / Retorno\n(tamaño burbuja = m²)")
    axes[2].legend(fontsize=8, framealpha=0.3, facecolor=STYLE["panel"], labelcolor=STYLE["text"])
    axes[2].yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))

    _apply_style(fig, list(axes))
    _save_or_show(fig, output_path)


# ---------------------------------------------------------------------------
# Tornado chart
# ---------------------------------------------------------------------------

def plot_tornado(
    asset: Asset,
    params: ScenarioParams,
    entry_price: float,
    output_path: Optional[str] = None,
):
    rows = SensitivityAnalyzer.tornado(asset, params, entry_price)
    base_roi = rows[0]["base_roi"] * 100

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor(STYLE["bg"])

    labels = [r["label"] for r in rows]
    y_pos = np.arange(len(rows))

    for i, r in enumerate(rows):
        lo = r["roi_low"] * 100
        hi = r["roi_high"] * 100
        ax.barh(i, hi - base_roi, left=base_roi, height=0.55, color=STYLE["green"], alpha=0.85)
        ax.barh(i, lo - base_roi, left=base_roi, height=0.55, color=STYLE["red"], alpha=0.85)
        ax.text(max(hi, base_roi) + 0.5, i, f"+{(hi-base_roi):.1f}pp", va="center", fontsize=8, color=STYLE["green"])
        ax.text(min(lo, base_roi) - 0.5, i, f"{(lo-base_roi):.1f}pp", va="center", ha="right", fontsize=8, color=STYLE["red"])

    ax.axvline(base_roi, color=STYLE["text"], linewidth=1.2, linestyle="--", alpha=0.7)
    ax.axvline(30, color=STYLE["yellow"], linewidth=1.0, linestyle=":", label="30% ROI")
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels)
    ax.set_xlabel("ROI (%)")
    ax.set_title(f"Tornado — Sensibilidad del ROI por Variable\n{asset.name} | Entrada: {entry_price:.0f} UF | ROI base: {base_roi:.1f}%")
    ax.legend(fontsize=8, framealpha=0.3, facecolor=STYLE["panel"], labelcolor=STYLE["text"])
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))

    _apply_style(fig, [ax])
    _save_or_show(fig, output_path)
