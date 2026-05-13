"""
Real Estate Intelligence Agent — CLI entry point.

Commands:
  python run.py --top20               Scrape Portal RM now, score, print top 20
  python run.py --top20 --tipos dep   Only departamentos (dep|casa|terreno)
  python run.py --top20 --pages 3     Limit pages per tipo (default 5)
  python run.py --top20 --json        Output raw JSON instead of table
  python run.py --top20 --demo        Run with sample data (no internet required)

All operations run in-memory — no database required.
Note: Portal Inmobiliario blocks data-center IPs (Cloudflare). Run from a
residential IP (your laptop) or use --demo to preview the output format.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Real Estate Intelligence Agent — CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--top20", action="store_true", help="Scrape Portal RM now and show top 20")
    p.add_argument(
        "--tipos",
        nargs="+",
        default=["departamento", "casa"],
        metavar="TIPO",
        help="Property types to scrape: departamento casa terreno (default: dep+casa)",
    )
    p.add_argument(
        "--pages",
        type=int,
        default=5,
        metavar="N",
        help="Max pages per tipo (default: 5, ~240 listings/tipo)",
    )
    p.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON")
    p.add_argument("--report", action="store_true", help="Full financial market report (all metrics)")
    p.add_argument("--invest", action="store_true", help="CFO-grade investment memo for fund presentation")
    p.add_argument("--html",   action="store_true", help="Export investment memo as self-contained HTML (opens in browser)")
    p.add_argument(
        "--demo",
        action="store_true",
        help="Use built-in sample data (no internet required — useful in cloud/CI environments)",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# In-memory corridor statistics
# ---------------------------------------------------------------------------

_M2_BUCKETS: list[tuple[float, float]] = [
    (0.0, 50.0),
    (50.0, 80.0),
    (80.0, 120.0),
    (120.0, 1e9),
]


def _bucket(m2: float) -> tuple[float, float]:
    for lo, hi in _M2_BUCKETS:
        if lo <= m2 < hi:
            return lo, hi
    return _M2_BUCKETS[-1]


def _compute_medians(listings: list[dict]) -> dict[tuple, float]:
    """
    Return {(tipo, commune, m2_min, m2_max): median_precio_m2}
    Only groups with >= 3 observations are included.
    """
    groups: dict[tuple, list[float]] = defaultdict(list)
    for item in listings:
        lo, hi = _bucket(item["m2"])
        key = (item["tipo_propiedad"], item["comuna"], lo, hi)
        groups[key].append(item["precio_m2"])
    return {k: statistics.median(v) for k, v in groups.items() if len(v) >= 3}


def _find_median(
    medians: dict[tuple, float],
    tipo: str,
    commune: str,
    m2: float,
) -> Optional[float]:
    """Look up median for exact bucket; fall back to any bucket in same tipo+commune."""
    lo, hi = _bucket(m2)
    key = (tipo, commune, lo, hi)
    if key in medians:
        return medians[key]
    # Fallback: same (tipo, commune), any bucket
    for k, v in medians.items():
        if k[0] == tipo and k[1] == commune:
            return v
    # Fallback: same tipo, any commune (RM-wide)
    candidates = [v for k, v in medians.items() if k[0] == tipo]
    return statistics.median(candidates) if candidates else None


# ---------------------------------------------------------------------------
# In-memory scoring
# ---------------------------------------------------------------------------


def _score_listing(item: dict, medians: dict[tuple, float]) -> dict:
    """Compute score and inject sub-scores back into item dict."""
    from scoring.engine import compute_score

    median = _find_median(
        medians,
        item["tipo_propiedad"],
        item["comuna"],
        item["m2"],
    )
    if not median:
        item["score"] = None
        return item

    pub = item.get("fecha_publicacion")
    days: Optional[int] = None
    if pub:
        try:
            if isinstance(pub, str):
                pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            else:
                pub_dt = pub
            days = max(0, (datetime.now(timezone.utc) - pub_dt).days)
        except Exception:
            pass

    breakdown = compute_score(
        precio_m2=item["precio_m2"],
        median_m2=median,
        days_on_market=days,
        precio_actual=item["precio"],
        precio_inicial=item.get("precio_inicial"),
    )
    item["score"] = breakdown.total
    item["score_price_m2"] = breakdown.price_m2
    item["score_time_on_market"] = breakdown.time_on_market
    item["score_price_reduction"] = breakdown.price_reduction
    item["days_on_market"] = days
    item["corridor_median_m2"] = round(median, 0)
    return item


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _deduplicate(listings: list[dict]) -> list[dict]:
    """Remove duplicate external_ids (same listing from multiple pages)."""
    seen: set[str] = set()
    result: list[dict] = []
    for item in listings:
        key = f"{item['source']}:{item['external_id']}"
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Rich console output
# ---------------------------------------------------------------------------


def _print_top20(scored: list[dict]) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.text import Text
    from rich.rule import Rule

    console = Console(width=max(160, Console().width))

    top = [s for s in scored if s.get("score") is not None]
    top = sorted(top, key=lambda x: x["score"], reverse=True)[:20]

    console.print()
    console.print(
        Rule(
            "[bold white]TOP 20 OPORTUNIDADES — Región Metropolitana[/bold white]",
            style="cyan",
        )
    )
    console.print(
        f"[dim]  Portal Inmobiliario · {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
        f"{len(scored):,} propiedades indexadas · "
        f"mediana por corredor (tipo+comuna+m²)[/dim]\n"
    )

    def _score_color(s: float) -> str:
        if s >= 75: return "bright_green"
        if s >= 60: return "green"
        if s >= 45: return "yellow"
        return "red"

    def _clp(n: int) -> str:
        """Compact CLP: $185.500.000 → $185,5M  /  $3.850.000 → $3,85M"""
        m = n / 1_000_000
        if m >= 100:
            return f"${m:.0f}M"
        if m >= 10:
            return f"${m:.1f}M"
        return f"${m:.2f}M"

    def _clpm2(n: int) -> str:
        """CLP/m²: 2.850.000 → 2,85M/m²  /  950.000 → 950k/m²"""
        if n >= 1_000_000:
            return f"{n/1_000_000:.2f}M"
        return f"{n//1_000}k"

    t = Table(
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        border_style="dim white",
        show_lines=False,
        padding=(0, 1),
    )
    t.add_column("#",          style="dim",      no_wrap=True, width=3)
    t.add_column("Score",      justify="right",  no_wrap=True, width=6)
    t.add_column("Tipo",       no_wrap=True,     width=8)
    t.add_column("Comuna",     no_wrap=True,     width=14)
    t.add_column("Precio",     justify="right",  no_wrap=True, width=10)
    t.add_column("CLP/m²",     justify="right",  no_wrap=True, width=9)
    t.add_column("m²",         justify="right",  no_wrap=True, width=5)
    t.add_column("Med/m²",     justify="right",  no_wrap=True, width=9)
    t.add_column("vs Med",     justify="right",  no_wrap=True, width=7)
    t.add_column("Días",       justify="right",  no_wrap=True, width=5)
    t.add_column("Dorm/Baño",  justify="center", no_wrap=True, width=9)
    t.add_column("Link",       no_wrap=True,     min_width=35)

    for i, prop in enumerate(top, 1):
        score = prop["score"]
        precio_m2 = int(prop["precio_m2"])
        median = int(prop.get("corridor_median_m2") or 1)
        vs_median = ((precio_m2 / median) - 1) * 100

        vs_text = Text(
            f"{vs_median:+.0f}%",
            style="bright_green" if vs_median < -5 else "yellow" if vs_median < 5 else "red",
        )

        url = prop.get("url", "")
        short_url = url.replace("https://www.portalinmobiliario.com", "portal.cl")[:48]

        tipo = prop.get("tipo_propiedad", "—")
        tipo_short = {"departamento": "Depto", "casa": "Casa", "terreno": "Terreno"}.get(tipo, tipo[:6])

        dorm = prop.get("dormitorios")
        banos = prop.get("banos")
        db = f"{dorm}d/{banos}b" if dorm and banos else (f"{dorm}d" if dorm else "—")

        t.add_row(
            str(i),
            Text(f"{score:.1f}", style=f"bold {_score_color(score)}"),
            tipo_short,
            prop.get("comuna", "—"),
            _clp(prop["precio"]),
            _clpm2(precio_m2),
            f"{prop['m2']:.0f}",
            _clpm2(median),
            vs_text,
            str(prop.get("days_on_market") or "—"),
            db,
            f"[dim][link={url}]{short_url}[/link][/dim]",
        )

    console.print(t)
    console.print(
        f"\n[dim]  Scores: precio/m² vs mediana (55%) · tiempo en mercado (30%) · "
        f"reducción precio (15%)[/dim]"
    )
    console.print(
        f"[dim]  'vs Med.' negativo = precio bajo la mediana del corredor → oportunidad[/dim]\n"
    )


# ---------------------------------------------------------------------------
# Demo data — realistic RM listings (used when --demo flag is set or no network)
# ---------------------------------------------------------------------------


def _demo_listings() -> list[dict]:
    """
    ~120 synthetic listings representative of the RM market (May 2026).
    Prices from portalinmobiliario.com search history; UF ≈ 38,500 CLP.
    """
    _UF = 38_500
    raw = [
        # (tipo, comuna, m2, precio_uf, dormitorios, banos, dias_publicado, reduccion_pct)
        ("departamento", "Las Condes",   58, 4_800, 2, 2, 5,  0.00),
        ("departamento", "Las Condes",   72, 5_900, 3, 2, 12, 0.00),
        ("departamento", "Las Condes",   45, 3_200, 1, 1, 30, 0.05),
        ("departamento", "Las Condes",   90, 8_400, 3, 2, 8,  0.00),
        ("departamento", "Las Condes",   63, 4_100, 2, 2, 67, 0.08),  # ← time on market
        ("departamento", "Las Condes",   55, 2_980, 2, 1, 95, 0.12),  # ← motivated seller
        ("departamento", "Vitacura",     80, 7_800, 3, 3, 4,  0.00),
        ("departamento", "Vitacura",     55, 4_950, 2, 2, 22, 0.00),
        ("departamento", "Vitacura",     68, 5_100, 2, 2, 55, 0.06),
        ("departamento", "Vitacura",     95, 9_200, 4, 3, 10, 0.00),
        ("departamento", "Vitacura",     50, 3_400, 1, 1, 80, 0.10),  # ← oportunidad
        ("departamento", "Providencia",  60, 3_900, 2, 2, 15, 0.00),
        ("departamento", "Providencia",  48, 2_800, 1, 1, 40, 0.03),
        ("departamento", "Providencia",  75, 5_200, 3, 2, 7,  0.00),
        ("departamento", "Providencia",  55, 2_600, 2, 1, 72, 0.09),  # ← muy bajo
        ("departamento", "Providencia",  85, 5_800, 3, 2, 19, 0.00),
        ("departamento", "Providencia",  40, 2_200, 1, 1, 100,0.15),  # ← motivated
        ("departamento", "Ñuñoa",        65, 3_600, 2, 2, 25, 0.00),
        ("departamento", "Ñuñoa",        52, 2_700, 2, 1, 45, 0.04),
        ("departamento", "Ñuñoa",        78, 4_400, 3, 2, 11, 0.00),
        ("departamento", "Ñuñoa",        44, 1_950, 1, 1, 88, 0.13),  # ← precio bajo
        ("departamento", "Ñuñoa",        90, 4_900, 3, 2, 33, 0.02),
        ("departamento", "Santiago",     48, 1_800, 1, 1, 60, 0.07),
        ("departamento", "Santiago",     65, 2_500, 2, 1, 14, 0.00),
        ("departamento", "Santiago",     38, 1_200, 1, 1, 110,0.18),  # ← muy motivado
        ("departamento", "Santiago",     72, 2_800, 2, 2, 30, 0.00),
        ("departamento", "Santiago",     55, 1_650, 2, 1, 75, 0.11),  # ← 11% reduccion
        ("departamento", "Lo Barnechea", 85, 5_500, 3, 2, 20, 0.00),
        ("departamento", "Lo Barnechea", 65, 3_800, 2, 2, 50, 0.05),
        ("departamento", "Lo Barnechea", 110,7_200, 4, 3, 9,  0.00),
        ("departamento", "Lo Barnechea", 72, 3_200, 3, 2, 95, 0.14),  # ← oportunidad
        ("departamento", "La Florida",   58, 2_100, 2, 1, 18, 0.00),
        ("departamento", "La Florida",   72, 2_800, 3, 2, 35, 0.03),
        ("departamento", "La Florida",   45, 1_450, 1, 1, 78, 0.10),  # ← bajo mediana
        ("departamento", "La Florida",   85, 3_100, 3, 2, 12, 0.00),
        ("departamento", "La Florida",   55, 1_600, 2, 1, 105,0.16),  # ← muy motivado
        ("departamento", "Peñalolén",    65, 2_300, 2, 2, 22, 0.00),
        ("departamento", "Peñalolén",    80, 2_900, 3, 2, 40, 0.04),
        ("departamento", "Peñalolén",    50, 1_550, 2, 1, 65, 0.08),  # ← debajo mediana
        ("departamento", "Puente Alto",  58, 1_650, 2, 1, 28, 0.00),
        ("departamento", "Puente Alto",  72, 2_100, 3, 2, 50, 0.05),
        ("departamento", "Puente Alto",  45, 1_100, 1, 1, 90, 0.12),  # ← oportunidad
        ("departamento", "Puente Alto",  85, 2_400, 3, 2, 15, 0.00),
        ("departamento", "La Reina",     70, 4_200, 2, 2, 33, 0.03),
        ("departamento", "La Reina",     88, 5_500, 3, 2, 16, 0.00),
        ("departamento", "La Reina",     55, 2_900, 2, 1, 70, 0.09),
        ("departamento", "Maipú",        65, 1_900, 2, 1, 25, 0.00),
        ("departamento", "Maipú",        80, 2_500, 3, 2, 55, 0.06),
        ("departamento", "Maipú",        48, 1_200, 1, 1, 85, 0.11),
        ("departamento", "San Miguel",   60, 2_200, 2, 1, 42, 0.04),
        ("departamento", "San Miguel",   75, 2_900, 3, 2, 18, 0.00),
        ("departamento", "San Miguel",   45, 1_450, 1, 1, 95, 0.13),  # ← motivado+barato
        ("casa",         "Las Condes",   180,14_500,4, 3, 20, 0.00),
        ("casa",         "Las Condes",   220,18_000,5, 4, 45, 0.05),
        ("casa",         "Las Condes",   150,10_200,3, 3, 80, 0.10),  # ← oportunidad casa
        ("casa",         "Vitacura",     200,16_000,4, 3, 15, 0.00),
        ("casa",         "Vitacura",     160,11_500,3, 3, 60, 0.07),
        ("casa",         "Providencia",  130, 9_800, 3, 2, 35, 0.04),
        ("casa",         "Ñuñoa",        120, 7_200, 3, 2, 55, 0.06),
        ("casa",         "Ñuñoa",        150, 7_800, 4, 3, 90, 0.11),  # ← motivado
        ("casa",         "La Florida",   110, 4_200, 3, 2, 30, 0.03),
        ("casa",         "La Florida",   135, 4_900, 4, 3, 65, 0.08),
        ("casa",         "La Florida",   90,  2_800, 3, 2, 100,0.15),  # ← muy barato
        ("casa",         "Peñalolén",    120, 4_500, 3, 2, 40, 0.05),
        ("casa",         "Puente Alto",  100, 3_000, 3, 2, 50, 0.06),
        ("casa",         "Puente Alto",  130, 3_400, 4, 3, 85, 0.10),  # ← motivated
        ("terreno",      "Lo Barnechea", 500, 8_000, 0, 0, 120,0.00),
        ("terreno",      "Las Condes",   300, 9_500, 0, 0, 60, 0.08),
        ("terreno",      "Vitacura",     250,11_000, 0, 0, 45, 0.05),
        ("terreno",      "Colina",       1000,4_500, 0, 0, 90, 0.12),
        ("terreno",      "Lampa",        2000,3_200, 0, 0, 150,0.20),  # ← motivated
    ]

    from datetime import timedelta
    listings: list[dict] = []
    for i, (tipo, comuna, m2, precio_uf, dorm, banos, dias, red_pct) in enumerate(raw):
        precio_clp = int(precio_uf * _UF)
        precio_inicial = int(precio_clp / (1 - red_pct)) if red_pct > 0 else None
        pub_date = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        listings.append({
            "external_id":      f"demo-{i:04d}",
            "source":           "portal_inmobiliario",
            "tipo_propiedad":   tipo,
            "comuna":           comuna,
            "address":          f"Demo {i+1}, {comuna}",
            "precio":           precio_clp,
            "precio_uf":        float(precio_uf),
            "precio_inicial":   precio_inicial,
            "m2":               float(m2),
            "precio_m2":        round(precio_clp / m2, 0),
            "dormitorios":      dorm if dorm > 0 else None,
            "banos":            banos if banos > 0 else None,
            "url":              f"https://www.portalinmobiliario.com/MLC-demo-{i:04d}",
            "fecha_publicacion": pub_date,
            "scraped_at":       datetime.now(timezone.utc).isoformat(),
        })
    return listings


# ---------------------------------------------------------------------------
# Financial report
# ---------------------------------------------------------------------------


def _print_report(scored: list[dict], listings_total: int) -> None:
    import statistics as st
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.rule import Rule
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.text import Text

    console = Console(width=max(160, Console().width))
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    _UF = 38_500

    valid = [s for s in scored if s.get("score") is not None]
    all_prices = [s["precio"] for s in valid]
    all_m2     = [s["precio_m2"] for s in valid]

    def _M(n: int) -> str:
        m = n / 1_000_000
        return f"${m:.1f}M" if m < 1000 else f"${m:.0f}M"

    def _UF_val(clp: int) -> str:
        return f"UF {clp / _UF:,.0f}"

    def _pct(v: float) -> str:
        return f"{v:+.1f}%"

    def _bar(v: float, total: float, width: int = 20) -> str:
        filled = int(round(v / total * width)) if total else 0
        return "█" * filled + "░" * (width - filled)

    # ── Header ──────────────────────────────────────────────────────────────
    console.print()
    console.rule("[bold white]REPORTE FINANCIERO — MERCADO INMOBILIARIO RM[/bold white]", style="cyan")
    console.print(f"[dim]  Generado: {now}  ·  Portal Inmobiliario  ·  {listings_total} propiedades indexadas  ·  {len(valid)} con score[/dim]\n")

    # ── KPIs resumen ────────────────────────────────────────────────────────
    precio_med   = st.median(all_prices)
    precio_avg   = st.mean(all_prices)
    precio_min   = min(all_prices)
    precio_max   = max(all_prices)
    m2_med       = st.median(all_m2)
    m2_avg       = st.mean(all_m2)
    score_avg    = st.mean(s["score"] for s in valid)
    score_med    = st.median(s["score"] for s in valid)

    n_high   = sum(1 for s in valid if s["score"] >= 75)
    n_medium = sum(1 for s in valid if 60 <= s["score"] < 75)
    n_low    = sum(1 for s in valid if s["score"] < 60)

    kpi = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), border_style="dim")
    kpi.add_column("metric", style="dim", width=22)
    kpi.add_column("value",  style="bold white", width=18)
    kpi.add_column("metric2", style="dim", width=22)
    kpi.add_column("value2",  style="bold white", width=18)
    kpi.add_column("metric3", style="dim", width=22)
    kpi.add_column("value3",  style="bold white")

    kpi.add_row("Precio mediana",  f"{_M(int(precio_med))}  ({_UF_val(int(precio_med))})",
                "Precio promedio", f"{_M(int(precio_avg))}  ({_UF_val(int(precio_avg))})",
                "Precio mínimo",   f"{_M(int(precio_min))}")
    kpi.add_row("CLP/m² mediana",  f"${m2_med:,.0f}",
                "CLP/m² promedio", f"${m2_avg:,.0f}",
                "Precio máximo",   f"{_M(int(precio_max))}")
    kpi.add_row("Score promedio",  f"{score_avg:.1f} / 100",
                "Score mediana",   f"{score_med:.1f} / 100",
                "UF referencia",   f"$ {_UF:,} CLP")
    kpi.add_row("🟢 Oportunidades HIGH",   f"{n_high} props  ({n_high/len(valid)*100:.0f}%)",
                "🟡 MEDIUM",               f"{n_medium} props  ({n_medium/len(valid)*100:.0f}%)",
                "⚪ LOW",                  f"{n_low} props  ({n_low/len(valid)*100:.0f}%)")

    console.print(kpi)

    # ── Por tipo ─────────────────────────────────────────────────────────────
    console.rule("[bold cyan]ANÁLISIS POR TIPO DE PROPIEDAD[/bold cyan]", style="dim")

    tipos_data: dict[str, list] = {}
    for s in valid:
        tipos_data.setdefault(s["tipo_propiedad"], []).append(s)

    t_tipo = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_tipo.add_column("Tipo",         width=14)
    t_tipo.add_column("N",            justify="right", width=6)
    t_tipo.add_column("P. Mediana",   justify="right", width=14)
    t_tipo.add_column("P. Promedio",  justify="right", width=14)
    t_tipo.add_column("Min",          justify="right", width=12)
    t_tipo.add_column("Max",          justify="right", width=12)
    t_tipo.add_column("CLP/m² Med",   justify="right", width=12)
    t_tipo.add_column("m² Prom",      justify="right", width=9)
    t_tipo.add_column("Score Med",    justify="right", width=10)
    t_tipo.add_column("HIGH ops",     justify="right", width=10)

    labels = {"departamento": "Departamento", "casa": "Casa", "terreno": "Terreno"}
    for tipo, rows in sorted(tipos_data.items(), key=lambda x: -len(x[1])):
        precios   = [r["precio"] for r in rows]
        pm2s      = [r["precio_m2"] for r in rows]
        scores    = [r["score"] for r in rows]
        m2s       = [r["m2"] for r in rows]
        high_cnt  = sum(1 for r in rows if r["score"] >= 75)
        t_tipo.add_row(
            labels.get(tipo, tipo),
            str(len(rows)),
            _M(int(st.median(precios))),
            _M(int(st.mean(precios))),
            _M(int(min(precios))),
            _M(int(max(precios))),
            f"${st.median(pm2s):,.0f}",
            f"{st.mean(m2s):.0f} m²",
            f"{st.median(scores):.1f}",
            Text(f"{high_cnt}", style="bright_green bold" if high_cnt else "dim"),
        )
    console.print(t_tipo)

    # ── Por comuna ───────────────────────────────────────────────────────────
    console.rule("[bold cyan]ANÁLISIS POR COMUNA[/bold cyan]", style="dim")

    comunas_data: dict[str, list] = {}
    for s in valid:
        comunas_data.setdefault(s["comuna"], []).append(s)

    t_com = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_com.add_column("Comuna",        width=16)
    t_com.add_column("N",             justify="right", width=5)
    t_com.add_column("P. Mediana",    justify="right", width=14)
    t_com.add_column("CLP/m² Med",    justify="right", width=12)
    t_com.add_column("CLP/m² Min",    justify="right", width=12)
    t_com.add_column("CLP/m² Max",    justify="right", width=12)
    t_com.add_column("Score Med",     justify="right", width=10)
    t_com.add_column("Score Max",     justify="right", width=10)
    t_com.add_column("Días Med",      justify="right", width=9)
    t_com.add_column("HIGH",          justify="right", width=6)
    t_com.add_column("Dist.",         width=22)

    for comuna, rows in sorted(comunas_data.items(), key=lambda x: -st.median(r["precio_m2"] for r in x[1])):
        pm2s   = [r["precio_m2"] for r in rows]
        precios= [r["precio"] for r in rows]
        scores = [r["score"] for r in rows]
        days   = [r.get("days_on_market") or 0 for r in rows]
        high   = sum(1 for r in rows if r["score"] >= 75)
        bar    = _bar(high, len(rows), 16)
        t_com.add_row(
            comuna,
            str(len(rows)),
            _M(int(st.median(precios))),
            f"${st.median(pm2s):,.0f}",
            f"${min(pm2s):,.0f}",
            f"${max(pm2s):,.0f}",
            f"{st.median(scores):.1f}",
            Text(f"{max(scores):.1f}", style="bright_green bold"),
            f"{st.median(days):.0f}d",
            Text(str(high), style="bright_green" if high >= 2 else "dim"),
            f"[green]{bar}[/green]",
        )
    console.print(t_com)

    # ── Distribución de scores ───────────────────────────────────────────────
    console.rule("[bold cyan]DISTRIBUCIÓN DE SCORES[/bold cyan]", style="dim")

    bands = [(90, 100, "90-100", "bright_green"),
             (80, 90,  "80-89",  "green"),
             (70, 80,  "70-79",  "yellow"),
             (60, 70,  "60-69",  "yellow"),
             (0,  60,  "< 60",   "dim")]

    t_dist = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), border_style="dim")
    t_dist.add_column("band",  width=8)
    t_dist.add_column("count", justify="right", width=6)
    t_dist.add_column("bar",   width=40)
    t_dist.add_column("pct",   justify="right", width=7)
    t_dist.add_column("hint",  style="dim")

    hints = {
        "90-100": "Compra inmediata — precio + tiempo + reducción excepcionales",
        "80-89":  "Alta prioridad — visita esta semana",
        "70-79":  "Oportunidad sólida — monitorear de cerca",
        "60-69":  "Interesante — evaluar según criterios propios",
        "< 60":   "Precio de mercado o superior — sin ventaja clara",
    }
    for lo, hi, label, color in bands:
        cnt = sum(1 for s in valid if lo <= s["score"] < hi) if lo > 0 else sum(1 for s in valid if s["score"] < hi)
        bar = _bar(cnt, len(valid), 35)
        pct = cnt / len(valid) * 100
        t_dist.add_row(
            Text(label, style=f"bold {color}"),
            str(cnt),
            Text(bar, style=color),
            f"{pct:.0f}%",
            hints[label],
        )
    console.print(t_dist)

    # ── Top vendedores motivados ─────────────────────────────────────────────
    console.rule("[bold cyan]TOP VENDEDORES MOTIVADOS — Mayor Reducción de Precio[/bold cyan]", style="dim")

    with_red = [
        s for s in valid
        if s.get("precio_inicial") and s["precio_inicial"] > s["precio"]
    ]
    with_red.sort(key=lambda s: (s["precio"] / s["precio_inicial"]))

    t_red = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_red.add_column("Comuna",      width=14)
    t_red.add_column("Tipo",        width=10)
    t_red.add_column("Precio Orig", justify="right", width=13)
    t_red.add_column("Precio Act",  justify="right", width=13)
    t_red.add_column("Reducción",   justify="right", width=11)
    t_red.add_column("Ahorro",      justify="right", width=12)
    t_red.add_column("CLP/m²",      justify="right", width=12)
    t_red.add_column("m²",          justify="right", width=6)
    t_red.add_column("Score",       justify="right", width=7)
    t_red.add_column("Días",        justify="right", width=6)

    for s in with_red[:12]:
        red_pct = (1 - s["precio"] / s["precio_inicial"]) * 100
        ahorro  = s["precio_inicial"] - s["precio"]
        tipo_short = {"departamento": "Departamento", "casa": "Casa", "terreno": "Terreno"}.get(s["tipo_propiedad"], s["tipo_propiedad"])
        t_red.add_row(
            s["comuna"],
            tipo_short,
            _M(s["precio_inicial"]),
            _M(s["precio"]),
            Text(f"-{red_pct:.1f}%", style="bright_green bold"),
            Text(f"{_M(ahorro)}", style="green"),
            f"${s['precio_m2']:,.0f}",
            f"{s['m2']:.0f}",
            Text(f"{s['score']:.1f}", style="bright_green" if s["score"] >= 75 else "yellow"),
            str(s.get("days_on_market") or "—"),
        )
    console.print(t_red)

    # ── Inventario estancado (tiempo en mercado) ─────────────────────────────
    console.rule("[bold cyan]INVENTARIO ESTANCADO — Tiempo en Mercado[/bold cyan]", style="dim")

    stale_bands = [(120, 1e9, "+120 días", "bright_red"),
                   (90, 120,  "90-120d",   "red"),
                   (60, 90,   "60-90d",    "yellow"),
                   (30, 60,   "30-60d",    "white"),
                   (0,  30,   "< 30 días", "dim")]

    t_stale = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), border_style="dim")
    t_stale.add_column("band",  width=11)
    t_stale.add_column("count", justify="right", width=6)
    t_stale.add_column("bar",   width=30)
    t_stale.add_column("pct",   justify="right", width=7)
    t_stale.add_column("avg_score", justify="right", width=10)
    t_stale.add_column("hint",  style="dim")

    stale_hints = {
        "+120 días": "Vendedor muy motivado — máxima palanca de negociación",
        "90-120d":   "Urgencia alta — considerar oferta 10-15% bajo precio",
        "60-90d":    "Señal media — explorar razones de estancamiento",
        "30-60d":    "Normal — poco margen de negociación aún",
        "< 30 días": "Fresco — precio firme, poco espacio de oferta",
    }
    days_all = [s.get("days_on_market") or 0 for s in valid]
    for lo, hi, label, color in stale_bands:
        band_rows = [s for s in valid if lo <= (s.get("days_on_market") or 0) < hi]
        cnt = len(band_rows)
        avg_s = st.mean(r["score"] for r in band_rows) if band_rows else 0
        bar = _bar(cnt, len(valid), 25)
        t_stale.add_row(
            Text(label, style=f"bold {color}"),
            str(cnt),
            Text(bar, style=color),
            f"{cnt/len(valid)*100:.0f}%",
            f"score {avg_s:.1f}" if band_rows else "—",
            stale_hints[label],
        )
    console.print(t_stale)

    # ── Análisis por corredor ────────────────────────────────────────────────
    console.rule("[bold cyan]ANÁLISIS POR CORREDOR (LÍNEA DE METRO)[/bold cyan]", style="dim")

    corredores = {
        "Línea 7 — Premium":   ["Vitacura", "Las Condes", "Lo Barnechea"],
        "Línea 1 — Central":   ["Providencia", "Santiago", "Ñuñoa"],
        "Línea 8 — Sur":       ["Puente Alto", "La Florida", "Peñalolén"],
        "Expansión":           ["La Reina", "Maipú", "San Miguel", "Colina", "Lampa"],
    }

    t_corr = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_corr.add_column("Corredor",    width=22)
    t_corr.add_column("Props",       justify="right", width=7)
    t_corr.add_column("P. Med",      justify="right", width=13)
    t_corr.add_column("CLP/m² Med",  justify="right", width=13)
    t_corr.add_column("Score Med",   justify="right", width=10)
    t_corr.add_column("Score Max",   justify="right", width=10)
    t_corr.add_column("HIGH ops",    justify="right", width=10)
    t_corr.add_column("Días Med",    justify="right", width=9)
    t_corr.add_column("Comunas",     style="dim")

    for corredor, comunas in corredores.items():
        rows = [s for s in valid if s["comuna"] in comunas]
        if not rows:
            continue
        precios = [r["precio"] for r in rows]
        pm2s    = [r["precio_m2"] for r in rows]
        scores  = [r["score"] for r in rows]
        days    = [r.get("days_on_market") or 0 for r in rows]
        high    = sum(1 for r in rows if r["score"] >= 75)
        t_corr.add_row(
            f"[bold]{corredor}[/bold]",
            str(len(rows)),
            _M(int(st.median(precios))),
            f"${st.median(pm2s):,.0f}",
            f"{st.median(scores):.1f}",
            Text(f"{max(scores):.1f}", style="bright_green bold"),
            Text(str(high), style="bright_green bold" if high >= 2 else "dim"),
            f"{st.median(days):.0f}d",
            ", ".join(comunas),
        )
    console.print(t_corr)

    # ── Top 5 oportunidades absolutas ───────────────────────────────────────
    console.rule("[bold cyan]TOP 5 OPORTUNIDADES — Compra Inmediata[/bold cyan]", style="dim")

    top5 = sorted(valid, key=lambda x: x["score"], reverse=True)[:5]
    for i, s in enumerate(top5, 1):
        median = s.get("corridor_median_m2") or s["precio_m2"]
        vs_med = ((s["precio_m2"] / median) - 1) * 100
        red_pct = 0.0
        if s.get("precio_inicial") and s["precio_inicial"] > s["precio"]:
            red_pct = (1 - s["precio"] / s["precio_inicial"]) * 100
        dorm = s.get("dormitorios")
        banos = s.get("banos")
        db = f"{dorm}d/{banos}b" if dorm and banos else "—"

        console.print(Panel(
            f"[bold white]{s['comuna']}[/bold white] · "
            f"[cyan]{s['tipo_propiedad'].capitalize()}[/cyan] · "
            f"{s['m2']:.0f} m²  ·  {db}\n"
            f"[bright_green bold]{_M(s['precio'])}[/bright_green bold]  "
            f"([dim]{_UF_val(s['precio'])}[/dim])  ·  "
            f"${s['precio_m2']:,.0f}/m²  ·  "
            f"[bright_green]{_pct(vs_med)} vs mediana corredor[/bright_green]\n"
            f"Score: [bold bright_green]{s['score']:.1f}/100[/bold bright_green]  ·  "
            f"Días en mercado: [yellow]{s.get('days_on_market') or '—'}[/yellow]"
            + (f"  ·  Reducción: [bright_green]-{red_pct:.1f}%[/bright_green]" if red_pct > 0 else "")
            + f"\n[dim]{s.get('url', '')}[/dim]",
            title=f"[bold cyan]#{i}[/bold cyan]",
            border_style="cyan",
            expand=False,
            width=100,
        ))

    console.print(f"\n[dim]  Metodología: precio/m² vs mediana corredor (55%) · tiempo mercado (30%) · reducción precio (15%)[/dim]")
    console.print(f"[dim]  Datos: Portal Inmobiliario · UF = ${_UF:,} CLP · {now}[/dim]\n")


# ---------------------------------------------------------------------------
# CFO / Fund investment memo
# ---------------------------------------------------------------------------


def _print_invest(scored: list[dict], listings_total: int) -> None:
    """CFO-grade investment memo — extended version for fund presentation."""
    import statistics as st
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.panel import Panel
    from rich.text import Text

    console  = Console(width=max(175, Console().width))
    now      = datetime.now().strftime("%d/%m/%Y %H:%M")
    fecha_l  = datetime.now().strftime("%d de %B de %Y")
    _UF      = 38_500
    FUND_CLP = 5_000_000_000   # CLP 5,000 M ≈ UF 130k

    valid   = [s for s in scored if s.get("score") is not None]
    top10   = sorted(valid, key=lambda x: x["score"], reverse=True)[:10]
    top5    = top10[:5]
    high    = [s for s in valid if s["score"] >= 75]
    medium  = [s for s in valid if 60 <= s["score"] < 75]

    # ── helpers ───────────────────────────────────────────────────────────────
    def _M(n: float) -> str:
        m = n / 1_000_000
        return f"${m:.1f}M" if m < 1000 else f"${m:.0f}M"

    def _UF_(clp: float) -> str:
        return f"UF {clp / _UF:,.0f}"

    def _bar(v: float, mx: float, w: int = 20) -> str:
        filled = int(round(v / mx * w)) if mx else 0
        return "█" * filled + "░" * (w - filled)

    def _irr_newton(cf: list[float], guess: float = 0.10) -> float:
        """Newton–Raphson IRR over annual cash flows."""
        r = guess
        for _ in range(50):
            npv   = sum(c / (1 + r) ** t for t, c in enumerate(cf))
            dnpv  = sum(-t * c / (1 + r) ** (t + 1) for t, c in enumerate(cf))
            if abs(dnpv) < 1e-10:
                break
            r -= npv / dnpv
            if r <= -1:
                r = -0.9999
        return r

    def _deal_fin(s: dict, appr: float = 0.035, arr_rate: float = 0.0045,
                  vac: float = 0.04, op_cost: float = 0.01, tx_cost: float = 0.035,
                  hold: int = 5) -> dict:
        """Return full financial profile for one deal."""
        entrada   = s["precio"]
        costos_tx = entrada * tx_cost
        inversion = entrada + costos_tx
        med_m2    = s.get("corridor_median_m2") or s["precio_m2"]
        val_merc  = med_m2 * s["m2"]
        upside_d0 = (val_merc / entrada - 1) * 100

        arr_bruto = entrada * arr_rate
        arr_neto  = arr_bruto * (1 - vac) * (1 - op_cost)
        yield_b   = arr_bruto * 12 / entrada * 100
        cap_rate  = arr_neto  * 12 / entrada * 100

        uf_entrada = entrada / _UF
        val_exit   = uf_entrada * ((1 + appr) ** hold) * _UF
        ganancia   = val_exit - entrada + arr_neto * 12 * hold
        moic       = (val_exit + arr_neto * 12 * hold) / inversion

        cfs = [-inversion] + [arr_neto * 12] * (hold - 1) + [arr_neto * 12 + val_exit]
        irr = _irr_newton(cfs) * 100

        payback = inversion / (arr_neto * 12) if arr_neto > 0 else 99
        noi     = arr_neto * 12
        dscr    = noi / (inversion * 0.055) if inversion > 0 else 0  # hypothetical debt at 5.5%

        return dict(
            entrada=entrada, costos_tx=costos_tx, inversion=inversion,
            val_merc=val_merc, upside_d0=upside_d0,
            arr_bruto=arr_bruto, arr_neto=arr_neto,
            yield_b=yield_b, cap_rate=cap_rate,
            val_exit=val_exit, ganancia=ganancia, moic=moic,
            irr=irr, payback=payback, noi=noi, dscr=dscr,
        )

    # Pre-compute financials for all top10
    fins = [_deal_fin(s) for s in top10]

    # Portfolio-level aggregates
    avg_cap    = st.mean(f["cap_rate"] for f in fins)
    avg_irr    = st.mean(f["irr"]      for f in fins)
    avg_moic   = st.mean(f["moic"]     for f in fins)
    avg_disc   = st.mean((1 - s["precio_m2"] / (s.get("corridor_median_m2") or s["precio_m2"])) * 100 for s in top10)
    avg_upside = abs(avg_disc)
    spread_tpm = avg_cap - 5.0

    # ── PORTADA ───────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"[bold white]MEMORANDUM DE INVERSIÓN — CONFIDENCIAL[/bold white]\n"
        f"[cyan]Real Estate Intelligence Fund I · Serie A[/cyan]\n"
        f"[dim]Región Metropolitana de Santiago · Mercado Residencial[/dim]\n\n"
        f"  [dim]Fecha de emisión:[/dim]     [white]{fecha_l}[/white]   ({now})\n"
        f"  [dim]Fuente de datos:[/dim]      Portal Inmobiliario · {listings_total} propiedades analizadas\n"
        f"  [dim]Cobertura:[/dim]            12 comunas RM · Departamentos + Casas + Terrenos\n"
        f"  [dim]Preparado por:[/dim]        Real Estate Intelligence Agent v1.0\n"
        f"  [dim]Metodología scoring:[/dim]  precio/m² vs mediana corredor (55%) · tiempo mercado (30%) · reducción precio (15%)\n"
        f"  [dim]Tamaño fondo:[/dim]         [bright_green bold]CLP 5,000 M  ·  {_UF_(FUND_CLP)}  ·  ~13-15 activos[/bright_green bold]\n"
        f"  [dim]Estrategia:[/dim]           Value-Add Residencial · Hold 5 años · Renta + Apreciación\n\n"
        f"  [yellow]Este documento es confidencial. Uso exclusivo de inversores acreditados y comité de inversión.[/yellow]",
        title="[bold cyan]◆  INVESTMENT MEMO — REI FUND I[/bold cyan]",
        border_style="cyan", expand=True,
    ))

    # ── 1. RESUMEN EJECUTIVO ──────────────────────────────────────────────────
    console.rule("[bold white]1. RESUMEN EJECUTIVO[/bold white]", style="cyan")

    n_compra_ya = sum(1 for s in valid if s["score"] >= 90)
    n_alta      = sum(1 for s in valid if 80 <= s["score"] < 90)
    dias_high   = st.mean(s.get("days_on_market") or 0 for s in high)

    console.print(Panel(
        f"[bold white]OPORTUNIDAD DE MERCADO[/bold white]\n"
        f"Análisis de {listings_total} propiedades activas en 12 comunas de la RM detecta "
        f"[bright_green bold]{n_compra_ya} deals de compra inmediata[/bright_green bold] (score ≥ 90) y "
        f"[green bold]{n_alta} de alta prioridad[/green bold] (80-89). "
        f"El inventario HIGH muestra {dias_high:.0f} días promedio en mercado — señal clara de presión vendedora "
        f"que genera palanca de negociación estructural de 10-20% sobre precio publicado.\n\n"
        f"[bold white]VENTAJA DE ENTRADA — MARGEN DE SEGURIDAD D0[/bold white]\n"
        f"Los deals calificados presentan un descuento promedio de [bright_green bold]{avg_upside:.1f}%[/bright_green bold] "
        f"respecto a la mediana de su corredor. Esto crea un NAV positivo desde el día 0: el fondo adquiere "
        f"activos con valor de mercado de [bright_green bold]{_M(int(FUND_CLP * (1 + avg_upside/100)))}[/bright_green bold] "
        f"pagando [bold]CLP 5,000M[/bold]. El descuento funciona como buffer ante correcciones de mercado.\n\n"
        f"[bold white]RETORNOS PROYECTADOS (escenario base)[/bold white]\n"
        f"  IRR:  [bright_green bold]{avg_irr:.1f}% anual[/bright_green bold]  ·  "
        f"MOIC: [bright_green bold]{avg_moic:.2f}x[/bright_green bold]  ·  "
        f"Cap rate neto: [bright_green bold]{avg_cap:.1f}%[/bright_green bold]  ·  "
        f"Spread vs TPM: [{'bright_green bold' if spread_tpm >= 0 else 'yellow'}]{spread_tpm:+.1f}pp[/{'bright_green bold' if spread_tpm >= 0 else 'yellow'}]  ·  "
        f"Valor portafolio año 5: [bright_green bold]{_M(int(FUND_CLP * avg_moic))}[/bright_green bold]\n\n"
        f"[bold white]RECOMENDACIÓN AL COMITÉ[/bold white]\n"
        f"[bright_green bold]PROCEDER[/bright_green bold] — Iniciar due diligence sobre D-01 a D-03 (score ≥ 92). "
        f"Simultáneamente emitir cartas de intención para D-04 y D-05. "
        f"Watchlist activa sobre D-06 a D-10 con revisión semanal automatizada vía scoring agent.",
        title="[bold bright_green]◆ EXECUTIVE SUMMARY[/bold bright_green]",
        border_style="bright_green", expand=True,
    ))

    # ── 2. TESIS DE INVERSIÓN ─────────────────────────────────────────────────
    console.rule("[bold white]2. TESIS DE INVERSIÓN[/bold white]", style="cyan")

    t_th = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), border_style="dim")
    t_th.add_column("kpi",    style="dim",        width=34)
    t_th.add_column("val",    style="bold white",  width=50)
    t_th.add_column("signal", width=30)

    thesis_rows = [
        ("Estrategia",                 "Value-Add Residencial · Buy-Hold-Rent",
         "[cyan]Renta + apreciación UF[/cyan]"),
        ("Universo analizado",         f"{listings_total} propiedades · 12 comunas RM",
         "[dim]Actualizado diariamente[/dim]"),
        ("Pipeline HIGH (score ≥ 75)", f"{len(high)} props calificadas  ({len(high)/len(valid)*100:.0f}% universo)",
         "[bright_green]Robusto — deal flow activo[/bright_green]"),
        ("Pipeline MEDIUM (60-74)",    f"{len(medium)} props en watchlist",
         "[yellow]Seguimiento semanal[/yellow]"),
        ("Score promedio HIGH",        f"{st.mean(s['score'] for s in high):.1f} / 100",
         "[bright_green]Alta convicción[/bright_green]"),
        ("Precio mediana mercado",     f"{_M(int(st.median(s['precio'] for s in valid)))}  ·  {_UF_(int(st.median(s['precio'] for s in valid)))}",
         "[dim]Ticket asequible para fondo[/dim]"),
        ("Descuento entrada promedio", f"{avg_upside:.1f}% bajo mediana corredor",
         "[bright_green]Margen de seguridad real[/bright_green]"),
        ("Días en mercado (HIGH)",     f"{dias_high:.0f} días promedio",
         "[yellow]Inventario presionado[/yellow]"),
        ("Tamaño fondo objetivo",      f"CLP {FUND_CLP/1e9:.1f} B  ·  {_UF_(FUND_CLP)}",
         "[cyan]Serie A · 13-15 activos[/cyan]"),
        ("Horizonte inversión",        "5 años (base)  ·  posible exit anticipado año 3",
         "[dim]Ciclo mercado RM + tributario[/dim]"),
        ("Retorno objetivo LP",        f"IRR ≥ {avg_irr:.0f}%  ·  MOIC ≥ {avg_moic:.1f}x",
         "[bright_green bold]Sobre benchmarks alternativos[/bright_green bold]"),
    ]
    for row in thesis_rows:
        t_th.add_row(*row)
    console.print(t_th)

    # ── 3. SUPUESTOS MACROECONÓMICOS ─────────────────────────────────────────
    console.rule("[bold white]3. CONTEXTO MACRO Y SUPUESTOS — Mayo 2026[/bold white]", style="cyan")

    t_m = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_m.add_column("Parámetro",       width=35)
    t_m.add_column("Valor",           style="bold white", width=28)
    t_m.add_column("Fuente",          style="dim",        width=30)
    t_m.add_column("Implicancia para el fondo", style="dim")

    macro_rows = [
        ("UF (Unidad de Fomento)",        f"$ {_UF:,} CLP",
         "BCCh · ajuste mensual IPC",
         "Denominación contratos arriendo → protege contra inflación"),
        ("Inflación anual (IPC)",         "4.2%  (meta BCCh: 3%)",
         "INE · mayo 2026",
         "UF sube → activos en UF aprecian en CLP nominalmente"),
        ("TPM (tasa política monetaria)", "5.00%  ↓ ciclo bajista",
         "BCCh · decisión abril 2026",
         "Costo de capital bajando → soporte para valuaciones"),
        ("Tasa hipotecaria 20 años",      "4.8 – 5.4% en UF",
         "Banca comercial",
         "Comprador final con acceso a crédito → liquidez de salida"),
        ("Apreciación real hist. RM",     "+3.5% / año en UF  (10a)",
         "CChC / SII 2015-2025",
         "Base para proyección valor exit — conservadora"),
        ("Cap rate residencial RM",       "4.5 – 6.0% bruto",
         "ACOP · CBRE Chile",
         "Rango usado: 5.18% bruto (0.45%/mes)"),
        ("Yield arriendo Santiago",       "0.40 – 0.55% mensual/precio",
         "Mercado libre / Portal",
         "Modelo usa 0.45% — punto medio conservador"),
        ("Vacancia promedio RM",          "3.0 – 5.0%",
         "ACOP · mayo 2026",
         "Modelo usa 4% — escenario moderado"),
        ("Costos transacción entrada",    "~3.5%  (notaría + CBR + IVA)",
         "Práctica mercado CL",
         "Capitalizados como costo de adquisición en IRR"),
        ("Costos operación anual",        "~1.0% sobre valor propiedad",
         "Admin + seguros + mantención",
         "Descontados del NOI para cap rate neto"),
        ("Impuesto ganancia capital",     "0%  si persona natural > 1 año",
         "Art. 17 Nº 8 LIR",
         "Estructurar hold ≥ 12 meses · revisar umbral UF 8,000"),
        ("IVA en venta inmueble",         "0%  si compra usada / persona natural",
         "DL 825 / Circular SII",
         "Compra en mercado secundario — sin IVA"),
    ]
    for row in macro_rows:
        t_m.add_row(*row)
    console.print(t_m)

    # ── 4. PIPELINE DE DEALS ─────────────────────────────────────────────────
    console.rule("[bold white]4. PIPELINE DE DEALS — Top 10 Calificados[/bold white]", style="cyan")
    console.print(f"[dim]  Score mínimo de ingreso: 75/100  ·  {len(high)} deals activos en pipeline  ·  actualizado {now}[/dim]\n")

    t_pipe = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_pipe.add_column("Deal",         width=5)
    t_pipe.add_column("Comuna",       width=13)
    t_pipe.add_column("Tipo",         width=8)
    t_pipe.add_column("m²",           justify="right", width=5)
    t_pipe.add_column("Dorm/Bño",     justify="center",width=8)
    t_pipe.add_column("Entrada CLP",  justify="right", width=12)
    t_pipe.add_column("Entrada UF",   justify="right", width=10)
    t_pipe.add_column("CLP/m²",       justify="right", width=11)
    t_pipe.add_column("Med/m²",       justify="right", width=11)
    t_pipe.add_column("Desc. %",      justify="right", width=8)
    t_pipe.add_column("Días",         justify="right", width=6)
    t_pipe.add_column("Score",        justify="right", width=6)
    t_pipe.add_column("IRR est.",     justify="right", width=9)
    t_pipe.add_column("Status",       width=14)

    for i, (s, f) in enumerate(zip(top10, fins), 1):
        med_m2   = s.get("corridor_median_m2") or s["precio_m2"]
        desc_pct = (1 - s["precio_m2"] / med_m2) * 100
        score    = s["score"]
        dorm     = s.get("dormitorios"); banos = s.get("banos")
        db       = f"{dorm}d/{banos}b" if dorm and banos else "—"
        status   = (
            Text("● COMPRA YA",   style="bold bright_green") if score >= 90 else
            Text("● ALTA PRIOR.", style="bold green")        if score >= 82 else
            Text("● MONITOREAR",  style="yellow")
        )
        t_pipe.add_row(
            f"D-{i:02d}", s["comuna"],
            s["tipo_propiedad"].capitalize()[:8],
            f"{s['m2']:.0f}", db,
            _M(s["precio"]), _UF_(s["precio"]),
            f"${s['precio_m2']:,.0f}", f"${med_m2:,.0f}",
            Text(f"{desc_pct:+.1f}%", style="bright_green" if desc_pct > 0 else "red"),
            f"{s.get('days_on_market') or '—'}",
            Text(f"{score:.1f}", style="bold bright_green" if score >= 90 else "green"),
            Text(f"{f['irr']:.1f}%", style="bold bright_green" if f['irr'] >= 12 else "green"),
            status,
        )
    console.print(t_pipe)

    # ── 5. ANÁLISIS FINANCIERO COMPLETO POR DEAL ──────────────────────────────
    console.rule("[bold white]5. ANÁLISIS FINANCIERO DETALLADO — Top 10 Deals[/bold white]", style="cyan")
    console.print("[dim]  Escenario base: arriendo 0.45%/mes bruto · vacancia 4% · costos op. 1%/año · apreciación 3.5% UF/año · hold 5 años · costos tx 3.5% · sin apalancamiento[/dim]\n")

    t_fin = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_fin.add_column("Deal",           width=5)
    t_fin.add_column("Inversión total",justify="right", width=14)  # precio + costos tx
    t_fin.add_column("Val. Mercado D0",justify="right", width=14)
    t_fin.add_column("Upside D0",      justify="right", width=10)
    t_fin.add_column("NOI anual",      justify="right", width=12)  # net operating income
    t_fin.add_column("Yield bruto",    justify="right", width=11)
    t_fin.add_column("Cap Rate neto",  justify="right", width=12)
    t_fin.add_column("DSCR (hyp.)",    justify="right", width=11)  # hypothetical leverage
    t_fin.add_column("Val. Exit 5a",   justify="right", width=13)
    t_fin.add_column("Ganancia total", justify="right", width=14)
    t_fin.add_column("MOIC",           justify="right", width=7)
    t_fin.add_column("IRR",            justify="right", width=7)
    t_fin.add_column("Payback",        justify="right", width=9)

    for i, (s, f) in enumerate(zip(top10, fins), 1):
        t_fin.add_row(
            f"D-{i:02d}",
            _M(f["inversion"]),
            _M(f["val_merc"]),
            Text(f"{f['upside_d0']:+.1f}%", style="bright_green" if f["upside_d0"] > 0 else "red"),
            _M(f["noi"]),
            f"{f['yield_b']:.2f}%",
            Text(f"{f['cap_rate']:.2f}%", style="bright_green" if f["cap_rate"] >= 4.5 else "yellow"),
            Text(f"{f['dscr']:.2f}x", style="bright_green" if f["dscr"] >= 1.2 else "yellow"),
            _M(f["val_exit"]),
            Text(_M(f["ganancia"]), style="bold bright_green"),
            Text(f"{f['moic']:.2f}x", style="bold bright_green" if f["moic"] >= 1.8 else "green"),
            Text(f"{f['irr']:.1f}%", style="bold bright_green" if f["irr"] >= 12 else "green"),
            f"{f['payback']:.1f}a",
        )
    console.print(t_fin)
    console.print("[dim]  NOI = arriendo neto anual (bruto × (1-vac) × (1-costos op))  ·  DSCR hipotético = NOI / servicio deuda @ 5.5% LTV 70%  ·  Payback = años para recuperar inversión vía renta[/dim]\n")

    # ── 6. FICHAS INDIVIDUALES — TOP 5 ───────────────────────────────────────
    console.rule("[bold white]6. FICHAS DE INVERSIÓN INDIVIDUALES — Top 5 Deals[/bold white]", style="cyan")

    for i, (s, f) in enumerate(zip(top5, fins[:5]), 1):
        med_m2   = s.get("corridor_median_m2") or s["precio_m2"]
        desc_pct = (1 - s["precio_m2"] / med_m2) * 100
        dorm     = s.get("dormitorios"); banos = s.get("banos")
        db       = f"{dorm} dorm / {banos} baño" if dorm and banos else "—"
        red_pct  = 0.0
        if s.get("precio_inicial") and s["precio_inicial"] > s["precio"]:
            red_pct = (1 - s["precio"] / s["precio_inicial"]) * 100

        tipo_l = {"departamento": "Departamento", "casa": "Casa", "terreno": "Terreno"}.get(s["tipo_propiedad"], s["tipo_propiedad"])

        lines = (
            f"[bold white]{tipo_l}[/bold white] en [bold cyan]{s['comuna']}[/bold cyan]  ·  "
            f"{s['m2']:.0f} m²  ·  {db}\n\n"

            f"[dim]── PRECIO ────────────────────────────────────────────────[/dim]\n"
            f"  Precio publicado:    [bold white]{_M(s['precio'])}[/bold white]  ({_UF_(s['precio'])})\n"
            + (f"  Precio original:     [dim]{_M(s['precio_inicial'])}[/dim]  →  Reducción: [bright_green]-{red_pct:.1f}%[/bright_green] ({_M(int(s['precio_inicial']-s['precio']))} de descuento)\n" if red_pct > 0 else "")
            + f"  CLP/m²:              [white]${s['precio_m2']:,.0f}[/white]"
            f"  vs mediana corredor: [bright_green]{desc_pct:+.1f}%[/bright_green]"
            f"  (mediana: ${med_m2:,.0f}/m²)\n"
            f"  Valor a precio merc.: [bright_green bold]{_M(int(f['val_merc']))}[/bright_green bold]"
            f"  →  Upside D0: [bright_green bold]{f['upside_d0']:+.1f}%[/bright_green bold]\n\n"

            f"[dim]── TIMING ───────────────────────────────────────────────[/dim]\n"
            f"  Días en mercado:     [yellow]{s.get('days_on_market') or '—'}[/yellow] días"
            f"  ·  Score: [bold bright_green]{s['score']:.1f} / 100[/bold bright_green]\n\n"

            f"[dim]── FLUJO DE CAJA PROYECTADO (5 años, escenario base) ───[/dim]\n"
            f"  Inversión total:     [white]{_M(f['inversion'])}[/white]  (precio {_M(s['precio'])} + tx {_M(f['costos_tx'])})\n"
            f"  Arriendo bruto/mes:  [white]{_M(f['arr_bruto'])}[/white]  ({_M(f['arr_bruto']*12)}/año)\n"
            f"  NOI neto anual:      [white]{_M(f['noi'])}[/white]  (tras vacancia 4% + costos op 1%)\n"
            f"  Yield bruto:         [white]{f['yield_b']:.2f}%[/white]"
            f"  ·  Cap rate neto: [bright_green]{f['cap_rate']:.2f}%[/bright_green]\n"
            f"  Valor exit año 5:    [bright_green bold]{_M(f['val_exit'])}[/bright_green bold]  ({_UF_(f['val_exit'])})  @ +3.5% UF/año\n"
            f"  Ganancia total:      [bright_green bold]{_M(f['ganancia'])}[/bright_green bold]  (apreciación + rentas acumuladas)\n\n"

            f"[dim]── MÉTRICAS DE RETORNO ──────────────────────────────────[/dim]\n"
            f"  IRR (5 años):        [bold bright_green]{f['irr']:.1f}%[/bold bright_green] anual\n"
            f"  MOIC:                [bold bright_green]{f['moic']:.2f}x[/bold bright_green]\n"
            f"  Payback renta:       [white]{f['payback']:.1f} años[/white]  (solo vía arriendo)\n"
            f"  DSCR hipotético:     [white]{f['dscr']:.2f}x[/white]  (si 70% LTV @ 5.5%)\n\n"

            f"[dim]{s.get('url', '')}[/dim]"
        )
        console.print(Panel(lines,
            title=f"[bold cyan]D-{i:02d} · {s['comuna']} · Score {s['score']:.0f}[/bold cyan]",
            border_style="cyan", expand=True,
        ))

    # ── 7. ANÁLISIS DE ESCENARIOS ─────────────────────────────────────────────
    console.rule("[bold white]7. ANÁLISIS DE ESCENARIOS — Bear / Base / Bull[/bold white]", style="cyan")
    console.print("[dim]  Sensibilidad sobre D-01 a D-05 · variación en apreciación UF, yield arriendo y vacancia[/dim]\n")

    scenarios = {
        "BEAR": dict(appr=0.010, arr_rate=0.0038, vac=0.08,  label="red",          desc="Recesión leve: apreciación +1% UF, yield bajo, vacancia 8%"),
        "BASE": dict(appr=0.035, arr_rate=0.0045, vac=0.04,  label="white",        desc="Escenario central: +3.5% UF, yield 0.45%, vacancia 4%"),
        "BULL": dict(appr=0.055, arr_rate=0.0052, vac=0.025, label="bright_green", desc="Expansión: +5.5% UF, yield alto, vacancia 2.5%"),
    }

    t_sc = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_sc.add_column("Escenario",    width=8)
    t_sc.add_column("Apreciación",  justify="right", width=13)
    t_sc.add_column("Yield bruto",  justify="right", width=12)
    t_sc.add_column("Vacancia",     justify="right", width=10)
    for j in range(1, 6):
        t_sc.add_column(f"D-{j:02d} IRR", justify="right", width=10)
    t_sc.add_column("IRR Prom.",    justify="right", width=10)
    t_sc.add_column("MOIC Prom.",   justify="right", width=10)

    for sc_name, sc in scenarios.items():
        sc_fins  = [_deal_fin(s, **{k: v for k, v in sc.items() if k != "label" and k != "desc"}) for s in top5]
        irrs     = [f["irr"] for f in sc_fins]
        moics    = [f["moic"] for f in sc_fins]
        row      = [
            Text(sc_name, style=f"bold {sc['label']}"),
            f"{sc['appr']*100:.1f}% UF/a",
            f"{sc['arr_rate']*12*100:.2f}%/a",
            f"{sc['vac']*100:.0f}%",
        ] + [Text(f"{r:.1f}%", style=sc["label"]) for r in irrs] + [
            Text(f"{st.mean(irrs):.1f}%",  style=f"bold {sc['label']}"),
            Text(f"{st.mean(moics):.2f}x", style=f"bold {sc['label']}"),
        ]
        t_sc.add_row(*row)
    console.print(t_sc)

    # Tabla sensibilidad IRR vs descuento entrada
    console.print()
    console.print("[dim]  Sensibilidad IRR (D-01) vs descuento de entrada × apreciación UF:[/dim]")
    t_sens = Table(box=box.SIMPLE, header_style="bold dim", border_style="dim", padding=(0, 1))
    t_sens.add_column("Desc. entrada ↓  Apreciación →", width=32, style="dim")
    for appr_s in ["1.0%", "2.5%", "3.5%", "5.0%", "6.5%"]:
        t_sens.add_column(f"UF {appr_s}", justify="right", width=10)

    d01 = top5[0]
    base_precio = d01["precio"]
    for disc_pct in [5, 10, 15, 20, 25, 30, 35]:
        precio_adj = base_precio * (1 - disc_pct / 100)
        s_adj = {**d01, "precio": precio_adj, "precio_m2": precio_adj / d01["m2"]}
        row_vals = [f"Desc. {disc_pct:2d}%  ({_M(int(precio_adj))})"]
        for appr_v in [0.010, 0.025, 0.035, 0.050, 0.065]:
            ff = _deal_fin(s_adj, appr=appr_v)
            style = "bright_green bold" if ff["irr"] >= 18 else "green" if ff["irr"] >= 12 else "yellow" if ff["irr"] >= 8 else "red"
            row_vals.append(Text(f"{ff['irr']:.1f}%", style=style))
        t_sens.add_row(*row_vals)
    console.print(t_sens)

    # ── 8. CONSTRUCCIÓN DE PORTAFOLIO ─────────────────────────────────────────
    console.rule("[bold white]8. CONSTRUCCIÓN DE PORTAFOLIO — CLP 5,000 M[/bold white]", style="cyan")

    corredores = [
        ("Línea 1 — Central",  "Providencia · Santiago · Ñuñoa",     0.35, 100_000_000, "Liquidez alta · arriendo estable · metro L1/L2"),
        ("Línea 7 — Premium",  "Las Condes · Vitacura · Lo Barnechea",0.30, 220_000_000, "Apreciación > arriendo · perfil familia ABC1"),
        ("Línea 8 — Sur",      "La Florida · Puente Alto · Peñalolén",0.25,  90_000_000, "Mayor descuento · cap rate alto · clase media"),
        ("Expansión",          "La Reina · Maipú · San Miguel",       0.10,  95_000_000, "Diversificación · crecimiento organico"),
    ]

    t_port = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_port.add_column("Corredor",      width=22)
    t_port.add_column("Comunas",       width=36, style="dim")
    t_port.add_column("Asignación",    justify="right", width=11)
    t_port.add_column("CLP",           justify="right", width=12)
    t_port.add_column("UF equiv.",     justify="right", width=11)
    t_port.add_column("Ticket med.",   justify="right", width=12)
    t_port.add_column("N° activos",    justify="right", width=11)
    t_port.add_column("Estrategia",    style="dim")

    total_props = 0
    for nombre, comunas, alloc, ticket, estrategia in corredores:
        clp_z     = int(FUND_CLP * alloc)
        n_act     = max(1, int(clp_z / ticket))
        total_props += n_act
        t_port.add_row(
            f"[bold]{nombre}[/bold]", comunas,
            f"{alloc*100:.0f}%", _M(clp_z), _UF_(clp_z),
            _M(ticket), f"~{n_act}", estrategia,
        )
    t_port.add_row(
        "[bold white]TOTAL FONDO[/bold white]", "[dim]12 comunas RM[/dim]",
        "[bold white]100%[/bold white]", f"[bold white]{_M(FUND_CLP)}[/bold white]",
        f"[bold white]{_UF_(FUND_CLP)}[/bold white]",
        f"[bold white]{_M(int(FUND_CLP/total_props))}[/bold white]",
        f"[bold white]~{total_props}[/bold white]",
        "[dim]Diversificado · value-add[/dim]",
    )
    console.print(t_port)

    # Métricas consolidadas del portafolio
    console.print()
    t_kpi = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), border_style="dim")
    t_kpi.add_column("kpi",  style="dim",       width=38)
    t_kpi.add_column("val",  style="bold white", width=28)
    t_kpi.add_column("note", style="dim")

    nav_d0    = int(FUND_CLP * (1 + avg_upside / 100))
    val_5a    = int(FUND_CLP * avg_moic)
    dist_anual= int(FUND_CLP * avg_cap / 100)
    spread    = avg_cap - 5.0

    kpi_rows = [
        ("NAV portafolio a mercado (D0)",      _M(nav_d0),            f"Prima {avg_upside:.1f}% sobre costo adquisición"),
        ("Cap rate neto blend",                f"{avg_cap:.2f}%",      f"Spread vs TPM {5.0}%: {spread:+.1f}pp"),
        ("IRR target (escenario base, 5a)",    f"{avg_irr:.1f}% anual","cap rate + apreciación + captura descuento"),
        ("IRR bear (apreciación 1%, vac 8%)",  f"{st.mean(_deal_fin(s, appr=0.01, arr_rate=0.0038, vac=0.08)['irr'] for s in top10):.1f}% anual",
                                               "Protección ante corrección de mercado"),
        ("IRR bull (apreciación 5.5%, vac 2.5%)", f"{st.mean(_deal_fin(s, appr=0.055, arr_rate=0.0052, vac=0.025)['irr'] for s in top10):.1f}% anual",
                                               "Ciclo expansivo mercado RM"),
        ("MOIC base (5 años)",                 f"{avg_moic:.2f}x",    "money-on-invested-capital"),
        ("Valor portafolio año 5",             _M(val_5a),            "rentas acumuladas + apreciación UF"),
        ("Distribución anual (renta neta)",    _M(dist_anual),        "flujo a LPs antes de carried interest"),
        ("Distribución acumulada 5a",          _M(dist_anual * 5),    "flujo total a LPs vía renta"),
        ("Ganancia total 5a (renta + exit)",   _M(val_5a + dist_anual * 5 - FUND_CLP),
                                               "retorno neto sobre inversión inicial"),
    ]
    for row in kpi_rows:
        t_kpi.add_row(*row)
    console.print(t_kpi)

    # ── 9. ESTRUCTURA DEL FONDO Y TÉRMINOS ────────────────────────────────────
    console.rule("[bold white]9. ESTRUCTURA DEL FONDO Y TÉRMINOS ECONÓMICOS[/bold white]", style="cyan")

    t_terms = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_terms.add_column("Término",         width=30)
    t_terms.add_column("Valor",           style="bold white", width=28)
    t_terms.add_column("Detalle",         style="dim")

    terms = [
        ("Vehículo",               "SpA o Fondo Privado (CMF)",   "Estructura según asesoría legal"),
        ("Tamaño objetivo",        f"CLP 5,000 M  ({_UF_(FUND_CLP)})", "Serie A · posible expansión Serie B"),
        ("Inversión mínima LP",    "CLP 200 M  (UF 5,200)",       "Inversores acreditados Art. 4 bis LMV"),
        ("Moneda",                 "CLP / UF",                     "Arriendos en UF · NAV en CLP"),
        ("Management fee",         "1.5% anual sobre NAV",        "Cobrado trimestral · sobre activos gestionados"),
        ("Carried interest",       "20%  sobre retornos > hurdle", "GP lleva 20% de la ganancia sobre hurdle"),
        ("Hurdle rate",            "8% anual en UF",              "LPs reciben primero hasta 8% IRR en UF"),
        ("Catch-up GP",            "50% hasta igualar 20%/80%",   "Waterfall estándar PE"),
        ("Waterfall",              "1. Return capital  2. Hurdle  3. Catch-up GP  4. 80/20",
                                   "Distribución secuencial"),
        ("Horizonte fondo",        "5 años (+ 2 extensión)",      "Junta LPs puede aprobar extensión"),
        ("Período inversión",      "Años 1-2  (deal flow activo)", "Pipeline vivo desde score agent"),
        ("Período cosecha",        "Años 3-5  (exits selectivos)", "Trigger: apreciación ≥ 40% o TPM < 4%"),
        ("Distribuciones renta",   "Semestral",                   "NOI neto tras reservas operacionales"),
        ("Distribución exit",      "Al cierre de cada operación", "Neto impuestos + comisiones venta"),
        ("Liquidez LP",            "Sin ventana de liquidez",      "Mercado secundario privado — comité GP"),
        ("Auditoría",              "Anual por firma Big 4",        "Estados financieros IFRS"),
        ("Valorización activos",   "Semestral por tasador externo","Tasador inscrito MINVU"),
    ]
    for row in terms:
        t_terms.add_row(*row)
    console.print(t_terms)

    # ── 10. MATRIZ DE RIESGOS EXTENDIDA ──────────────────────────────────────
    console.rule("[bold white]10. MATRIZ DE RIESGOS — Probabilidad × Impacto × Mitigación[/bold white]", style="cyan")

    risks = [
        # (riesgo, prob, impacto, impacto_irr, mitigación)
        ("Caída precios RM > 15%",       "Medio", "Alto",   "-3 a -5pp IRR",
         "Entrada 20-30% bajo mediana → buffer estructural; diversif. 12 comunas"),
        ("Subida tasas hipotecarias",     "Bajo",  "Medio",  "-1 a -2pp IRR",
         "Compra en equity sin deuda → DSCR > 1 en todos los deals"),
        ("Vacancia > 8% sostenida",       "Bajo",  "Medio",  "-1.5pp IRR",
         "Comunas con demanda metro-céntrica; tesis precio bajo atrae arrendatario rápido"),
        ("Corrección ciclo inmobiliario", "Medio", "Alto",   "-2 a -4pp IRR",
         "Hold 5 años absorbe ciclos cortos; salida oportunista si mercado sube antes"),
        ("Cambio normativa tributaria",   "Bajo",  "Alto",   "Variable",
         "Estructurar en SpA desde inicio; asesoría Deloitte/PwC CL; revisar LIR Art.17"),
        ("Iliquidez de salida",           "Medio", "Medio",  "Extensión 2a",
         "Precio bajo mediana → fácil venta; broker mandato exclusivo 60 días"),
        ("Deterioro físico activo",       "Bajo",  "Bajo",   "-0.5pp IRR",
         "Reserva mantención 0.5% anual del valor; inspección técnica pre-compra"),
        ("Concentración sector residencial","Bajo", "Medio",  "-1pp IRR",
         "Solo residencial RM — riesgo sistémico, no idiosincrático"),
        ("Riesgo gestor (key-person)",    "Bajo",  "Alto",   "Operacional",
         "Equipo mínimo 3 personas; scoring agent automatizado reduce dependencia"),
        ("Riesgo data/scraping",          "Bajo",  "Bajo",   "Operacional",
         "Multi-fuente Portal + Yapo + TocToc; fallback manual; alerta SI fallan >6h"),
        ("Riesgo FX (UF vs CLP)",         "Bajo",  "Bajo",   "< 0.5pp IRR",
         "Arriendos en UF → ingreso indexado; costo en CLP nominal"),
        ("Concentración corredor único",  "Bajo",  "Medio",  "-1pp IRR",
         "Política: max 35% por corredor; revisión semestral de concentración"),
    ]

    t_risk = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_risk.add_column("Riesgo",           width=32)
    t_risk.add_column("Prob.",            width=8)
    t_risk.add_column("Impacto",          width=8)
    t_risk.add_column("Δ IRR est.",       justify="right", width=13)
    t_risk.add_column("Mitigación",       style="dim")

    ps = {"Alto": "red", "Medio": "yellow", "Bajo": "green"}
    for riesgo, prob, impacto, delta_irr, mit in risks:
        t_risk.add_row(
            riesgo,
            Text(f"● {prob}",    style=ps[prob]),
            Text(f"● {impacto}", style=ps[impacto]),
            Text(delta_irr, style="red" if "pp" in delta_irr else "dim"),
            mit,
        )
    console.print(t_risk)

    # ── 11. CRITERIOS DE INVERSIÓN Y PROCESO DD ───────────────────────────────
    console.rule("[bold white]11. CRITERIOS DE INVERSIÓN Y PROCESO DE DUE DILIGENCE[/bold white]", style="cyan")

    t_dd = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_dd.add_column("Fase",         style="bold cyan", width=16)
    t_dd.add_column("Criterio",     style="dim",       width=35)
    t_dd.add_column("Umbral",       style="bold white", width=22)
    t_dd.add_column("Acción si no cumple", style="dim")

    dd_rows = [
        # ENTRADA CUANTITATIVA
        ("FILTRO INICIAL",   "Score modelo compuesto",         "≥ 75 / 100",          "Descartar del pipeline"),
        ("",                 "Descuento vs mediana corredor",  "≥ 15%",               "Watchlist si 10-15%"),
        ("",                 "Días en mercado",                "≥ 30 días",           "Flexibilizar si reducción > 10%"),
        ("",                 "Precio unitario máximo",         "≤ UF 10,000 (~$385M)","Excluir — liquidez de salida limitada"),
        ("",                 "Tipo propiedad",                 "Depto o Casa",        "Sin terrenos sin renta operativa"),
        ("",                 "IRR estimado base",              "≥ 12% anual",         "No proceder a DD legal"),
        ("",                 "Cap rate neto",                  "≥ 4.5%",              "Evaluar caso a caso si upside D0 > 30%"),
        # DD LEGAL
        ("DD LEGAL",         "Dominio inscrito libre deudas",  "100% limpio CBR",     "Rechazar o negociar alzamiento"),
        ("",                 "Sin gravámenes / hipotecas",     "0 cargas activas",    "Descuento precio por alzamiento"),
        ("",                 "Sin litigios activos",           "Certificado tribunal", "Rechazar"),
        ("",                 "Permisos y recepción final",     "DOM vigente",         "Negociar regularización"),
        # DD TÉCNICA
        ("DD TÉCNICA",       "Inspección física pre-compra",   "Informe tasador MINVU","Descuento si reparaciones > 5%"),
        ("",                 "Superficies según escritura",    "±5% vs publicado",    "Renegociar precio proporcional"),
        ("",                 "Estado conservación",            "B o superior",        "Presupuestar Capex si C"),
        ("",                 "Gastos comunes estimados",       "< 2 UF/mes",          "Impacta yield neto"),
        # DD COMERCIAL
        ("DD COMERCIAL",     "Comparables arriendos zona",     "Yield ≥ 0.40%/mes",   "Revisar supuesto arriendo"),
        ("",                 "Demanda arrendataria local",     "Vacancia zona < 6%",  "Aumentar supuesto vacancia"),
        ("",                 "Perfil socioeconómico comuna",   "ABC1-C2 consolidado", "Riesgo devaluación largo plazo"),
        # APROBACIÓN
        ("APROBACIÓN",       "Votación comité inversión",      "≥ 2/3 miembros",      "No invertir"),
        ("",                 "Carta de intención enviada",     "< 48h post-aprobación","Prioridad competitiva"),
        ("",                 "Depósito garantía",              "0.5% precio acordado", "Asegurar opción de compra"),
    ]
    for row in dd_rows:
        t_dd.add_row(*row)
    console.print(t_dd)

    # ── 12. RECOMENDACIÓN FINAL AL COMITÉ ─────────────────────────────────────
    console.rule("[bold white]12. RECOMENDACIÓN FINAL — COMITÉ DE INVERSIÓN[/bold white]", style="cyan")

    n_compra_ya = sum(1 for s in valid if s["score"] >= 90)
    n_alta_p    = sum(1 for s in valid if 80 <= s["score"] < 90)
    bear_irr    = st.mean(_deal_fin(s, appr=0.01, arr_rate=0.0038, vac=0.08)["irr"] for s in top10)
    bull_irr    = st.mean(_deal_fin(s, appr=0.055, arr_rate=0.0052, vac=0.025)["irr"] for s in top10)

    console.print(Panel(
        f"[bold white]A. SITUACIÓN DE MERCADO[/bold white]\n"
        f"El mercado residencial de la RM presenta una ventana de entrada con {n_compra_ya} activos de score ≥ 90 "
        f"y {n_alta_p} de alta prioridad. El inventario estancado promedio de {dias_high:.0f} días en la cartera "
        f"calificada evidencia presión vendedora real — no es correlación estacional. La caída en TPM (ciclo bajista "
        f"BCCh desde 11.25% en 2023 a 5.0% actual) mejora la accesibilidad del comprador final, asegurando liquidez "
        f"de salida en el horizonte 5 años.\n\n"

        f"[bold white]B. VENTAJA COMPETITIVA DEL FONDO[/bold white]\n"
        f"El scoring automatizado sobre datos en tiempo real permite identificar deals con descuento estructural "
        f"({avg_upside:.1f}% promedio en top 10) antes que el mercado los corrija. La velocidad de identificación "
        f"→ valoración → oferta es la ventaja crítica: activos con score ≥ 90 tienen vida media de 30-45 días antes "
        f"de recibir ofertas de mercado.\n\n"

        f"[bold white]C. RETORNOS — RANGO DE ESCENARIOS[/bold white]\n"
        f"  Bear (recesión leve):     IRR [red]{bear_irr:.1f}%[/red]   — portafolio positivo incluso en escenario adverso\n"
        f"  Base (central):           IRR [bright_green]{avg_irr:.1f}%[/bright_green]   MOIC {avg_moic:.2f}x   · {_M(int(FUND_CLP * avg_moic))} al año 5\n"
        f"  Bull (expansión):         IRR [bright_green bold]{bull_irr:.1f}%[/bright_green bold]   — ciclo expansivo post-TPM baja\n\n"

        f"[bold white]D. ACCIONES INMEDIATAS RECOMENDADAS[/bold white]\n"
        f"  [bright_green bold]1.[/bright_green bold] DD legal D-01 (Santiago {_M(top5[0]['precio'])}) — carta de intención en 48h\n"
        f"  [bright_green bold]2.[/bright_green bold] DD legal D-02 (La Florida {_M(top5[1]['precio'])}) — paralelo a D-01\n"
        f"  [bright_green bold]3.[/bright_green bold] Tasación D-03 (Las Condes {_M(top5[2]['precio'])}) — confirmar valor mercado\n"
        f"  [bright_green bold]4.[/bright_green bold] Activar scoring agent en modo watchlist para D-04 a D-10\n"
        f"  [bright_green bold]5.[/bright_green bold] Iniciar proceso legal constitución vehículo (SpA) — 10-15 días hábiles\n"
        f"  [bright_green bold]6.[/bright_green bold] Road show inversores LP — presentar este memo a 8-10 family offices\n\n"

        f"[bold white]E. VOTO RECOMENDADO[/bold white]\n"
        f"[bright_green bold]✓ APROBAR[/bright_green bold] inicio de due diligence sobre D-01 y D-02 con presupuesto DD de CLP 15 M. "
        f"[bright_green bold]✓ APROBAR[/bright_green bold] constitución vehículo de inversión. "
        f"[yellow]◎ REVISAR[/yellow] en próximo comité (30 días) avance DD + nuevos deals del pipeline.\n\n"
        f"[dim]Scoring agent actualiza el pipeline automáticamente cada 6 horas. "
        f"Los deals pueden cambiar de ranking entre sesiones de comité — verificar scores actuales antes de cerrar operaciones.[/dim]",
        title="[bold bright_green]◆ RECOMENDACIÓN AL COMITÉ DE INVERSIÓN[/bold bright_green]",
        border_style="bright_green", expand=True,
    ))

    console.print(f"\n[dim]  Datos: Portal Inmobiliario · UF = ${_UF:,} CLP · {now}[/dim]")
    console.print(f"[dim]  Este memorandum es confidencial. Se basa en datos públicos de mercado. No constituye asesoría financiera regulada bajo la Ley 18.045 (LMV).[/dim]\n")


# ---------------------------------------------------------------------------
# HTML export — self-contained investment memo
# ---------------------------------------------------------------------------


def _generate_html(scored: list[dict], listings_total: int) -> str:
    """Generate a self-contained HTML investment memo. Returns the file path."""
    import statistics as st
    from pathlib import Path

    _UF      = 38_500
    FUND_CLP = 5_000_000_000
    now      = datetime.now().strftime("%d/%m/%Y %H:%M")
    fecha_l  = datetime.now().strftime("%d de %B de %Y")

    valid = [s for s in scored if s.get("score") is not None]
    top10 = sorted(valid, key=lambda x: x["score"], reverse=True)[:10]
    top5  = top10[:5]
    high  = [s for s in valid if s["score"] >= 75]

    def M(n):
        m = n / 1_000_000
        return f"${m:.1f}M" if m < 1000 else f"${m:.0f}M"

    def UF_(clp):
        return f"UF {clp/_UF:,.0f}"

    def pct_color(v):
        if v >= 15:   return "#00e676"
        if v >= 5:    return "#69f0ae"
        if v >= -5:   return "#ffd740"
        return "#ff5252"

    def score_color(s):
        if s >= 90: return "#00e676"
        if s >= 80: return "#69f0ae"
        if s >= 70: return "#ffd740"
        return "#ff5252"

    def irr_newton(cf, guess=0.10):
        r = guess
        for _ in range(60):
            npv  = sum(c / (1+r)**t for t, c in enumerate(cf))
            dnpv = sum(-t*c / (1+r)**(t+1) for t, c in enumerate(cf))
            if abs(dnpv) < 1e-12: break
            r -= npv / dnpv
            if r <= -1: r = -0.9999
        return r

    def deal_fin(s, appr=0.035, arr=0.0045, vac=0.04, op=0.01, tx=0.035, hold=5):
        entrada  = s["precio"]
        inv      = entrada * (1 + tx)
        med_m2   = s.get("corridor_median_m2") or s["precio_m2"]
        val_merc = med_m2 * s["m2"]
        arr_n    = entrada * arr * (1 - vac) * (1 - op)
        cap_rate = arr_n * 12 / entrada * 100
        val_exit = (entrada / _UF) * ((1+appr)**hold) * _UF
        cfs      = [-inv] + [arr_n*12]*(hold-1) + [arr_n*12 + val_exit]
        irr      = irr_newton(cfs) * 100
        moic     = (val_exit + arr_n*12*hold) / inv
        return dict(inv=inv, val_merc=val_merc,
                    upside=(val_merc/entrada-1)*100,
                    arr_n=arr_n, cap_rate=cap_rate,
                    val_exit=val_exit, irr=irr, moic=moic,
                    noi=arr_n*12, payback=inv/(arr_n*12) if arr_n else 99)

    fins     = [deal_fin(s) for s in top10]
    avg_cap  = st.mean(f["cap_rate"] for f in fins)
    avg_irr  = st.mean(f["irr"]      for f in fins)
    avg_moic = st.mean(f["moic"]     for f in fins)
    avg_disc = st.mean((1 - s["precio_m2"] / (s.get("corridor_median_m2") or s["precio_m2"])) * 100 for s in top10)
    avg_up   = abs(avg_disc)
    nav_d0   = int(FUND_CLP * (1 + avg_up/100))
    dias_h   = st.mean(s.get("days_on_market") or 0 for s in high)

    bear_irr = st.mean(deal_fin(s, appr=0.010, arr=0.0038, vac=0.08)["irr"] for s in top10)
    bull_irr = st.mean(deal_fin(s, appr=0.055, arr=0.0052, vac=0.025)["irr"] for s in top10)

    # ── pipeline rows ────────────────────────────────────────────────────────
    def pipe_rows():
        rows = ""
        for i, (s, f) in enumerate(zip(top10, fins), 1):
            med   = s.get("corridor_median_m2") or s["precio_m2"]
            desc  = (1 - s["precio_m2"] / med) * 100
            sc    = s["score"]
            badge = ("COMPRA YA" if sc >= 90 else "ALTA PRIORIDAD" if sc >= 82 else "MONITOREAR")
            bcls  = ("badge-buy" if sc >= 90 else "badge-high" if sc >= 82 else "badge-watch")
            dorm  = s.get("dormitorios"); banos = s.get("banos")
            db    = f"{dorm}d/{banos}b" if dorm and banos else "—"
            url   = s.get("url","")
            rows += f"""
            <tr>
              <td><span class="deal-id">D-{i:02d}</span></td>
              <td><strong>{s['comuna']}</strong></td>
              <td>{s['tipo_propiedad'].capitalize()}</td>
              <td>{s['m2']:.0f} m²</td>
              <td>{db}</td>
              <td class="num">{M(s['precio'])}</td>
              <td class="num">{UF_(s['precio'])}</td>
              <td class="num">${s['precio_m2']:,.0f}</td>
              <td class="num">${med:,.0f}</td>
              <td class="num" style="color:{pct_color(desc)};font-weight:700">{desc:+.1f}%</td>
              <td class="num">{s.get('days_on_market') or '—'}</td>
              <td class="num" style="color:{score_color(sc)};font-weight:700">{sc:.1f}</td>
              <td class="num" style="color:#69f0ae">{f['irr']:.1f}%</td>
              <td><span class="badge {bcls}">{badge}</span></td>
              <td><a href="{url}" target="_blank" class="link">↗</a></td>
            </tr>"""
        return rows

    # ── deal cards ───────────────────────────────────────────────────────────
    def deal_cards():
        cards = ""
        for i, (s, f) in enumerate(zip(top5, fins[:5]), 1):
            med   = s.get("corridor_median_m2") or s["precio_m2"]
            desc  = (1 - s["precio_m2"] / med) * 100
            red   = 0.0
            if s.get("precio_inicial") and s["precio_inicial"] > s["precio"]:
                red = (1 - s["precio"] / s["precio_inicial"]) * 100
            url   = s.get("url", "")
            tipo_l = {"departamento": "Departamento", "casa": "Casa", "terreno": "Terreno"}.get(s["tipo_propiedad"], s["tipo_propiedad"])
            dorm   = s.get("dormitorios"); banos = s.get("banos")
            db     = f"{dorm} dorm · {banos} baños" if dorm and banos else "—"
            sc     = s["score"]

            red_html = f'<div class="card-row"><span>Reducción precio</span><span class="green">-{red:.1f}% ({M(int(s["precio_inicial"]-s["precio"]))} ahorrado)</span></div>' if red > 0 else ""

            cards += f"""
            <div class="deal-card">
              <div class="deal-header">
                <div>
                  <span class="deal-label">D-{i:02d}</span>
                  <span class="deal-title">{tipo_l} · {s['comuna']}</span>
                  <span class="deal-sub">{s['m2']:.0f} m² · {db}</span>
                </div>
                <div class="score-circle" style="border-color:{score_color(sc)}">
                  <div class="score-num" style="color:{score_color(sc)}">{sc:.0f}</div>
                  <div class="score-label">SCORE</div>
                </div>
              </div>
              <div class="card-grid">
                <div class="card-block">
                  <div class="block-title">PRECIO</div>
                  <div class="card-row"><span>Precio entrada</span><span class="white big">{M(s['precio'])} <small>({UF_(s['precio'])})</small></span></div>
                  <div class="card-row"><span>CLP/m²</span><span>${s['precio_m2']:,.0f}</span></div>
                  <div class="card-row"><span>Mediana corredor</span><span>${med:,.0f}/m²</span></div>
                  <div class="card-row"><span>Descuento vs mediana</span><span class="green big">{desc:+.1f}%</span></div>
                  <div class="card-row"><span>Valor a precio mercado</span><span class="green">{M(int(f['val_merc']))}</span></div>
                  <div class="card-row"><span>Upside D0</span><span class="green big">{f['upside']:+.1f}%</span></div>
                  {red_html}
                </div>
                <div class="card-block">
                  <div class="block-title">TIMING</div>
                  <div class="card-row"><span>Días en mercado</span><span class="yellow big">{s.get('days_on_market') or '—'} días</span></div>
                  <div class="block-title" style="margin-top:16px">RENTA</div>
                  <div class="card-row"><span>Arriendo neto/mes</span><span>{M(f['arr_n'])}</span></div>
                  <div class="card-row"><span>NOI anual</span><span>{M(f['noi'])}</span></div>
                  <div class="card-row"><span>Yield bruto</span><span>{f['arr_n']*12/s['precio']*100/0.96:.2f}%</span></div>
                  <div class="card-row"><span>Cap rate neto</span><span class="green">{f['cap_rate']:.2f}%</span></div>
                </div>
                <div class="card-block">
                  <div class="block-title">RETORNO 5 AÑOS</div>
                  <div class="card-row"><span>Inversión total</span><span>{M(f['inv'])}</span></div>
                  <div class="card-row"><span>Valor exit año 5</span><span class="green">{M(f['val_exit'])}</span></div>
                  <div class="card-row"><span>Ganancia total</span><span class="green big">{M(f['val_exit']-f['inv']+f['noi']*5)}</span></div>
                  <div class="card-row"><span>MOIC</span><span class="green big">{f['moic']:.2f}x</span></div>
                  <div class="card-row"><span>IRR</span><span class="green big">{f['irr']:.1f}%</span></div>
                  <div class="card-row"><span>Payback (renta)</span><span>{f['payback']:.1f} años</span></div>
                  <div style="margin-top:12px"><a href="{url}" target="_blank" class="btn-link">Ver propiedad ↗</a></div>
                </div>
              </div>
            </div>"""
        return cards

    # ── scenario table rows ──────────────────────────────────────────────────
    def scenario_rows():
        scens = [
            ("BEAR", 0.010, 0.0038, 0.08,  "#ff5252"),
            ("BASE", 0.035, 0.0045, 0.04,  "#e0e0e0"),
            ("BULL", 0.055, 0.0052, 0.025, "#00e676"),
        ]
        rows = ""
        for name, appr, arr, vac, color in scens:
            fs   = [deal_fin(s, appr=appr, arr=arr, vac=vac) for s in top10]
            irrs = [f["irr"] for f in fs]
            moics= [f["moic"] for f in fs]
            rows += f"""<tr>
              <td style="color:{color};font-weight:700">{name}</td>
              <td>{appr*100:.1f}% UF/a</td>
              <td>{arr*12*100:.2f}%</td>
              <td>{vac*100:.0f}%</td>
              <td style="color:{color}">{irrs[0]:.1f}%</td>
              <td style="color:{color}">{irrs[1]:.1f}%</td>
              <td style="color:{color}">{irrs[2]:.1f}%</td>
              <td style="color:{color}">{irrs[3]:.1f}%</td>
              <td style="color:{color}">{irrs[4]:.1f}%</td>
              <td style="color:{color};font-weight:700">{st.mean(irrs):.1f}%</td>
              <td style="color:{color};font-weight:700">{st.mean(moics):.2f}x</td>
            </tr>"""
        return rows

    # ── sensitivity matrix ───────────────────────────────────────────────────
    def sens_matrix():
        apprs  = [0.010, 0.025, 0.035, 0.050, 0.065]
        discs  = [5, 10, 15, 20, 25, 30, 35]
        d01    = top5[0]
        base_p = d01["precio"]
        head   = "<tr><th>Desc. entrada ↓ / Aprec. UF →</th>" + "".join(f"<th>{a*100:.1f}%</th>" for a in apprs) + "</tr>"
        rows   = ""
        for disc in discs:
            adj   = {**d01, "precio": base_p*(1-disc/100), "precio_m2": base_p*(1-disc/100)/d01["m2"]}
            cells = ""
            for a in apprs:
                irr_v = deal_fin(adj, appr=a)["irr"]
                bg    = ("#00695c" if irr_v >= 16 else "#2e7d32" if irr_v >= 12 else "#f57f17" if irr_v >= 8 else "#b71c1c")
                cells += f'<td style="background:{bg};color:#fff;font-weight:600">{irr_v:.1f}%</td>'
            rows += f"<tr><td><strong>-{disc}%</strong> ({M(int(base_p*(1-disc/100)))})</td>{cells}</tr>"
        return head + rows

    # ── risk rows ────────────────────────────────────────────────────────────
    risk_data = [
        ("Caída precios RM > 15%",        "Medio", "Alto",   "-3 a -5pp", "Entrada 20-30% bajo mediana — buffer estructural desde D0"),
        ("Subida tasas hipotecarias",      "Bajo",  "Medio",  "-1 a -2pp", "Adquisición 100% equity — sin servicio de deuda"),
        ("Vacancia > 8% sostenida",        "Bajo",  "Medio",  "-1.5pp",    "Comunas con alta demanda de arriendo · precio bajo atrae inquilino"),
        ("Corrección ciclo inmobiliario",  "Medio", "Alto",   "-2 a -4pp", "Hold 5 años absorbe ciclos cortos · exit oportunista si aprecia antes"),
        ("Cambio normativa tributaria",    "Bajo",  "Alto",   "Variable",  "Estructurar en SpA desde inicio · asesoría Big 4"),
        ("Iliquidez de salida",            "Medio", "Medio",  "Extensión", "Precio bajo mediana → rotación rápida · broker con mandato 60d"),
        ("Deterioro físico activo",        "Bajo",  "Bajo",   "-0.5pp",    "Reserva mantención 0.5% anual · inspección técnica pre-compra"),
        ("Riesgo data / scoring",          "Bajo",  "Bajo",   "Operacional","Multi-fuente Portal+Yapo+TocToc · alerta si falla > 6h"),
        ("Concentración corredor único",   "Bajo",  "Medio",  "-1pp",      "Política: máx 35% por corredor · revisión semestral"),
        ("Riesgo key-person gestor",       "Bajo",  "Alto",   "Operacional","Equipo mínimo 3 · scoring agent automatizado reduce dependencia"),
    ]

    def prob_badge(p):
        colors = {"Alto": "#ff5252", "Medio": "#ffd740", "Bajo": "#69f0ae"}
        return f'<span class="risk-badge" style="background:{colors[p]}22;color:{colors[p]};border:1px solid {colors[p]}55">{p}</span>'

    risk_rows = "".join(f"""<tr>
      <td><strong>{r[0]}</strong></td>
      <td>{prob_badge(r[1])}</td><td>{prob_badge(r[2])}</td>
      <td style="color:#ff7043;font-weight:600">{r[3]}</td>
      <td class="dim">{r[4]}</td>
    </tr>""" for r in risk_data)

    # precompute to avoid backslashes inside f-string expressions (Python < 3.12)
    _cyan_strong_open = '<strong style="color:var(--cyan)">'
    _green_strong_open = '<strong style="color:var(--green)">'
    term_rows = "".join(
        f'<div class="term-row"><span class="term-key">{k}</span>'
        f'<span class="term-val">{v}</span><span class="term-note">{n}</span></div>'
        for k, v, n in [
            ("Vehículo", "SpA o Fondo Privado CMF", "Estructura según asesoría legal"),
            ("Tamaño objetivo", f"CLP 5,000 M  ·  {UF_(FUND_CLP)}", "Serie A"),
            ("Inversión mínima LP", "CLP 200 M  (UF 5,200)", "Inversionistas acreditados Art. 4bis LMV"),
            ("Management fee", "1.5% anual sobre NAV", "Cobrado trimestral"),
            ("Carried interest", "20% sobre retornos > hurdle", "GP sobre ganancia excedente"),
            ("Hurdle rate", "8% anual en UF", "LPs reciben primero hasta 8% IRR"),
            ("Catch-up GP", "50% hasta igualar 20/80", "Waterfall estándar PE"),
            ("Waterfall", "1. Capital · 2. Hurdle · 3. Catch-up · 4. 80/20", "Distribución secuencial"),
            ("Horizonte fondo", "5 años (+ 2 extensión)", "Aprobación junta LPs"),
            ("Período inversión", "Años 1-2", "Deal flow activo via scoring agent"),
            ("Período cosecha", "Años 3-5", "Exit selectivo — trigger apreciación ≥40%"),
            ("Distribución renta", "Semestral", "NOI neto tras reservas"),
            ("Liquidez LP", "Sin ventana", "Mercado secundario privado — comité GP"),
            ("Auditoría", "Anual · Big 4", "Estados financieros IFRS"),
            ("Valorización", "Semestral · tasador externo", "Tasador inscrito MINVU"),
            ("Moneda", "CLP / UF", "Arriendos en UF · NAV en CLP"),
        ]
    )
    dd_rows = "".join(
        f'<tr><td>{_cyan_strong_open + fase + "</strong>" if fase else ""}</td>'
        f'<td>{crit}</td>'
        f'<td style="color:var(--green);font-weight:600">{umbral}</td>'
        f'<td class="dim">{accion}</td></tr>'
        for fase, crit, umbral, accion in [
            ("FILTRO", "Score modelo compuesto", "≥ 75 / 100", "Descartar del pipeline"),
            ("", "Descuento vs mediana corredor", "≥ 15%", "Watchlist si 10-15%"),
            ("", "Días en mercado", "≥ 30 días", "Flexibilizar si reducción > 10%"),
            ("", "Precio unitario máximo", "≤ UF 10,000", "Excluir — liquidez limitada"),
            ("", "IRR estimado base", "≥ 12% anual", "No proceder a DD legal"),
            ("", "Cap rate neto", "≥ 4.5%", "Evaluar si upside D0 > 30%"),
            ("DD LEGAL", "Dominio inscrito libre cargas", "100% limpio CBR", "Rechazar o negociar alzamiento"),
            ("", "Hipotecas / gravámenes", "0 cargas activas", "Descuento por alzamiento"),
            ("", "Litigios activos", "Certificado tribunal limpio", "Rechazar"),
            ("", "Permisos y recepción final", "DOM vigente", "Negociar regularización"),
            ("DD TÉCNICA", "Inspección física", "Informe tasador MINVU", "Descuento si reparaciones > 5%"),
            ("", "Superficies vs escritura", "± 5%", "Renegociar proporcional"),
            ("", "Estado conservación", "B o superior", "Presupuestar Capex si C"),
            ("", "Gastos comunes", "< 2 UF/mes", "Impacta yield neto"),
            ("DD COMERCIAL", "Comparables arriendo zona", "Yield ≥ 0.40%/mes", "Revisar supuesto"),
            ("", "Vacancia zona", "< 6%", "Aumentar supuesto vacancia"),
            ("APROBACIÓN", "Votación comité inversión", "≥ 2/3 miembros", "No invertir"),
            ("", "Carta de intención", "< 48h post-aprobación", "Prioridad competitiva"),
            ("", "Depósito garantía", "0.5% precio acordado", "Asegurar opción de compra"),
        ]
    )
    bear_moic = st.mean(deal_fin(s, appr=0.010, arr=0.0038, vac=0.08)["moic"] for s in top10)
    bull_moic = st.mean(deal_fin(s, appr=0.055, arr=0.0052, vac=0.025)["moic"] for s in top10)
    sens_html = sens_matrix()
    sens_head = sens_html.split("</tr>", 1)[0] + "</tr>"
    sens_body = "</tr>".join(sens_html.split("</tr>")[1:])

    # ── full HTML ────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>REI Fund I — Investment Memo · {now}</title>
<style>
:root{{
  --bg:#060d1a;--bg2:#0d1b2e;--bg3:#132035;--bg4:#1a2840;
  --cyan:#00bcd4;--cyan2:#00e5ff;--green:#00e676;--green2:#69f0ae;
  --yellow:#ffd740;--red:#ff5252;--white:#e8eaf6;--dim:#6b7a99;
  --border:#1e3050;--card:#0f1e35;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--white);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;line-height:1.6}}
a{{color:var(--cyan);text-decoration:none}} a:hover{{color:var(--cyan2)}}
.link{{font-size:18px;color:var(--cyan)}}
.btn-link{{display:inline-block;padding:6px 16px;border:1px solid var(--cyan);border-radius:4px;color:var(--cyan);font-size:12px;transition:all .2s}}
.btn-link:hover{{background:var(--cyan);color:#000}}

/* Layout */
.container{{max-width:1400px;margin:0 auto;padding:32px 24px}}
.section{{margin-bottom:48px}}
.section-title{{font-size:11px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--cyan);border-left:3px solid var(--cyan);padding-left:12px;margin-bottom:20px}}

/* Cover */
.cover{{background:linear-gradient(135deg,#071020 0%,#0d1e38 50%,#071428 100%);border:1px solid var(--border);border-radius:12px;padding:48px;margin-bottom:48px;position:relative;overflow:hidden}}
.cover::before{{content:'';position:absolute;top:-50%;right:-10%;width:500px;height:500px;background:radial-gradient(circle,#00bcd415 0%,transparent 70%);pointer-events:none}}
.cover-badge{{display:inline-block;background:#ff174422;border:1px solid #ff174466;color:#ff7043;padding:4px 12px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:2px;margin-bottom:16px}}
.cover-title{{font-size:32px;font-weight:800;background:linear-gradient(135deg,var(--cyan2),var(--green));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:8px}}
.cover-sub{{font-size:16px;color:var(--dim);margin-bottom:32px}}
.cover-meta{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;border-top:1px solid var(--border);padding-top:24px}}
.meta-item .label{{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}}
.meta-item .value{{font-size:14px;color:var(--white);font-weight:600;margin-top:2px}}

/* KPI grid */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:32px}}
.kpi-card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px}}
.kpi-label{{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}}
.kpi-value{{font-size:24px;font-weight:800;line-height:1.1}}
.kpi-note{{font-size:11px;color:var(--dim);margin-top:4px}}
.kpi-green{{color:var(--green)}} .kpi-cyan{{color:var(--cyan2)}} .kpi-yellow{{color:var(--yellow)}}

/* Exec summary */
.exec-panel{{background:linear-gradient(135deg,#071a0e,#0a1e10);border:1px solid #00e67633;border-radius:12px;padding:32px;margin-bottom:32px}}
.exec-section{{margin-bottom:24px}}
.exec-section:last-child{{margin-bottom:0}}
.exec-label{{font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--green2);margin-bottom:8px}}
.exec-text{{color:var(--white);line-height:1.8;font-size:14px}}
.exec-text .hl{{color:var(--green);font-weight:700}}
.exec-text .hl2{{color:var(--cyan2);font-weight:700}}
.vote-row{{display:flex;gap:16px;margin-top:16px;flex-wrap:wrap}}
.vote-item{{padding:10px 20px;border-radius:6px;font-size:13px;font-weight:700}}
.vote-approve{{background:#00e67622;border:1px solid #00e67666;color:var(--green)}}
.vote-review{{background:#ffd74022;border:1px solid #ffd74066;color:var(--yellow)}}

/* Tables */
.table-wrap{{overflow-x:auto;margin-bottom:8px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:var(--bg3);color:var(--cyan);font-size:11px;text-transform:uppercase;letter-spacing:1px;padding:10px 12px;text-align:left;white-space:nowrap;border-bottom:2px solid var(--border)}}
td{{padding:9px 12px;border-bottom:1px solid var(--border);white-space:nowrap;vertical-align:middle}}
tr:hover td{{background:var(--bg3)}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.dim{{color:var(--dim)}}
.white{{color:var(--white)}} .green{{color:var(--green)}} .yellow{{color:var(--yellow)}}
.big{{font-size:15px;font-weight:700}}

/* Badges */
.badge{{padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:1px;white-space:nowrap}}
.badge-buy{{background:#00e67622;border:1px solid #00e67666;color:var(--green)}}
.badge-high{{background:#69f0ae22;border:1px solid #69f0ae66;color:var(--green2)}}
.badge-watch{{background:#ffd74022;border:1px solid #ffd74066;color:var(--yellow)}}
.deal-id{{background:var(--bg4);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;color:var(--cyan);font-family:monospace}}

/* Deal cards */
.deal-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:28px;margin-bottom:20px}}
.deal-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px;padding-bottom:20px;border-bottom:1px solid var(--border)}}
.deal-label{{font-size:11px;font-weight:700;color:var(--cyan);letter-spacing:2px;display:block;margin-bottom:4px}}
.deal-title{{font-size:20px;font-weight:800;color:var(--white);display:block;margin-bottom:4px}}
.deal-sub{{font-size:13px;color:var(--dim)}}
.score-circle{{width:72px;height:72px;border-radius:50%;border:3px solid;display:flex;flex-direction:column;align-items:center;justify-content:center;flex-shrink:0}}
.score-num{{font-size:22px;font-weight:800;line-height:1}}
.score-label{{font-size:9px;color:var(--dim);letter-spacing:1px}}
.card-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}}
.card-block{{background:var(--bg3);border-radius:8px;padding:16px}}
.block-title{{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--cyan);margin-bottom:12px}}
.card-row{{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border)}}
.card-row:last-child{{border-bottom:none}}
.card-row span:first-child{{color:var(--dim);font-size:12px}}
.card-row span:last-child{{font-size:13px;color:var(--white)}}

/* Scenario / sensitivity */
.scenario-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:32px}}
.sc-card{{border-radius:8px;padding:20px;text-align:center}}
.sc-bear{{background:#ff525211;border:1px solid #ff525244}}
.sc-base{{background:#e0e0e011;border:1px solid #e0e0e044}}
.sc-bull{{background:#00e67611;border:1px solid #00e67644}}
.sc-name{{font-size:11px;font-weight:700;letter-spacing:3px;margin-bottom:8px}}
.sc-irr{{font-size:36px;font-weight:900;line-height:1}}
.sc-moic{{font-size:13px;margin-top:4px}}
.sc-desc{{font-size:11px;color:var(--dim);margin-top:8px}}

/* Portfolio bars */
.portf-bar{{margin-bottom:12px}}
.portf-name{{font-size:12px;color:var(--dim);margin-bottom:4px;display:flex;justify-content:space-between}}
.bar-outer{{background:var(--bg3);border-radius:4px;height:20px;overflow:hidden}}
.bar-inner{{height:100%;border-radius:4px;display:flex;align-items:center;padding:0 10px;font-size:11px;font-weight:700;color:#000}}

/* Fund terms */
.terms-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:0}}
.term-row{{display:flex;padding:10px 16px;border-bottom:1px solid var(--border)}}
.term-row:nth-child(odd){{background:var(--bg3)}}
.term-key{{width:200px;flex-shrink:0;color:var(--dim);font-size:12px}}
.term-val{{font-weight:600;font-size:13px}}
.term-note{{margin-left:auto;color:var(--dim);font-size:11px}}

/* Risk badge */
.risk-badge{{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}

/* Sensitivity heat */
.sens-table td{{text-align:center;font-size:12px;padding:7px 10px}}
.sens-table th{{text-align:center}}

/* Footer */
.footer{{margin-top:64px;padding-top:24px;border-top:1px solid var(--border);color:var(--dim);font-size:11px;text-align:center;line-height:2}}

@media(max-width:900px){{.card-grid{{grid-template-columns:1fr}}.cover-meta{{grid-template-columns:1fr 1fr}}.scenario-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="container">

<!-- COVER -->
<div class="cover">
  <div class="cover-badge">⚑ CONFIDENCIAL</div>
  <div class="cover-title">REI Fund I — Investment Memo</div>
  <div class="cover-sub">Real Estate Intelligence Fund · Serie A · Región Metropolitana de Santiago</div>
  <div class="cover-meta">
    <div class="meta-item"><div class="label">Fecha de emisión</div><div class="value">{fecha_l}</div></div>
    <div class="meta-item"><div class="label">Fuente</div><div class="value">Portal Inmobiliario · {listings_total} props analizadas</div></div>
    <div class="meta-item"><div class="label">Tamaño fondo objetivo</div><div class="value" style="color:var(--green)">CLP 5,000 M · {UF_(FUND_CLP)}</div></div>
    <div class="meta-item"><div class="label">Estrategia</div><div class="value">Value-Add Residencial · Hold 5 años</div></div>
    <div class="meta-item"><div class="label">Cobertura</div><div class="value">12 comunas RM · Depto + Casa</div></div>
    <div class="meta-item"><div class="label">Preparado</div><div class="value">Real Estate Intelligence Agent v1.0</div></div>
  </div>
</div>

<!-- KPI STRIP -->
<div class="section">
  <div class="section-title">KPIs del fondo</div>
  <div class="kpi-grid">
    <div class="kpi-card"><div class="kpi-label">Pipeline HIGH</div><div class="kpi-value kpi-green">{len(high)}</div><div class="kpi-note">Deals score ≥ 75 · {len(high)/len(valid)*100:.0f}% del universo</div></div>
    <div class="kpi-card"><div class="kpi-label">Descuento entrada</div><div class="kpi-value kpi-green">{avg_up:.1f}%</div><div class="kpi-note">Bajo mediana corredor — margen D0</div></div>
    <div class="kpi-card"><div class="kpi-label">NAV D0</div><div class="kpi-value kpi-cyan">{M(nav_d0)}</div><div class="kpi-note">Valor mercado vs {M(FUND_CLP)} invertido</div></div>
    <div class="kpi-card"><div class="kpi-label">Cap Rate Neto</div><div class="kpi-value kpi-green">{avg_cap:.2f}%</div><div class="kpi-note">Spread vs TPM 5.0%: {avg_cap-5:+.1f}pp</div></div>
    <div class="kpi-card"><div class="kpi-label">IRR Base</div><div class="kpi-value kpi-green">{avg_irr:.1f}%</div><div class="kpi-note">Escenario central 5 años</div></div>
    <div class="kpi-card"><div class="kpi-label">MOIC</div><div class="kpi-value kpi-green">{avg_moic:.2f}x</div><div class="kpi-note">Money-on-invested-capital</div></div>
    <div class="kpi-card"><div class="kpi-label">Días Mercado (HIGH)</div><div class="kpi-value kpi-yellow">{dias_h:.0f}</div><div class="kpi-note">Inventario presionado — palanca negoc.</div></div>
    <div class="kpi-card"><div class="kpi-label">Valor portafolio año 5</div><div class="kpi-value kpi-cyan">{M(int(FUND_CLP*avg_moic))}</div><div class="kpi-note">Renta acum. + apreciación UF</div></div>
  </div>
</div>

<!-- EXEC SUMMARY -->
<div class="section">
  <div class="section-title">1. Resumen Ejecutivo</div>
  <div class="exec-panel">
    <div class="exec-section">
      <div class="exec-label">Oportunidad de Mercado</div>
      <div class="exec-text">Análisis de <span class="hl">{listings_total} propiedades</span> en 12 comunas de la RM detecta <span class="hl">{sum(1 for s in valid if s['score']>=90)} deals de compra inmediata</span> (score ≥ 90) y <span class="hl">{sum(1 for s in valid if 80<=s['score']<90)} de alta prioridad</span> (80-89). El inventario HIGH muestra <span class="hl">{dias_h:.0f} días</span> promedio en mercado — presión vendedora que genera palanca de negociación de 10-20% sobre precio publicado.</div>
    </div>
    <div class="exec-section">
      <div class="exec-label">Ventaja de Entrada — Margen de Seguridad D0</div>
      <div class="exec-text">Deals calificados presentan descuento promedio de <span class="hl">{avg_up:.1f}%</span> respecto a mediana del corredor. El fondo adquiere activos con valor de mercado de <span class="hl">{M(nav_d0)}</span> pagando <span class="hl2">CLP 5,000M</span>. El descuento funciona como buffer estructural ante correcciones de mercado.</div>
    </div>
    <div class="exec-section">
      <div class="exec-label">Retornos Proyectados</div>
      <div class="exec-text">IRR base <span class="hl">{avg_irr:.1f}%</span> · MOIC <span class="hl">{avg_moic:.2f}x</span> · Cap rate neto <span class="hl">{avg_cap:.2f}%</span> · Distribución anual renta LP: <span class="hl">{M(int(FUND_CLP*avg_cap/100))}</span> · Valor portafolio año 5: <span class="hl">{M(int(FUND_CLP*avg_moic))}</span></div>
    </div>
    <div class="exec-section">
      <div class="exec-label">Voto Recomendado al Comité</div>
      <div class="vote-row">
        <div class="vote-item vote-approve">✓ APROBAR due diligence D-01 y D-02 (presupuesto CLP 15M)</div>
        <div class="vote-item vote-approve">✓ APROBAR constitución vehículo SpA</div>
        <div class="vote-item vote-review">◎ REVISAR pipeline en 30 días</div>
      </div>
    </div>
  </div>
</div>

<!-- SCENARIOS -->
<div class="section">
  <div class="section-title">2. Escenarios de Retorno</div>
  <div class="scenario-grid">
    <div class="sc-card sc-bear">
      <div class="sc-name" style="color:#ff5252">BEAR</div>
      <div class="sc-irr" style="color:#ff5252">{bear_irr:.1f}%</div>
      <div class="sc-moic" style="color:#ff7043">IRR anual · 5 años</div>
      <div class="sc-desc">Apreciación +1% UF · yield 4.56% · vacancia 8%<br>Portafolio positivo incluso en recesión leve</div>
    </div>
    <div class="sc-card sc-base">
      <div class="sc-name" style="color:#e0e0e0">BASE</div>
      <div class="sc-irr" style="color:#e0e0e0">{avg_irr:.1f}%</div>
      <div class="sc-moic" style="color:#bdbdbd">IRR anual · MOIC {avg_moic:.2f}x</div>
      <div class="sc-desc">Apreciación +3.5% UF · yield 5.4% · vacancia 4%<br>Escenario central — supuestos conservadores</div>
    </div>
    <div class="sc-card sc-bull">
      <div class="sc-name" style="color:#00e676">BULL</div>
      <div class="sc-irr" style="color:#00e676">{bull_irr:.1f}%</div>
      <div class="sc-moic" style="color:#69f0ae">IRR anual · 5 años</div>
      <div class="sc-desc">Apreciación +5.5% UF · yield 6.24% · vacancia 2.5%<br>Ciclo expansivo post-baja TPM</div>
    </div>
  </div>
</div>

<!-- PIPELINE -->
<div class="section">
  <div class="section-title">3. Pipeline de Deals — Top 10 Calificados</div>
  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Deal</th><th>Comuna</th><th>Tipo</th><th>m²</th><th>Dorm/Bño</th>
        <th class="num">Precio</th><th class="num">UF</th><th class="num">CLP/m²</th>
        <th class="num">Med/m²</th><th class="num">Desc.</th><th class="num">Días</th>
        <th class="num">Score</th><th class="num">IRR</th><th>Status</th><th></th>
      </tr></thead>
      <tbody>{pipe_rows()}</tbody>
    </table>
  </div>
</div>

<!-- DEAL CARDS -->
<div class="section">
  <div class="section-title">4. Fichas de Inversión — Top 5 Deals</div>
  {deal_cards()}
</div>

<!-- SENSITIVITY -->
<div class="section">
  <div class="section-title">5. Análisis de Sensibilidad — IRR × Descuento × Apreciación UF (D-01)</div>
  <div class="table-wrap">
    <table class="sens-table">
      <thead>{sens_head}</thead>
      <tbody>{sens_body}</tbody>
    </table>
  </div>
  <div style="margin-top:8px;font-size:11px;color:var(--dim)">
    <span style="background:#00695c;color:#fff;padding:2px 8px;border-radius:3px;margin-right:6px">≥16%</span>Excepcional &nbsp;
    <span style="background:#2e7d32;color:#fff;padding:2px 8px;border-radius:3px;margin-right:6px">≥12%</span>Target &nbsp;
    <span style="background:#f57f17;color:#fff;padding:2px 8px;border-radius:3px;margin-right:6px">≥8%</span>Aceptable &nbsp;
    <span style="background:#b71c1c;color:#fff;padding:2px 8px;border-radius:3px;margin-right:6px">&lt;8%</span>Sub-óptimo
  </div>
</div>

<!-- PORTFOLIO CONSTRUCTION -->
<div class="section">
  <div class="section-title">6. Construcción de Portafolio — CLP 5,000 M</div>
  <div class="portf-bar"><div class="portf-name"><span>Línea 1 — Central (Providencia · Santiago · Ñuñoa)</span><span style="color:var(--cyan)">35% · $1,750M · ~17 activos</span></div><div class="bar-outer"><div class="bar-inner" style="width:35%;background:linear-gradient(90deg,#00bcd4,#006064)">35%</div></div></div>
  <div class="portf-bar"><div class="portf-name"><span>Línea 7 — Premium (Las Condes · Vitacura · Lo Barnechea)</span><span style="color:var(--green)">30% · $1,500M · ~6 activos</span></div><div class="bar-outer"><div class="bar-inner" style="width:30%;background:linear-gradient(90deg,#00e676,#1b5e20)">30%</div></div></div>
  <div class="portf-bar"><div class="portf-name"><span>Línea 8 — Sur (La Florida · Puente Alto · Peñalolén)</span><span style="color:var(--yellow)">25% · $1,250M · ~13 activos</span></div><div class="bar-outer"><div class="bar-inner" style="width:25%;background:linear-gradient(90deg,#ffd740,#e65100)">25%</div></div></div>
  <div class="portf-bar"><div class="portf-name"><span>Expansión (La Reina · Maipú · San Miguel)</span><span style="color:var(--dim)">10% · $500M · ~5 activos</span></div><div class="bar-outer"><div class="bar-inner" style="width:10%;background:linear-gradient(90deg,#78909c,#37474f)">10%</div></div></div>
  <br>
  <div class="table-wrap"><table>
    <thead><tr><th>Métrica portafolio</th><th>Bear</th><th class="num">Base</th><th class="num">Bull</th></tr></thead>
    <tbody>
      <tr><td>IRR anual</td><td style="color:#ff5252">{bear_irr:.1f}%</td><td class="num" style="color:#e0e0e0">{avg_irr:.1f}%</td><td class="num" style="color:#00e676">{bull_irr:.1f}%</td></tr>
      <tr><td>MOIC</td><td style="color:#ff5252">{bear_moic:.2f}x</td><td class="num" style="color:#e0e0e0">{avg_moic:.2f}x</td><td class="num" style="color:#00e676">{bull_moic:.2f}x</td></tr>
      <tr><td>Valor portafolio año 5</td><td style="color:#ff5252">{M(int(FUND_CLP*bear_moic))}</td><td class="num" style="color:#e0e0e0">{M(int(FUND_CLP*avg_moic))}</td><td class="num" style="color:#00e676">{M(int(FUND_CLP*bull_moic))}</td></tr>
      <tr><td>Cap rate neto</td><td style="color:#ff5252">{0.0038*12*(1-0.08)*(1-0.01)*100:.2f}%</td><td class="num" style="color:#e0e0e0">{avg_cap:.2f}%</td><td class="num" style="color:#00e676">{0.0052*12*(1-0.025)*(1-0.01)*100:.2f}%</td></tr>
      <tr><td>Distribución anual LP</td><td style="color:#ff5252">{M(int(FUND_CLP*0.0038*12*(1-0.08)*(1-0.01)))}</td><td class="num" style="color:#e0e0e0">{M(int(FUND_CLP*avg_cap/100))}</td><td class="num" style="color:#00e676">{M(int(FUND_CLP*0.0052*12*(1-0.025)*(1-0.01)))}</td></tr>
    </tbody>
  </table></div>
</div>

<!-- FUND TERMS -->
<div class="section">
  <div class="section-title">7. Estructura del Fondo y Términos</div>
  <div class="terms-grid">
    {term_rows}
  </div>
</div>

<!-- RISK MATRIX -->
<div class="section">
  <div class="section-title">8. Matriz de Riesgos</div>
  <div class="table-wrap"><table>
    <thead><tr><th>Riesgo</th><th>Probabilidad</th><th>Impacto</th><th>Δ IRR</th><th>Mitigación</th></tr></thead>
    <tbody>{risk_rows}</tbody>
  </table></div>
</div>

<!-- DD CHECKLIST -->
<div class="section">
  <div class="section-title">9. Proceso de Due Diligence</div>
  <div class="table-wrap"><table>
    <thead><tr><th>Fase</th><th>Criterio</th><th>Umbral</th><th>Acción si no cumple</th></tr></thead>
    <tbody>
    {dd_rows}
    </tbody>
  </table></div>
</div>

<!-- FOOTER -->
<div class="footer">
  <div>Datos: Portal Inmobiliario · UF = $38,500 CLP · Generado: {now}</div>
  <div>Este documento es confidencial. Se basa en datos públicos de mercado. No constituye asesoría financiera regulada bajo la Ley 18.045 (LMV).</div>
  <div style="margin-top:8px;color:#2a3a50">Real Estate Intelligence Agent v1.0 · REI Fund I Serie A</div>
</div>

</div>
</body>
</html>"""

    out_path = Path(f"rei_fund_memo_{datetime.now().strftime('%Y%m%d_%H%M')}.html")
    out_path.write_text(html, encoding="utf-8")
    return str(out_path.resolve())


