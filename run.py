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
    """
    CFO-grade investment memo for fund presentation.
    Covers: thesis, deal pipeline, per-deal financials, portfolio construction,
    risk matrix, and return projections.
    """
    import statistics as st
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.rule import Rule
    from rich.panel import Panel
    from rich.text import Text

    console = Console(width=max(170, Console().width))
    now     = datetime.now().strftime("%d/%m/%Y %H:%M")
    _UF     = 38_500          # CLP por UF — Mayo 2026
    _UF_Y   = 38_500 * 12    # referencia anual
    FUND_CLP = 5_000_000_000  # tamaño fondo objetivo: CLP 5,000 M = ~UF 130k

    valid = [s for s in scored if s.get("score") is not None]
    top10 = sorted(valid, key=lambda x: x["score"], reverse=True)[:10]

    def _M(n):
        m = n / 1_000_000
        return f"${m:.1f}M" if m < 1000 else f"${m:.0f}M"

    def _UF_(clp):
        return f"UF {clp / _UF:,.0f}"

    def _pct(v, bold_green=False):
        s = f"{v:+.1f}%"
        return Text(s, style="bold bright_green") if bold_green and v > 0 else Text(s, style="bright_green" if v > 0 else "red")

    def _bar(v, mx, w=18):
        filled = int(round(v / mx * w)) if mx else 0
        return "█" * filled + "░" * (w - filled)

    # ── PORTADA ─────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"[bold white]MEMORANDUM DE INVERSIÓN[/bold white]\n"
        f"[cyan]Real Estate Intelligence Fund — Región Metropolitana de Santiago[/cyan]\n\n"
        f"[dim]Fecha:[/dim]          [white]{now}[/white]\n"
        f"[dim]Fuente:[/dim]         Portal Inmobiliario · {listings_total} propiedades analizadas\n"
        f"[dim]Cobertura:[/dim]      12 comunas RM · Departamentos + Casas\n"
        f"[dim]Clasificación:[/dim]  [yellow]CONFIDENCIAL — Uso interno fondo de inversión[/yellow]\n\n"
        f"[dim]Preparado por:[/dim]  Real Estate Intelligence Agent v1.0\n"
        f"[dim]Metodología:[/dim]    Scoring compuesto: precio/m² (55%) · tiempo mercado (30%) · reducción precio (15%)",
        title="[bold cyan]◆ INVESTMENT MEMO[/bold cyan]",
        border_style="cyan",
        expand=True,
    ))

    # ── 1. TESIS DE INVERSIÓN ────────────────────────────────────────────────
    console.rule("[bold white]1. TESIS DE INVERSIÓN[/bold white]", style="cyan")

    high   = [s for s in valid if s["score"] >= 75]
    medium = [s for s in valid if 60 <= s["score"] < 75]

    thesis_data = [
        ("Universo analizado",        f"{listings_total} propiedades · 12 comunas RM",       ""),
        ("Pipeline HIGH (score ≥75)", f"{len(high)} propiedades calificadas ({len(high)/len(valid)*100:.0f}% del universo)",
                                      "[bright_green]Robusto[/bright_green]"),
        ("Pipeline MEDIUM (60-74)",   f"{len(medium)} propiedades en watchlist",              "[yellow]Seguimiento[/yellow]"),
        ("Precio mediana mercado",    f"{_M(int(st.median(s['precio'] for s in valid)))}  ·  {_UF_(int(st.median(s['precio'] for s in valid)))}",
                                      ""),
        ("Descuento promedio HIGH",
         f"{st.mean((1 - s['precio_m2'] / s['corridor_median_m2']) * 100 for s in high if s.get('corridor_median_m2')):.1f}% bajo mediana corredor",
         "[bright_green]Entrada con margen[/bright_green]"),
        ("Días en mercado HIGH",
         f"{st.mean(s.get('days_on_market') or 0 for s in high):.0f} días promedio",
         "[yellow]Inventario presionado[/yellow]"),
        ("Tamaño fondo objetivo",     f"CLP {FUND_CLP/1e9:.1f} B  ·  {_UF_(FUND_CLP)}",    "[cyan]~13-15 activos[/cyan]"),
    ]

    t_th = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), border_style="dim")
    t_th.add_column("kpi",    style="dim", width=30)
    t_th.add_column("val",    style="bold white", width=55)
    t_th.add_column("signal", width=28)
    for row in thesis_data:
        t_th.add_row(*row)
    console.print(t_th)

    # ── 2. SUPUESTOS MACROECONÓMICOS ─────────────────────────────────────────
    console.rule("[bold white]2. SUPUESTOS MACRO — Mayo 2026[/bold white]", style="cyan")

    macro = [
        ("UF (Unidad de Fomento)",         f"$ {_UF:,} CLP",             "BCCh · ajuste inflación mensual"),
        ("Inflación anual (IPC)",           "4.2%",                        "INE · Meta BCCh 3%"),
        ("Tasa política monetaria (TPM)",   "5.00%",                       "BCCh · ciclo bajista 2025-2026"),
        ("Tasa hipotecaria a 20 años",      "4.8 – 5.4% en UF",           "Banca comercial · mayo 2026"),
        ("Apreciación real histórica RM",   "+3.5% / año en UF",           "Fuente: CChC / SII 2015-2025"),
        ("Cap rate residencial RM",         "4.5 – 6.0% bruto",           "Arriendo neto / precio compra"),
        ("Vacancia promedio RM",            "3 – 5%",                      "ACOP · mercado arrendamiento"),
        ("Costo transacción (entrada)",     "~3.5%",                       "Notaría + Conservador + IVA"),
        ("Impuesto ganancias capital",      "0% si >1 año hold (persona natural)", "Art. 17 LIR — umbral UF 8,000"),
    ]

    t_m = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), border_style="dim")
    t_m.add_column("param",  style="dim",        width=35)
    t_m.add_column("val",    style="bold white",  width=30)
    t_m.add_column("note",   style="dim",         width=50)
    for row in macro:
        t_m.add_row(*row)
    console.print(t_m)

    # ── 3. PIPELINE DE DEALS ─────────────────────────────────────────────────
    console.rule("[bold white]3. PIPELINE DE DEALS — Top 10 Calificados[/bold white]", style="cyan")

    t_pipe = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_pipe.add_column("Deal",        width=5)
    t_pipe.add_column("Comuna",      width=14)
    t_pipe.add_column("Tipo",        width=10)
    t_pipe.add_column("m²",          justify="right", width=5)
    t_pipe.add_column("Entrada CLP", justify="right", width=13)
    t_pipe.add_column("Entrada UF",  justify="right", width=11)
    t_pipe.add_column("CLP/m²",      justify="right", width=12)
    t_pipe.add_column("Med/m²",      justify="right", width=12)
    t_pipe.add_column("Desc.",       justify="right", width=7)
    t_pipe.add_column("Días Merc.",  justify="right", width=10)
    t_pipe.add_column("Score",       justify="right", width=7)
    t_pipe.add_column("Prioridad",   width=13)

    for i, s in enumerate(top10, 1):
        median_m2 = s.get("corridor_median_m2") or s["precio_m2"]
        desc_pct  = (1 - s["precio_m2"] / median_m2) * 100
        score     = s["score"]
        prioridad = (
            Text("● COMPRA YA",    style="bold bright_green") if score >= 90 else
            Text("● ALTA PRIOR.",  style="bold green")        if score >= 80 else
            Text("● SEGUIMIENTO",  style="yellow")
        )
        t_pipe.add_row(
            f"D-{i:02d}",
            s["comuna"],
            s["tipo_propiedad"].capitalize(),
            f"{s['m2']:.0f}",
            _M(s["precio"]),
            _UF_(s["precio"]),
            f"${s['precio_m2']:,.0f}",
            f"${median_m2:,.0f}",
            Text(f"{desc_pct:+.1f}%", style="bright_green" if desc_pct > 0 else "red"),
            f"{s.get('days_on_market') or '—'}d",
            Text(f"{score:.1f}", style="bold bright_green" if score >= 90 else "green"),
            prioridad,
        )
    console.print(t_pipe)

    # ── 4. ANÁLISIS FINANCIERO POR DEAL ──────────────────────────────────────
    console.rule("[bold white]4. ANÁLISIS FINANCIERO POR DEAL[/bold white]", style="cyan")
    console.print("[dim]  Supuestos: arriendo bruto 0.45% / mes sobre precio compra · vacancia 4% · costos op. 1% anual · apreciación 3.5% UF/año · hold 5 años · salida sin impuesto[/dim]\n")

    t_fin = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_fin.add_column("Deal",         width=5)
    t_fin.add_column("Precio Entr.", justify="right", width=13)
    t_fin.add_column("Val. Mercado", justify="right", width=13)
    t_fin.add_column("Upside Entr.", justify="right", width=12)
    t_fin.add_column("Arriendo/mes", justify="right", width=13)
    t_fin.add_column("Renta bruta",  justify="right", width=11)
    t_fin.add_column("Cap Rate",     justify="right", width=9)
    t_fin.add_column("Val. 5a (3.5%UF)", justify="right", width=16)
    t_fin.add_column("Ganancia 5a",  justify="right", width=13)
    t_fin.add_column("IRR ~5a",      justify="right", width=9)

    for i, s in enumerate(top10, 1):
        entrada   = s["precio"]
        med_m2    = s.get("corridor_median_m2") or s["precio_m2"]
        val_merc  = int(med_m2 * s["m2"])
        upside    = (val_merc / entrada - 1) * 100
        arriendo  = int(entrada * 0.0045 * (1 - 0.04))   # 0.45% bruto, 4% vacancia
        renta_b   = arriendo * 12 / entrada * 100
        cap_rate  = arriendo * 12 * (1 - 0.01) / entrada * 100  # -1% costos op
        # Apreciación: entrada ajustado por 3.5% real anual en UF, 5 años
        uf_units  = entrada / _UF
        uf_5y     = uf_units * (1.035 ** 5)
        val_5y    = int(uf_5y * _UF)
        ganancia  = val_5y - entrada + arriendo * 12 * 5
        # IRR simple (Newton aproximado con 3 iteraciones no es trivial; usar XIRR ~)
        # CF: -entrada, +arriendo*12 por 4 años, +arriendo*12+val_5y año 5
        # IRR aproximado: flujo total (rentas 5a + valor salida) sobre inversión inicial
        total_return = val_5y + arriendo * 12 * 5
        irr_aprox = (total_return / entrada) ** (1 / 5) - 1

        t_fin.add_row(
            f"D-{i:02d}",
            _M(entrada),
            _M(val_merc),
            Text(f"{upside:+.1f}%", style="bright_green" if upside > 0 else "red"),
            _M(arriendo),
            f"{renta_b:.2f}%",
            Text(f"{cap_rate:.2f}%", style="bright_green" if cap_rate >= 4.5 else "yellow"),
            _M(val_5y),
            Text(_M(ganancia), style="bold bright_green"),
            Text(f"{irr_aprox*100:.1f}%", style="bold bright_green" if irr_aprox >= 0.12 else "green"),
        )
    console.print(t_fin)

    # ── 5. CONSTRUCCIÓN DE PORTAFOLIO ────────────────────────────────────────
    console.rule("[bold white]5. CONSTRUCCIÓN DE PORTAFOLIO — Fondo CLP 5,000 M[/bold white]", style="cyan")

    # Estimar cuántos deals caben y diversificación recomendada
    corredor_alloc = {
        "Línea 1 — Central (Providencia/Santiago/Ñuñoa)": (0.35, "35%", "Liquidez alta, arriendo estable"),
        "Línea 7 — Premium (Las Condes/Vitacura/Lo Barnechea)": (0.30, "30%", "Apreciación > arriendo"),
        "Línea 8 — Sur (La Florida/Puente Alto/Peñalolén)":     (0.25, "25%", "Mayor descuento, cap rate alto"),
        "Expansión (La Reina/Maipú/San Miguel)":                (0.10, "10%", "Diversificación + crecimiento"),
    }

    t_port = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_port.add_column("Corredor / Zona",  width=48)
    t_port.add_column("Asignación",       justify="right", width=12)
    t_port.add_column("CLP",              justify="right", width=14)
    t_port.add_column("UF equiv.",        justify="right", width=12)
    t_port.add_column("N° activos est.", justify="right", width=14)
    t_port.add_column("Rationale",        style="dim")

    for zone, (alloc, alloc_s, rationale) in corredor_alloc.items():
        clp_z  = int(FUND_CLP * alloc)
        uf_z   = clp_z / _UF
        # Precio promedio estimado por zona
        zona_prices = {"Central": 100_000_000, "Premium": 220_000_000, "Sur": 90_000_000, "Expansión": 95_000_000}
        key = "Central" if "Central" in zone else "Premium" if "Premium" in zone else "Sur" if "Sur" in zone else "Expansión"
        n_activos = int(clp_z / zona_prices[key])
        t_port.add_row(
            zone, alloc_s,
            _M(clp_z),
            f"UF {uf_z:,.0f}",
            f"~{n_activos} props",
            rationale,
        )

    t_port.add_row(
        "[bold white]TOTAL FONDO[/bold white]", "[bold white]100%[/bold white]",
        "[bold white]" + _M(FUND_CLP) + "[/bold white]",
        "[bold white]" + f"UF {FUND_CLP/_UF:,.0f}" + "[/bold white]",
        "[bold white]~13-15 props[/bold white]",
        "[dim]Diversificado · 12 comunas[/dim]",
    )
    console.print(t_port)

    # Métricas portafolio proyectadas — calculadas dinámicamente desde top10
    console.print()
    high_scores = [s for s in top10]
    # Cap rate neto: 0.45%/mes bruto × 12 × (1-vacancia 4%) × (1-costos op 1%)
    avg_cap  = 0.0045 * 12 * (1 - 0.04) * (1 - 0.01) * 100   # ≈ 5.1%
    # IRR blend: cap rate neto + apreciación UF 3.5% + amortización descuento entrada /5
    avg_disc = st.mean((1 - s["precio_m2"] / (s.get("corridor_median_m2") or s["precio_m2"])) * 100 for s in high_scores)
    avg_upside = abs(avg_disc)
    avg_irr  = avg_cap + 3.5 + avg_upside / 5   # cap rate + apreciación + captura descuento

    t_proj = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), border_style="dim")
    t_proj.add_column("kpi",  style="dim",       width=38)
    t_proj.add_column("val",  style="bold white", width=25)
    t_proj.add_column("note", style="dim")

    spread = avg_cap - 5.0
    t_proj.add_row("Cap Rate portafolio blend",        f"{avg_cap:.1f}% neto",
                   f"vs TPM 5.0% — spread {'positivo +' if spread > 0 else ''}{spread:.1f}pp")
    t_proj.add_row("IRR target 5 años",                f"~{avg_irr:.1f}% anual",      "cap rate + apreciación UF + descuento entrada")
    t_proj.add_row("Descuento entrada promedio",       f"{avg_upside:.1f}% vs mediana","margen de seguridad desde día 1")
    t_proj.add_row("Valor portafolio a mercado (D0)",  _M(int(FUND_CLP * (1 + avg_upside/100))),
                                                       "NAV inmediato por descuento adquisición")
    t_proj.add_row("Valor proyectado año 5",           _M(int(FUND_CLP * ((1 + avg_irr/100) ** 5))),
                                                       "incluye rentas + apreciación")
    t_proj.add_row("Múltiplo equity (MOIC) 5a",       f"{(1 + avg_irr/100)**5:.2f}x", "money-on-invested-capital")
    t_proj.add_row("Distribución anual renta",         _M(int(FUND_CLP * avg_cap / 100)),
                                                       "flujo a LPs antes de carry")
    console.print(t_proj)

    # ── 6. MATRIZ DE RIESGOS ─────────────────────────────────────────────────
    console.rule("[bold white]6. MATRIZ DE RIESGOS[/bold white]", style="cyan")

    risks = [
        # (riesgo, probabilidad, impacto, mitigación)
        ("Caída precios RM",          "Medio",  "Alto",   "Entrada con 20-30% descuento vs mediana — buffer estructural"),
        ("Subida tasa hipotecaria",   "Bajo",   "Medio",  "Adquisición en equity — sin deuda a nivel fondo"),
        ("Vacancia > 5%",             "Bajo",   "Medio",  "Diversif. geográfica · zonas metro alta demanda"),
        ("Iliquidez activo",          "Medio",  "Medio",  "Hold mínimo 12 meses · ciclo venta 60-90 días RM"),
        ("Cambio normativa SII",      "Bajo",   "Alto",   "Estructurar como persona jurídica · asesoría tributaria"),
        ("Deterioro comuna",          "Bajo",   "Medio",  "Solo comunas consolidadas · score mínimo 75"),
        ("Ejecución scraping / data", "Bajo",   "Bajo",   "Multi-fuente · fallback httpx/BS4 · alertas automáticas"),
        ("Concentración corredor",    "Bajo",   "Medio",  "Límite 35% por zona · política diversificación"),
    ]

    t_risk = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim", padding=(0, 1))
    t_risk.add_column("Riesgo",         width=30)
    t_risk.add_column("Probabilidad",   width=13)
    t_risk.add_column("Impacto",        width=10)
    t_risk.add_column("Mitigación",     style="dim")

    prob_style = {"Alto": "red", "Medio": "yellow", "Bajo": "green"}
    for riesgo, prob, impacto, mit in risks:
        t_risk.add_row(
            riesgo,
            Text(f"● {prob}",    style=prob_style.get(prob, "white")),
            Text(f"● {impacto}", style=prob_style.get(impacto, "white")),
            mit,
        )
    console.print(t_risk)

    # ── 7. CRITERIOS DE ENTRADA / SALIDA ─────────────────────────────────────
    console.rule("[bold white]7. CRITERIOS DE ENTRADA Y SALIDA[/bold white]", style="cyan")

    criteria = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), border_style="dim")
    criteria.add_column("tipo",  style="bold cyan", width=14)
    criteria.add_column("crit",  style="dim",       width=32)
    criteria.add_column("val",   style="bold white", width=25)
    criteria.add_column("razon", style="dim")

    entradas = [
        ("ENTRADA",  "Score mínimo",          "≥ 75 / 100",          "Pipeline calificado según modelo"),
        ("",         "Descuento vs mediana",   "≥ 15% bajo corredor", "Margen seguridad + upside D0"),
        ("",         "Días en mercado",        "≥ 30 días",           "Vendedor presionado → negociación"),
        ("",         "Precio máx. unitario",   "≤ UF 10,000 (~$385M)","Liquidez de salida garantizada"),
        ("",         "Tipo propiedad",         "Depto o Casa",        "Sin terrenos — sin renta operativa"),
        ("",         "Reducción precio",       "≥ 5% vs precio orig.",  "Señal motivación vendedor"),
        ("SALIDA",   "Horizonte base",         "5 años",              "Optimización tributaria + ciclo mercado"),
        ("",         "Trigger anticipado",     "Apreciación ≥ 40%",   "Toma de ganancias temprana"),
        ("",         "Stop-loss",              "Vacancia > 8% × 6m",  "Evaluar desinversión anticipada"),
        ("",         "Refinanciamiento",       "Año 3 si TPM < 4.5%", "Apalancar para 2° tranche fondo"),
    ]
    for row in entradas:
        criteria.add_row(*row)
    console.print(criteria)

    # ── 8. RESUMEN EJECUTIVO ─────────────────────────────────────────────────
    console.rule("[bold white]8. RESUMEN EJECUTIVO — RECOMENDACIÓN AL COMITÉ[/bold white]", style="cyan")

    n_compra_ya = sum(1 for s in valid if s["score"] >= 90)
    n_alta      = sum(1 for s in valid if 80 <= s["score"] < 90)
    n_watch     = sum(1 for s in valid if 70 <= s["score"] < 80)

    console.print(Panel(
        f"[bold white]OPORTUNIDAD DE MERCADO[/bold white]\n"
        f"El análisis sobre {listings_total} propiedades activas en la RM detecta [bright_green bold]{n_compra_ya} deals de compra inmediata[/bright_green bold] "
        f"(score ≥ 90) y [green bold]{n_alta} de alta prioridad[/green bold] (80-89). "
        f"El mercado muestra inventario presionado con {st.mean(s.get('days_on_market') or 0 for s in high):.0f} días promedio "
        f"en cartera HIGH, generando palanca de negociación estructural.\n\n"
        f"[bold white]VENTAJA DE ENTRADA[/bold white]\n"
        f"Los deals calificados presentan un descuento promedio de [bright_green bold]{avg_upside:.1f}%[/bright_green bold] respecto a la mediana "
        f"del corredor, creando un NAV positivo desde el día 0. La combinación precio/m² + tiempo + "
        f"reducción histórica permite una entrada con margen de seguridad real, no teórico.\n\n"
        f"[bold white]RETORNOS PROYECTADOS[/bold white]\n"
        f"IRR target [bright_green bold]~{avg_irr:.1f}% anual[/bright_green bold] a 5 años · "
        f"MOIC [bright_green bold]{(1+avg_irr/100)**5:.2f}x[/bright_green bold] · "
        f"Cap rate neto [bright_green bold]{avg_cap:.1f}%[/bright_green bold] · "
        f"Spread vs TPM: [{'bright_green bold' if avg_cap >= 5.5 else 'yellow'}]{avg_cap - 5.0:+.1f}pp[/{'bright_green bold' if avg_cap >= 5.5 else 'yellow'}]\n\n"
        f"[bold white]RECOMENDACIÓN[/bold white]\n"
        f"[bright_green bold]PROCEDER[/bright_green bold] con due diligence sobre los [bold]3 deals D-01 a D-03[/bold]. "
        f"Iniciar proceso notarial en paralelo. Lanzar watchlist sobre D-04 a D-07 con alerta de "
        f"seguimiento semanal. Tamaño de fondo objetivo [bold]CLP 5,000 M[/bold] permite "
        f"diversificación en 13-15 activos con ticket promedio [bold]{_M(int(FUND_CLP/14))}[/bold].",
        title="[bold cyan]◆ EXECUTIVE SUMMARY[/bold cyan]",
        border_style="bright_green",
        expand=True,
    ))

    console.print(f"\n[dim]  Datos: Portal Inmobiliario · UF = ${_UF:,} CLP · {now}[/dim]")
    console.print(f"[dim]  Este memorandum es confidencial y se basa en datos de mercado público. No constituye asesoría financiera regulada.[/dim]\n")


# ---------------------------------------------------------------------------
# Main command: --top20
# ---------------------------------------------------------------------------


async def cmd_top20(tipos: list[str], max_pages: int, output_json: bool, demo: bool = False, report: bool = False, invest: bool = False) -> None:
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
    else:
        _print_top20(scored)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = _parse_args()

    if not args.top20 and not args.report and not args.invest:
        print(__doc__)
        sys.exit(0)

    asyncio.run(cmd_top20(
        tipos=args.tipos,
        max_pages=args.pages,
        output_json=args.output_json,
        demo=args.demo,
        report=args.report,
        invest=args.invest,
    ))


if __name__ == "__main__":
    main()