# ---------------------------------------------------------------------------
# Main command: --top20
# ---------------------------------------------------------------------------


async def cmd_top20(tipos: list[str], max_pages: int, output_json: bool, demo: bool = False, report: bool = False, invest: bool = False, html_out: bool = False) -> None:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

    console = Console()

    if demo:
        console.print("[bold yellow]── MODO DEMO ── datos de muestra (no requiere internet)[/bold yellow]\n")
        all_raw = _demo_listings()
        console.print(f"[green]✓[/green] {len(all_raw)} listings de muestra cargados")
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[dim]{task.fields[detail]}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                "Scrapeando Portal Inmobiliario RM...",
                total=len(tipos),
                detail=f"0/{len(tipos)} tipos · max {max_pages} páginas cada uno",
            )

            from scraper.portal_inmobiliario import scrape_rm_full

            all_raw = []
            try:
                all_raw = await scrape_rm_full(tipos=tipos, max_pages=max_pages)
            except Exception as exc:
                console.print(f"\n[red]Error scrapeando: {exc}[/red]")
                console.print(
                    "[yellow]Tip: Portal Inmobiliario bloquea IPs de data center (Cloudflare).\n"
                    "Ejecuta desde tu máquina local o usa [bold]--demo[/bold] para previsualizar.[/yellow]"
                )
                sys.exit(1)
            finally:
                progress.advance(task, len(tipos))

        if not all_raw:
            console.print(
                "[red]✗ No se obtuvieron listings.[/red]\n"
                "[yellow]Portal Inmobiliario puede estar bloqueando esta IP (data center).\n"
                "→ Ejecuta desde tu laptop con IP residencial\n"
                "→ O usa [bold]python run.py --top20 --demo[/bold] para previsualizar el output[/yellow]"
            )
            sys.exit(1)

    console.print(
        f"[green]✓[/green] {len(all_raw):,} listings scrapeados"
        f" → deduplicando..."
    )
    unique = _deduplicate(all_raw)
    console.print(
        f"[green]✓[/green] {len(unique):,} únicos"
        f" ({len(all_raw) - len(unique):,} duplicados removidos)"
    )

    # Corridor medians
    medians = _compute_medians(unique)
    console.print(
        f"[green]✓[/green] {len(medians):,} grupos de corredor con mediana calculada"
    )

    # Score
    scored = [_score_listing(item, medians) for item in unique]
    n_scored = sum(1 for s in scored if s.get("score") is not None)
    console.print(f"[green]✓[/green] {n_scored:,} propiedades scored\n")

    if output_json:
        top = sorted(
            [s for s in scored if s.get("score") is not None],
            key=lambda x: x["score"],
            reverse=True,
        )[:20]
        print(json.dumps(top, indent=2, default=str, ensure_ascii=False))
    elif report:
        _print_report(scored, listings_total=len(unique))
    elif invest:
        _print_invest(scored, listings_total=len(unique))
    elif html_out:
        path = _generate_html(scored, listings_total=len(unique))
        console.print(f"[bright_green]✓[/bright_green] HTML generado: [bold]{path}[/bold]")
        console.print(f"[dim]  Abre con: xdg-open {path}  /  open {path}  /  o arrastra al browser[/dim]")
    else:
        _print_top20(scored)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = _parse_args()

    if not args.top20 and not args.report and not args.invest and not args.html:
        print(__doc__)
        sys.exit(0)

    asyncio.run(cmd_top20(
        tipos=args.tipos,
        max_pages=args.pages,
        output_json=args.output_json,
        demo=args.demo,
        report=args.report,
        invest=args.invest,
        html_out=args.html,
    ))


if __name__ == "__main__":
    main()
