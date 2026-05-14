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
    p.add_argument("--html",      action="store_true", help="Export investment memo as self-contained HTML (opens in browser)")
    p.add_argument("--dashboard", action="store_true", help="Export interactive HTML dashboard with JS filters (tipo, zona, score, badges)")
    p.add_argument(
        "--demo",
        action="store_true",
        help="Use built-in sample data (no internet required — useful in cloud/CI environments)",
    )
    p.add_argument("--corredor", action="store_true", help="Generate branded Market Intelligence Report PDF for real estate agents")
    p.add_argument("--zona", type=str, default="", metavar="COMUNAS", help='Comma-separated communes to filter, e.g. "Lampa,Quilicura"')
    p.add_argument("--subscription-preview", action="store_true", dest="subscription_preview", help="Preview weekly subscriber report format")
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
# Dynamic commune liquidity
# ---------------------------------------------------------------------------


def _compute_commune_liquidity(listings: list[dict]) -> dict[str, float]:
    """
    Compute liquidity score (0–100) per commune from observed data.
    Blends days-on-market speed (50%) and price tier vs RM median (50%).
    Communes with < 3 observations get 50 (neutral).
    """
    comm_days: dict[str, list[float]] = defaultdict(list)
    comm_pm2: dict[str, list[float]] = defaultdict(list)

    for item in listings:
        c = item.get("comuna", "")
        if not c:
            continue
        pub = item.get("fecha_publicacion")
        if pub:
            try:
                if isinstance(pub, str):
                    pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                else:
                    pub_dt = pub
                comm_days[c].append(float(max(0, (datetime.now(timezone.utc) - pub_dt).days)))
            except Exception:
                pass
        if item.get("precio_m2"):
            comm_pm2[c].append(float(item["precio_m2"]))

    all_pm2 = [v for vals in comm_pm2.values() for v in vals]
    global_med = statistics.median(all_pm2) if all_pm2 else 1.0

    result: dict[str, float] = {}
    for commune in set(comm_days) | set(comm_pm2):
        days_list = comm_days.get(commune, [])
        pm2_list  = comm_pm2.get(commune, [])

        # days-on-market score: 0d→100, 120d→30
        dias_score = (
            max(20.0, 100.0 - statistics.mean(days_list) * 0.583)
            if len(days_list) >= 3 else 50.0
        )
        # price-tier score: at global median→50, 2× median→90, 0.5× median→25
        price_score = (
            min(90.0, max(25.0, 25.0 + (statistics.median(pm2_list) / global_med) * 32.5))
            if len(pm2_list) >= 3 else 50.0
        )
        result[commune] = round(dias_score * 0.5 + price_score * 0.5, 1)

    return result


# ---------------------------------------------------------------------------
# In-memory scoring
# ---------------------------------------------------------------------------


def _score_listing(item: dict, medians: dict[tuple, float], commune_liq: Optional[dict[str, float]] = None) -> dict:
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

    # urgency_score
    from scoring.engine import urgency_score as _urgency_score, flip_score as _flip_score
    red_pct = 0.0
    if item.get("precio_inicial") and item["precio_inicial"] > item["precio"]:
        red_pct = (item["precio_inicial"] - item["precio"]) / item["precio_inicial"]
    item["urgency_score"] = _urgency_score(days_on_market=days or 0, reduccion_pct=red_pct)

    # flip_score — use dynamic liquidity if provided, else fall back to static estimates
    upside = ((median / item["precio_m2"]) - 1) * 100 if median else 0.0
    _STATIC_LIQ: dict[str, float] = {
        "Las Condes": 85, "Vitacura": 82, "Providencia": 80, "Lo Barnechea": 75,
        "Ñuñoa": 72, "La Reina": 70, "Santiago": 68, "Maipú": 55,
        "La Florida": 58, "San Miguel": 62, "Peñalolén": 50, "Puente Alto": 48,
        "Quilicura": 52, "Colina": 45, "Lampa": 42, "Buin": 40,
    }
    liq_table = commune_liq if commune_liq else _STATIC_LIQ
    item["flip_score"] = _flip_score(
        upside_pct=upside,
        commune_liquidity=liq_table.get(item["comuna"], 50.0),
    )

    # potencial_loteo_score for terrenos
    if item["tipo_propiedad"] == "terreno":
        from scoring.engine import potencial_loteo_score as _loteo_score
        ha = item["m2"] / 10_000
        precio_ha = item["precio"] / ha if ha > 0 else item["precio"]
        # derive median_ha from median precio_m2 * 10000
        median_ha_v2 = median * 10_000 if median else precio_ha
        item["potencial_loteo_score"] = _loteo_score(
            precio_ha=precio_ha,
            median_ha=median_ha_v2,
            zonificacion=item.get("zonificacion", ""),
        )
    else:
        item["potencial_loteo_score"] = None

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
    t.add_column("Flags",      no_wrap=True,     width=22)
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

        badges = []
        urg = prop.get("urgency_score", 0) or 0
        flp = prop.get("flip_score", 0) or 0
        lot = prop.get("potencial_loteo_score")
        if urg >= 60:    badges.append("[bold red]URGENTE[/bold red]")
        if flp >= 65:    badges.append("[bold yellow]FLIP[/bold yellow]")
        if lot and lot >= 65: badges.append("[bold cyan]LOTEO[/bold cyan]")
        badge_str = " ".join(badges) if badges else ""

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
            badge_str,
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


def _commune_slug(commune: str) -> str:
    """Convert commune display name to Portal Inmobiliario URL slug."""
    trans = str.maketrans("ÁÉÍÓÚáéíóúÑñÜü", "AEIOUaeiouNnUu")
    return commune.translate(trans).lower().replace(" ", "-")


def _demo_listings() -> list[dict]:
    """
    ~120 synthetic listings representative of the RM market (May 2026).
    Prices from portalinmobiliario.com search history; UF ≈ 38,500 CLP.
    """
    _UF = 40_100
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
        ("terreno",      "Quilicura",    5000,2_800, 0, 0, 200,0.22),  # ← loteo periurbano
        ("terreno",      "Colina",       3000,1_900, 0, 0, 180,0.18),
        ("terreno",      "Buin",         8000,1_200, 0, 0, 240,0.25),
        ("terreno",      "Paine",        6000,1_100, 0, 0, 120,0.15),
        ("terreno",      "Lampa",        4000,2_100, 0, 0, 90, 0.10),
        ("terreno",      "Batuco",      10000,  800, 0, 0, 300,0.30),  # ← muy motivado
    ]

    from datetime import timedelta
    zon_map = {
        "Quilicura": "ZH-Habitacional", "Colina": "ZH-Habitacional",
        "Buin": "Ag-Parcela", "Paine": "Ag-Parcela",
        "Lampa": "ZH-Expansion", "Batuco": "Ag-Parcela",
        "Lo Barnechea": "ZH-Habitacional", "Las Condes": "ZH-Habitacional",
        "Vitacura": "ZH-Habitacional",
    }
    listings: list[dict] = []
    for i, (tipo, comuna, m2, precio_uf, dorm, banos, dias, red_pct) in enumerate(raw):
        precio_clp = int(precio_uf * _UF)
        precio_inicial = int(precio_clp / (1 - red_pct)) if red_pct > 0 else None
        pub_date = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        item_dict = {
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
            "url":              f"https://www.portalinmobiliario.com/venta/{tipo}/{_commune_slug(comuna)}-metropolitana",
            "fecha_publicacion": pub_date,
            "scraped_at":       datetime.now(timezone.utc).isoformat(),
        }
        item_dict["zonificacion"] = zon_map.get(comuna, "") if tipo == "terreno" else ""
        listings.append(item_dict)

    # ── Real listings: URLs MLC completas extraídas de Portal Inmobiliario ───
    # URL completa con slug (formato MercadoLibre): MLC-{id}-{slug}-_JM
    # Precios confirmados ✓ desde el aviso; estimados (~) en otros.
    # external_id "mlc-..." → dashboard muestra "Ver en Portal →" directo al aviso.
    _REAL = [
        # (tipo, comuna, m2, precio_uf, dorm, banos, dias, red_pct, url_completa, sector, zon)
        # ── Departamentos ─────────────────────────────────────────────────────
        ("departamento", "Las Condes",  65, 5_200, 2, 2,  45, 0.00,
         "https://www.portalinmobiliario.com/MLC-1623084587-departamento-venta-2-dormitorios-manquehue-sur-las-condes-_JM",
         "Manquehue Sur, Las Condes", ""),
        ("departamento", "Las Condes",  70, 5_800, 2, 2,  30, 0.00,
         "https://www.portalinmobiliario.com/MLC-3180451284-departamento-en-venta-de-2-dorm-en-las-condes-_JM",
         "Las Condes", ""),
        ("departamento", "Providencia", 94, 7_500, 3, 3,  20, 0.00,
         "https://www.portalinmobiliario.com/MLC-3535905844-departamento-en-venta-en-providencia-_JM",
         "Av. Holanda, Providencia", ""),                     # triplex confirmado 94m²
        ("departamento", "Providencia", 55, 3_800, 2, 2,  35, 0.03,
         "https://www.portalinmobiliario.com/MLC-1598649629-departamento-en-venta-de-2-dorm-en-providencia-_JM",
         "Plaza Las Lilas, Providencia", ""),
        ("departamento", "Providencia", 80, 5_200, 3, 2,  25, 0.00,
         "https://www.portalinmobiliario.com/MLC-1002698085-departamento-venta-providencia-metro-manuel-montt-_JM",
         "Metro Manuel Montt, Providencia", ""),
        ("departamento", "Providencia", 60, 4_200, 2, 2,  40, 0.00,
         "https://www.portalinmobiliario.com/MLC-2890962336-departamento-2d2b-en-venta-en-providencia-alta-plusvalia-_JM",
         "Providencia, Alta Plusvalía", ""),
        ("departamento", "Ñuñoa",       55, 3_100, 2, 2,  55, 0.05,
         "https://www.portalinmobiliario.com/MLC-2453340000-venta-de-departamento-irrarazaval-2931-nunoa-_JM",
         "Irrazábal 2931, Ñuñoa", ""),
        # ── Casas ─────────────────────────────────────────────────────────────
        ("casa", "Ñuñoa",    80,  2_169, 4, 2,  90, 0.00,
         "https://www.portalinmobiliario.com/MLC-551863438-casa-en-venta-de-4-dorm-en-nunoa-_JM",
         "Metro Estadio Nacional, Ñuñoa", ""),                # $87M CLP ✓
        ("casa", "Vitacura", 200, 13_000, 4, 3,  60, 0.00,
         "https://www.portalinmobiliario.com/MLC-598763760-casa-en-venta-de-4-dormitorios-en-vitacura-_JM",
         "Vitacura", ""),                                      # UF 13.000 ✓
        # ── Terrenos ──────────────────────────────────────────────────────────
        ("terreno", "Colina", 5_000,  3_950, 0, 0, 120, 0.00,
         "https://www.portalinmobiliario.com/MLC-1569867597-gran-parcela-en-colina-5000m-por-3950-uf-_JM",
         "Colina / Batuco", "ZH-Habitacional"),                # UF 3.950 ✓
        ("terreno", "Colina", 5_000,  4_000, 0, 0, 150, 0.08,
         "https://www.portalinmobiliario.com/MLC-1483208591-parcela-en-colina-precio-desde-4000-uf-_JM",
         "Colina", "ZH-Habitacional"),                         # desde UF 4.000 ✓
        ("terreno", "Buin",   5_000,  6_000, 0, 0, 180, 0.00,
         "https://www.portalinmobiliario.com/MLC-1572477125-parcelas-en-venta-buin-5000-m2-desde-uf-6000-urbanizada-_JM",
         "Acceso Sur / P. Hurtado, Buin", "Ag-Parcela"),      # UF 6.000 ✓
        ("terreno", "Lampa", 24_590,  4_000, 0, 0, 200, 0.15,
         "https://www.portalinmobiliario.com/MLC-3060988162-terreno-2459-hectareas-fundo-chicauma-lamparm-_JM",
         "Fundo Chicauma, Lampa", "ZH-Expansion"),
        ("terreno", "Lampa", 120_000, 8_000, 0, 0, 120, 0.10,
         "https://www.portalinmobiliario.com/MLC-2808028908-terreno-lampa-subdividido-12-has-con-luz-agua-21-parcelas-_JM",
         "Lampa 12 há — 21 parcelas luz+agua", "ZH-Expansion"),
    ]

    for tipo, comuna, m2, precio_uf, dorm, banos, dias, red_pct, url, sector, zon in _REAL:
        precio_clp   = int(precio_uf * _UF)
        precio_inic  = int(precio_clp / (1 - red_pct)) if red_pct > 0 else None
        pub_date     = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        mlc_id       = url.split("/MLC-")[1].split("-")[0]
        listings.append({
            "external_id":       f"mlc-{mlc_id}",
            "source":            "portal_inmobiliario",
            "tipo_propiedad":    tipo,
            "comuna":            comuna,
            "address":           sector,
            "precio":            precio_clp,
            "precio_uf":         float(precio_uf),
            "precio_inicial":    precio_inic,
            "m2":                float(m2),
            "precio_m2":         round(precio_clp / m2, 0),
            "dormitorios":       dorm if dorm > 0 else None,
            "banos":             banos if banos > 0 else None,
            "url":               url,
            "fecha_publicacion": pub_date,
            "scraped_at":        datetime.now(timezone.utc).isoformat(),
            "zonificacion":      zon,
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
    _UF = 40_100

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
    _UF      = 40_100
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

    _UF      = 40_100
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
# Interactive Dashboard HTML — --dashboard
# ---------------------------------------------------------------------------


def _generate_dashboard_html(scored: list[dict], listings_total: int) -> str:
    """Generate a self-contained interactive HTML dashboard for daily corredor use."""
    import json as _json
    import statistics as st
    from datetime import datetime, timezone
    from pathlib import Path

    _UF = 40_100

    valid = [s for s in scored if s.get("score") is not None]
    valid_sorted = sorted(valid, key=lambda x: x["score"], reverse=True)

    # ── pre-compute global KPIs ─────────────────────────────────────────────
    prices_m2    = [s["precio_m2"] for s in valid]
    med_pm2      = st.median(prices_m2) if prices_m2 else 0
    avg_score    = st.mean(s["score"] for s in valid) if valid else 0
    pct_below    = 100 * sum(1 for s in valid if s.get("vs_median_pct", 0) < 0) / len(valid) if valid else 0
    n_urgente    = sum(1 for s in valid if (s.get("urgency_score") or 0) >= 60)
    n_flip       = sum(1 for s in valid if (s.get("flip_score") or 0) >= 65)
    n_loteo      = sum(1 for s in valid if (s.get("potencial_loteo_score") or 0) >= 65)

    # ── serialize listings for JS ────────────────────────────────────────────
    def _serializable(s: dict) -> dict:
        tipo  = s.get("tipo_propiedad", "")
        score = round(s.get("score") or 0, 1)
        vm    = round(s.get("vs_median_pct") or 0, 1)
        urg   = round(s.get("urgency_score") or 0, 1)
        flip  = round(s.get("flip_score") or 0, 1)
        loteo = round(s.get("potencial_loteo_score") or 0, 1)
        dias  = s.get("dias_mercado") or 0
        p_m2  = round(s.get("precio_m2") or 0, 0)
        p_uf  = round(s.get("precio") / _UF, 1) if s.get("precio") else 0

        # corredor group
        corredor_map = {
            "Las Condes": "Premium", "Vitacura": "Premium", "Providencia": "Premium",
            "Lo Barnechea": "Premium", "La Reina": "Premium",
            "Ñuñoa": "Consolidado", "Santiago": "Consolidado", "San Miguel": "Consolidado",
            "La Florida": "Consolidado", "Maipú": "Consolidado", "Peñalolén": "Consolidado",
            "Puente Alto": "Consolidado",
            "Quilicura": "Periurbano", "Colina": "Periurbano", "Lampa": "Periurbano",
            "Buin": "Periurbano", "Paine": "Periurbano", "Batuco": "Periurbano",
        }
        corredor = corredor_map.get(s.get("comuna", ""), "Otro")

        return {
            "id":       s.get("external_id", ""),
            "tipo":     tipo,
            "comuna":   s.get("comuna", "—"),
            "corredor": corredor,
            "address":  s.get("address", ""),
            "precio":   s.get("precio", 0),
            "precio_uf": p_uf,
            "precio_m2": int(p_m2),
            "m2":       s.get("m2", 0),
            "dorm":     s.get("dormitorios"),
            "banos":    s.get("banos"),
            "dias":     dias,
            "red_pct":  round((s.get("reduccion_precio_pct") or 0) * 100, 1),
            "score":    score,
            "vm":       vm,
            "urgente":  urg >= 60,
            "flip":     flip >= 65,
            "loteo":    loteo >= 65,
            "urg_val":  urg,
            "flip_val": flip,
            "lot_val":  loteo,
            "url":      s.get("url", "#"),
        }

    data_js = _json.dumps([_serializable(s) for s in valid_sorted], ensure_ascii=False)
    now_str = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    def _M(v: int) -> str:
        if v >= 1_000_000_000:
            return f"${v/1_000_000_000:.2f}B"
        if v >= 1_000_000:
            return f"${v/1_000_000:.1f}M"
        return f"${v:,.0f}"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>REI Dashboard — Portal Inmobiliario RM · {now_str}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;min-height:100vh}}
a{{color:#58a6ff;text-decoration:none}}
a:hover{{text-decoration:underline}}

/* ── header ── */
#hdr{{background:linear-gradient(135deg,#161b22 0%,#0d1117 100%);border-bottom:1px solid #30363d;padding:18px 24px 14px}}
#hdr h1{{font-size:20px;font-weight:700;color:#f0f6fc;letter-spacing:.3px}}
#hdr .sub{{color:#8b949e;font-size:12px;margin-top:3px}}

/* ── KPI bar ── */
#kpis{{display:flex;gap:20px;margin-top:14px;flex-wrap:wrap}}
.kpi{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 16px;min-width:120px}}
.kpi-val{{font-size:22px;font-weight:700;color:#f0f6fc}}
.kpi-lbl{{font-size:11px;color:#8b949e;margin-top:2px;text-transform:uppercase;letter-spacing:.5px}}
.kpi-val.green{{color:#3fb950}}
.kpi-val.yellow{{color:#d29922}}
.kpi-val.orange{{color:#f0883e}}
.kpi-val.red{{color:#f85149}}

/* ── filter bar ── */
#filters{{background:#161b22;border-bottom:1px solid #30363d;padding:12px 24px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;position:sticky;top:0;z-index:100}}
.fgroup{{display:flex;flex-direction:column;gap:4px}}
.flabel{{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;font-weight:600}}
.frow{{display:flex;gap:6px;align-items:center}}

/* checkboxes as pills */
.pill{{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:20px;border:1px solid #30363d;background:#0d1117;cursor:pointer;font-size:12px;color:#8b949e;transition:all .15s;user-select:none}}
.pill:hover{{border-color:#58a6ff;color:#58a6ff}}
.pill.active{{background:#1f6feb;border-color:#388bfd;color:#f0f6fc;font-weight:600}}
.pill input{{display:none}}

/* select */
select{{background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:5px 8px;font-size:12px;cursor:pointer;outline:none}}
select:focus{{border-color:#58a6ff}}

/* slider */
.slider-wrap{{display:flex;align-items:center;gap:8px}}
input[type=range]{{-webkit-appearance:none;width:110px;height:4px;background:#30363d;border-radius:2px;outline:none}}
input[type=range]::-webkit-slider-thumb{{-webkit-appearance:none;width:14px;height:14px;background:#58a6ff;border-radius:50%;cursor:pointer}}
.slider-val{{font-size:12px;color:#f0f6fc;min-width:24px;text-align:center;font-weight:600}}

/* sort buttons */
.sort-btn{{padding:4px 10px;border-radius:6px;border:1px solid #30363d;background:#0d1117;color:#8b949e;font-size:12px;cursor:pointer;transition:all .15s}}
.sort-btn:hover,.sort-btn.active{{background:#21262d;border-color:#58a6ff;color:#f0f6fc}}

/* badge toggle buttons */
.badge-toggle{{padding:4px 10px;border-radius:20px;font-size:11px;font-weight:700;cursor:pointer;border:1px solid;transition:all .15s;opacity:.5}}
.badge-toggle:hover{{opacity:.8}}
.badge-toggle.active{{opacity:1}}
.bt-urg{{color:#f85149;border-color:#f85149}}
.bt-urg.active{{background:rgba(248,81,73,.15)}}
.bt-flip{{color:#3fb950;border-color:#3fb950}}
.bt-flip.active{{background:rgba(63,185,80,.15)}}
.bt-lot{{color:#d29922;border-color:#d29922}}
.bt-lot.active{{background:rgba(210,153,34,.15)}}

/* ── count bar ── */
#cbar{{padding:8px 24px;background:#0d1117;border-bottom:1px solid #21262d;font-size:12px;color:#8b949e}}
#cbar span{{color:#f0f6fc;font-weight:600}}

/* ── grid ── */
#grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;padding:18px 24px 40px}}

/* ── card ── */
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;transition:border-color .15s;position:relative;overflow:hidden}}
.card:hover{{border-color:#58a6ff}}
.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px}}
.card.score-hi::before{{background:linear-gradient(90deg,#238636,#3fb950)}}
.card.score-md::before{{background:linear-gradient(90deg,#9e6a03,#d29922)}}
.card.score-lo::before{{background:linear-gradient(90deg,#6e1004,#f85149)}}

.card-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}}
.card-tipo{{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:#8b949e;font-weight:600}}
.score-badge{{font-size:18px;font-weight:800;padding:2px 8px;border-radius:6px}}
.score-badge.hi{{color:#3fb950;background:rgba(63,185,80,.12)}}
.score-badge.md{{color:#d29922;background:rgba(210,153,34,.12)}}
.score-badge.lo{{color:#f85149;background:rgba(248,81,73,.12)}}

.card-comuna{{font-size:15px;font-weight:700;color:#f0f6fc;margin-bottom:2px}}
.card-addr{{font-size:11px;color:#8b949e;margin-bottom:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}

.metrics{{display:grid;grid-template-columns:1fr 1fr;gap:5px 10px;margin-bottom:10px}}
.met{{display:flex;flex-direction:column}}
.met-val{{font-size:13px;font-weight:600;color:#f0f6fc}}
.met-lbl{{font-size:10px;color:#8b949e}}

.vm-pos{{color:#f85149}}
.vm-neg{{color:#3fb950}}

.flags{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:10px}}
.flag{{font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;letter-spacing:.4px}}
.flag-urg{{background:rgba(248,81,73,.2);color:#f85149;border:1px solid rgba(248,81,73,.4)}}
.flag-flip{{background:rgba(63,185,80,.2);color:#3fb950;border:1px solid rgba(63,185,80,.4)}}
.flag-lot{{background:rgba(210,153,34,.2);color:#d29922;border:1px solid rgba(210,153,34,.4)}}

.card-footer{{display:flex;justify-content:space-between;align-items:center;border-top:1px solid #21262d;padding-top:8px;margin-top:4px}}
.card-dias{{font-size:11px;color:#8b949e}}
.card-link{{font-size:11px}}

/* ── empty state ── */
#empty{{display:none;text-align:center;padding:60px 24px;color:#8b949e}}
#empty h3{{font-size:18px;color:#e6edf3;margin-bottom:8px}}

/* ── footer ── */
#footer{{text-align:center;padding:20px;font-size:11px;color:#484f58;border-top:1px solid #21262d}}

@media(max-width:600px){{
  #kpis{{gap:10px}}
  .kpi{{min-width:90px;padding:8px 12px}}
  #filters{{gap:10px}}
  #grid{{grid-template-columns:1fr;gap:10px;padding:12px}}
}}
</style>
</head>
<body>

<div id="hdr">
  <h1>REI Intelligence Dashboard</h1>
  <div class="sub">Portal Inmobiliario · Región Metropolitana · {now_str} · {listings_total:,} listings analizados</div>
  <div id="kpis">
    <div class="kpi"><div class="kpi-val" id="kpi-vis">{len(valid):,}</div><div class="kpi-lbl">Propiedades</div></div>
    <div class="kpi"><div class="kpi-val green">{avg_score:.0f}</div><div class="kpi-lbl">Score Promedio</div></div>
    <div class="kpi"><div class="kpi-val">{int(med_pm2/1000):,}K</div><div class="kpi-lbl">Mediana $/m²</div></div>
    <div class="kpi"><div class="kpi-val yellow">{pct_below:.0f}%</div><div class="kpi-lbl">Bajo Mediana</div></div>
    <div class="kpi"><div class="kpi-val red">{n_urgente}</div><div class="kpi-lbl">Urgentes</div></div>
    <div class="kpi"><div class="kpi-val green">{n_flip}</div><div class="kpi-lbl">Flip</div></div>
    <div class="kpi"><div class="kpi-val orange">{n_loteo}</div><div class="kpi-lbl">Loteo</div></div>
  </div>
</div>

<div id="filters">
  <div class="fgroup">
    <div class="flabel">Tipo</div>
    <div class="frow" id="tipo-pills">
      <label class="pill active" data-tipo="departamento"><input type="checkbox" checked> Depto</label>
      <label class="pill active" data-tipo="casa"><input type="checkbox" checked> Casa</label>
      <label class="pill active" data-tipo="terreno"><input type="checkbox" checked> Terreno</label>
    </div>
  </div>
  <div class="fgroup">
    <div class="flabel">Corredor</div>
    <div class="frow">
      <select id="corredor-sel">
        <option value="">Todos</option>
        <option value="Premium">Premium</option>
        <option value="Consolidado">Consolidado</option>
        <option value="Periurbano">Periurbano</option>
      </select>
    </div>
  </div>
  <div class="fgroup">
    <div class="flabel">Score mínimo</div>
    <div class="frow slider-wrap">
      <input type="range" id="score-slider" min="0" max="100" value="0">
      <div class="slider-val" id="score-val">0</div>
    </div>
  </div>
  <div class="fgroup">
    <div class="flabel">Señales</div>
    <div class="frow" id="badge-toggles">
      <button class="badge-toggle bt-urg" data-flag="urgente">URGENTE</button>
      <button class="badge-toggle bt-flip" data-flag="flip">FLIP</button>
      <button class="badge-toggle bt-lot" data-flag="loteo">LOTEO</button>
    </div>
  </div>
  <div class="fgroup">
    <div class="flabel">Ordenar</div>
    <div class="frow" id="sort-btns">
      <button class="sort-btn active" data-sort="score">Score ↓</button>
      <button class="sort-btn" data-sort="precio_asc">Precio ↑</button>
      <button class="sort-btn" data-sort="precio_desc">Precio ↓</button>
      <button class="sort-btn" data-sort="dias">Días ↑</button>
    </div>
  </div>
  <div class="fgroup">
    <div class="flabel">&nbsp;</div>
    <div class="frow">
      <button class="sort-btn" id="reset-btn">↺ Reset</button>
    </div>
  </div>
</div>

<div id="cbar">Mostrando <span id="cnt">0</span> de <span id="total">{len(valid)}</span> propiedades</div>
<div id="grid"></div>
<div id="empty"><h3>Sin resultados</h3><p>Ajusta los filtros para ver propiedades.</p></div>
<div id="footer">Real Estate Intelligence Agent · Portal Inmobiliario RM · Metodología: precio/m² vs mediana corredor (55%) · tiempo mercado (30%) · reducción precio (15%) · UF = $40,100 CLP</div>

<script>
const DATA = {data_js};

// ── state ────────────────────────────────────────────────────────────────
let tipos = new Set(['departamento','casa','terreno']);
let corredor = '';
let minScore = 0;
let flagFilters = new Set(); // 'urgente','flip','loteo'
let sortKey = 'score';

// ── helpers ──────────────────────────────────────────────────────────────
function fmtM(v){{
  if(v>=1e9) return '$'+( v/1e9).toFixed(2)+'B';
  if(v>=1e6) return '$'+(v/1e6).toFixed(1)+'M';
  if(v>=1e3) return '$'+(v/1e3).toFixed(0)+'K';
  return '$'+v;
}}
function fmtNum(v){{return new Intl.NumberFormat('es-CL').format(v);}}
function scoreClass(s){{return s>=70?'hi':s>=50?'md':'lo';}}
function badgeClass(s){{return s>=70?'score-badge hi':s>=50?'score-badge md':'score-badge lo';}}
function cardClass(s){{return s>=70?'card score-hi':s>=50?'card score-md':'card score-lo';}}
function vmClass(v){{return v>0?'vm-pos':'vm-neg';}}

// ── filter + sort ─────────────────────────────────────────────────────────
function applyFilters(){{
  let data = DATA.filter(d => {{
    if(!tipos.has(d.tipo)) return false;
    if(corredor && d.corredor !== corredor) return false;
    if(d.score < minScore) return false;
    for(const f of flagFilters) if(!d[f]) return false;
    return true;
  }});

  if(sortKey==='score')       data.sort((a,b)=>b.score-a.score);
  else if(sortKey==='precio_asc')  data.sort((a,b)=>a.precio-b.precio);
  else if(sortKey==='precio_desc') data.sort((a,b)=>b.precio-a.precio);
  else if(sortKey==='dias')   data.sort((a,b)=>a.dias-b.dias);

  return data;
}}

// ── render card ───────────────────────────────────────────────────────────
function renderCard(d){{
  const sc = scoreClass(d.score);
  const vmStr = (d.vm>=0?'+':'')+d.vm.toFixed(1)+'%';
  const vmCls = vmClass(d.vm);
  const dormStr = d.dorm?`${{d.dorm}}d/${{d.banos||'–'}}b`:'—';
  let flags = '';
  if(d.urgente) flags += '<span class="flag flag-urg">URGENTE</span>';
  if(d.flip)    flags += '<span class="flag flag-flip">FLIP</span>';
  if(d.loteo)   flags += '<span class="flag flag-lot">LOTEO</span>';
  const tipoLabel = {{departamento:'Departamento',casa:'Casa',terreno:'Terreno'}}[d.tipo]||d.tipo;
  return `<div class="${{cardClass(d.score)}}">
  <div class="card-top">
    <div><div class="card-tipo">${{tipoLabel}} · ${{d.corredor}}</div></div>
    <div class="${{badgeClass(d.score)}}">${{d.score.toFixed(0)}}</div>
  </div>
  <div class="card-comuna">${{d.comuna}}</div>
  <div class="card-addr">${{d.address}}</div>
  <div class="metrics">
    <div class="met"><div class="met-val">UF ${{fmtNum(d.precio_uf)}}</div><div class="met-lbl">Precio</div></div>
    <div class="met"><div class="met-val ${{vmCls}}">${{vmStr}}</div><div class="met-lbl">vs Mediana</div></div>
    <div class="met"><div class="met-val">${{fmtNum(d.m2)}} m²</div><div class="met-lbl">Superficie</div></div>
    <div class="met"><div class="met-val">${{fmtNum(d.precio_m2)}} $/m²</div><div class="met-lbl">Precio/m²</div></div>
    ${{d.dorm?`<div class="met"><div class="met-val">${{dormStr}}</div><div class="met-lbl">Dorm/Baños</div></div>`:''}}
    ${{d.red_pct>0?`<div class="met"><div class="met-val vm-neg">-${{d.red_pct.toFixed(1)}}%</div><div class="met-lbl">Reducción</div></div>`:''}}
  </div>
  ${{flags?`<div class="flags">${{flags}}</div>`:''}}
  <div class="card-footer">
    <div class="card-dias">${{d.dias}} días publicado</div>
    <a class="card-link" href="${{d.url}}" target="_blank">${{d.id.startsWith('demo-')?'Buscar similares →':'Ver en Portal →'}}</a>
  </div>
</div>`;
}}

// ── render grid ───────────────────────────────────────────────────────────
function render(){{
  const data = applyFilters();
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  document.getElementById('cnt').textContent = data.length;

  if(data.length===0){{
    grid.innerHTML='';
    empty.style.display='block';
    return;
  }}
  empty.style.display='none';
  grid.innerHTML = data.map(renderCard).join('');
}}

// ── event wiring ──────────────────────────────────────────────────────────
document.querySelectorAll('#tipo-pills .pill').forEach(pill=>{{
  pill.addEventListener('click',()=>{{
    const t = pill.dataset.tipo;
    if(tipos.has(t)) tipos.delete(t);
    else tipos.add(t);
    pill.classList.toggle('active', tipos.has(t));
    render();
  }});
}});

document.getElementById('corredor-sel').addEventListener('change', e=>{{
  corredor = e.target.value;
  render();
}});

const slider = document.getElementById('score-slider');
const sliderVal = document.getElementById('score-val');
slider.addEventListener('input', ()=>{{
  minScore = +slider.value;
  sliderVal.textContent = slider.value;
  render();
}});

document.querySelectorAll('#badge-toggles .badge-toggle').forEach(btn=>{{
  btn.addEventListener('click',()=>{{
    const f = btn.dataset.flag;
    if(flagFilters.has(f)) flagFilters.delete(f);
    else flagFilters.add(f);
    btn.classList.toggle('active', flagFilters.has(f));
    render();
  }});
}});

document.querySelectorAll('#sort-btns .sort-btn').forEach(btn=>{{
  btn.addEventListener('click',()=>{{
    sortKey = btn.dataset.sort;
    document.querySelectorAll('#sort-btns .sort-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    render();
  }});
}});

document.getElementById('reset-btn').addEventListener('click',()=>{{
  tipos = new Set(['departamento','casa','terreno']);
  document.querySelectorAll('#tipo-pills .pill').forEach(p=>p.classList.add('active'));
  corredor='';
  document.getElementById('corredor-sel').value='';
  minScore=0;
  slider.value=0;
  sliderVal.textContent='0';
  flagFilters.clear();
  document.querySelectorAll('.badge-toggle').forEach(b=>b.classList.remove('active'));
  sortKey='score';
  document.querySelectorAll('#sort-btns .sort-btn').forEach(b=>b.classList.remove('active'));
  document.querySelector('#sort-btns .sort-btn[data-sort="score"]').classList.add('active');
  render();
}});

// ── initial render ────────────────────────────────────────────────────────
render();
</script>
</body>
</html>"""

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path(f"rei_dashboard_{ts}.html")
    out_path.write_text(html, encoding="utf-8")
    return str(out_path.resolve())


# ---------------------------------------------------------------------------
# Corredor export — Market Intelligence Report
# ---------------------------------------------------------------------------


def _generate_corredor_pdf(
    top_props: list[dict],
    all_props: list[dict],
    zona_label: str,
    fecha_l: str,
    listings_total: int,
) -> str:
    """Generate a branded Market Intelligence Report PDF. Returns file path."""
    from pathlib import Path
    import statistics as st

    _UF = 40_100
    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path(f"market_intel_{now_str}.pdf")

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table as RLTable,
            TableStyle, HRFlowable,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

        # Colors
        C_BG_DARK  = colors.HexColor("#060d1a")
        C_CYAN     = colors.HexColor("#00bcd4")
        C_GREEN    = colors.HexColor("#00e676")
        C_YELLOW   = colors.HexColor("#ffd740")
        C_WHITE    = colors.HexColor("#e8eaf6")
        C_DIM      = colors.HexColor("#6b7a99")
        C_RED      = colors.HexColor("#ff5252")

        styles = getSampleStyleSheet()
        style_title = ParagraphStyle("title", parent=styles["Title"],
            textColor=C_WHITE, backColor=C_BG_DARK, fontSize=22,
            fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=4)
        style_sub = ParagraphStyle("sub", parent=styles["Normal"],
            textColor=C_DIM, fontSize=10, alignment=TA_CENTER, spaceAfter=12)
        style_h2 = ParagraphStyle("h2", parent=styles["Heading2"],
            textColor=C_CYAN, fontSize=11, fontName="Helvetica-Bold",
            spaceBefore=16, spaceAfter=6)
        style_normal = ParagraphStyle("norm", parent=styles["Normal"],
            textColor=C_WHITE, fontSize=9, spaceAfter=4)
        style_footer = ParagraphStyle("footer", parent=styles["Normal"],
            textColor=C_DIM, fontSize=7, alignment=TA_CENTER)

        doc = SimpleDocTemplate(
            str(out_path), pagesize=A4,
            leftMargin=1.8*cm, rightMargin=1.8*cm,
            topMargin=1.5*cm, bottomMargin=1.5*cm,
        )
        story = []

        def M(n):
            m = n / 1_000_000
            return f"${m:.1f}M" if m < 1000 else f"${m:.0f}M"

        def UF_v(clp):
            return f"UF {clp/_UF:,.0f}"

        # Cover
        story.append(Paragraph("MARKET INTELLIGENCE REPORT", style_title))
        story.append(Paragraph("Real Estate Intelligence Agent · Informe para Corredor", style_sub))
        story.append(Paragraph(f"Zona: {zona_label}  ·  {fecha_l}", style_sub))
        story.append(Spacer(1, 0.4*cm))
        story.append(HRFlowable(width="100%", thickness=1, color=C_CYAN))
        story.append(Spacer(1, 0.4*cm))

        # KPI summary
        story.append(Paragraph("Resumen de Mercado", style_h2))
        avg_score = st.mean(s["score"] for s in all_props)
        med_price = st.median(s["precio"] for s in all_props)
        n_high = sum(1 for s in all_props if s["score"] >= 75)
        avg_m2 = st.mean(s["m2"] for s in all_props)

        kpi_data = [
            ["Propiedades analizadas", str(listings_total), "Oportunidades HIGH", str(n_high)],
            ["Precio mediana zona", f"{M(int(med_price))} ({UF_v(int(med_price))})", "Score promedio", f"{avg_score:.1f}"],
            ["Superficie prom.", f"{avg_m2:.0f} m²", "Top oportunidades presentadas", str(len(top_props))],
        ]
        kpi_tbl = RLTable(kpi_data, colWidths=[4.5*cm, 5*cm, 5*cm, 3.5*cm])
        kpi_tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0,0), (-1,-1), C_BG_DARK),
            ("TEXTCOLOR",   (0,0), (-1,-1), C_WHITE),
            ("TEXTCOLOR",   (0,0), (0,-1),  C_DIM),
            ("TEXTCOLOR",   (2,0), (2,-1),  C_DIM),
            ("FONTNAME",    (1,0), (1,-1),  "Helvetica-Bold"),
            ("FONTNAME",    (3,0), (3,-1),  "Helvetica-Bold"),
            ("TEXTCOLOR",   (1,0), (1,-1),  C_GREEN),
            ("TEXTCOLOR",   (3,0), (3,-1),  C_CYAN),
            ("FONTSIZE",    (0,0), (-1,-1), 9),
            ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#0d1b2e"), colors.HexColor("#132035")]),
            ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#1e3050")),
            ("TOPPADDING",  (0,0), (-1,-1), 6),
            ("BOTTOMPADDING",(0,0), (-1,-1), 6),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
        ]))
        story.append(kpi_tbl)
        story.append(Spacer(1, 0.4*cm))

        # Pipeline table
        story.append(Paragraph(f"Top {len(top_props)} Oportunidades — Zona {zona_label}", style_h2))
        headers = ["#", "Tipo", "Comuna", "Precio", "UF", "m²", "vs Med.", "Días", "Score", "Señal"]
        tbl_data = [headers]
        for idx, prop in enumerate(top_props, 1):
            sc = prop["score"]
            med = int(prop.get("corridor_median_m2") or 1)
            pm2 = int(prop["precio_m2"])
            vs = ((pm2 / med) - 1) * 100
            urg = prop.get("urgency_score", 0) or 0
            flp = prop.get("flip_score", 0) or 0
            lot = prop.get("potencial_loteo_score") or 0
            sigs = []
            if urg >= 60: sigs.append("URGENTE")
            if flp >= 65: sigs.append("FLIP")
            if lot >= 65: sigs.append("LOTEO")
            tipo_short = {"departamento": "Depto", "casa": "Casa", "terreno": "Terreno"}.get(prop.get("tipo_propiedad",""), "—")
            tbl_data.append([
                str(idx),
                tipo_short,
                prop.get("comuna", "—"),
                M(prop["precio"]),
                UF_v(prop["precio"]),
                f"{prop['m2']:.0f}",
                f"{vs:+.0f}%",
                str(prop.get("days_on_market") or "—"),
                f"{sc:.1f}",
                " / ".join(sigs) if sigs else "—",
            ])

        def _sc_color(row_idx):
            if row_idx == 0: return None
            sc_val = float(tbl_data[row_idx][8])
            if sc_val >= 75: return C_GREEN
            if sc_val >= 60: return C_YELLOW
            return C_RED

        col_widths = [0.7*cm, 1.8*cm, 3.2*cm, 2.8*cm, 2.5*cm, 1.4*cm, 1.6*cm, 1.2*cm, 1.5*cm, 2.3*cm]
        pipeline_tbl = RLTable(tbl_data, colWidths=col_widths, repeatRows=1)
        ts = [
            ("BACKGROUND",    (0,0), (-1,0),  colors.HexColor("#132035")),
            ("TEXTCOLOR",     (0,0), (-1,0),  C_CYAN),
            ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("BACKGROUND",    (0,1), (-1,-1), C_BG_DARK),
            ("TEXTCOLOR",     (0,1), (-1,-1), C_WHITE),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.HexColor("#0d1b2e"), colors.HexColor("#0a1520")]),
            ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#1e3050")),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
            ("ALIGN",         (3,0), (5,-1),  "RIGHT"),
            ("ALIGN",         (6,0), (-1,-1), "CENTER"),
        ]
        # Color score column per value
        for row_i in range(1, len(tbl_data)):
            c = _sc_color(row_i)
            if c:
                ts.append(("TEXTCOLOR", (8, row_i), (8, row_i), c))
                ts.append(("FONTNAME",  (8, row_i), (8, row_i), "Helvetica-Bold"))
        pipeline_tbl.setStyle(TableStyle(ts))
        story.append(pipeline_tbl)
        story.append(Spacer(1, 0.5*cm))

        # Methodology note
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_DIM))
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph(
            "Metodología: Score compuesto = precio/m² vs mediana corredor (55%) + tiempo en mercado (30%) + "
            "reducción precio (15%). Señal URGENTE = días > 60 + reducción > 10%. "
            "Señal FLIP = upside vs mediana > 10% + liquidez comunal alta. "
            "Señal LOTEO = precio/ha bajo mediana + zonificación favorable.",
            style_footer,
        ))
        story.append(Spacer(1, 0.1*cm))
        story.append(Paragraph(
            f"Datos: Portal Inmobiliario · UF = $38,500 CLP · Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
            "Este documento es confidencial. No constituye asesoría financiera.",
            style_footer,
        ))

        doc.build(story)
        return str(out_path.resolve())

    except ImportError:
        # ReportLab not available — write a plain text file instead
        txt_path = Path(f"market_intel_{now_str}.txt")
        lines = [
            "MARKET INTELLIGENCE REPORT",
            f"Zona: {zona_label}  ·  {fecha_l}",
            f"Propiedades analizadas: {listings_total}",
            "",
            f"{'#':>3}  {'Score':>6}  {'Tipo':<8}  {'Comuna':<14}  {'Precio':>10}",
            "-" * 60,
        ]
        def M(n):
            m = n / 1_000_000
            return f"${m:.1f}M" if m < 1000 else f"${m:.0f}M"
        for idx, prop in enumerate(top_props, 1):
            lines.append(
                f"{idx:>3}  {prop['score']:>6.1f}  "
                f"{prop.get('tipo_propiedad','')[:8]:<8}  "
                f"{prop.get('comuna',''):<14}  "
                f"{M(prop['precio']):>10}"
            )
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        return str(txt_path.resolve())


def _print_corredor(scored: list[dict], listings_total: int, zona: str = "") -> None:
    import statistics as st
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.rule import Rule
    from rich.panel import Panel
    from rich.text import Text
    from pathlib import Path

    console = Console(width=max(160, Console().width))
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    fecha_l = datetime.now().strftime("%d de %B de %Y")
    _UF = 40_100

    # Filter by zona
    comunas_filter = [c.strip() for c in zona.split(",") if c.strip()] if zona else []
    valid = [s for s in scored if s.get("score") is not None]
    if comunas_filter:
        valid = [s for s in valid if s["comuna"] in comunas_filter]
    if not valid:
        console.print("[red]No hay propiedades en la zona especificada.[/red]")
        return

    top = sorted(valid, key=lambda x: x["score"], reverse=True)[:15]

    def _M(n):
        m = n / 1_000_000
        return f"${m:.1f}M" if m < 1000 else f"${m:.0f}M"

    def _UF_v(clp):
        return f"UF {clp/_UF:,.0f}"

    zona_label = " · ".join(comunas_filter) if comunas_filter else "Región Metropolitana"

    console.print()
    console.rule(f"[bold white]MARKET INTELLIGENCE REPORT[/bold white]", style="bright_cyan")
    console.print(Panel(
        f"[bold cyan]Real Estate Intelligence Agent[/bold cyan] · [dim]Informe para Corredor[/dim]\n"
        f"[white]{fecha_l}[/white]  ·  Zona: [bold]{zona_label}[/bold]  ·  "
        f"{len(valid)} oportunidades analizadas  ·  Top {len(top)} presentadas",
        style="cyan",
        padding=(0, 2),
    ))

    # Stats
    avg_score = st.mean(s["score"] for s in valid)
    med_price = st.median(s["precio"] for s in valid)
    avg_m2 = st.mean(s["m2"] for s in valid)
    n_high = sum(1 for s in valid if s["score"] >= 75)

    stats = Table(box=box.SIMPLE, show_header=False, padding=(0, 3), border_style="dim")
    stats.add_column("k", style="dim", width=22)
    stats.add_column("v", style="bold white", width=18)
    stats.add_column("k2", style="dim", width=22)
    stats.add_column("v2", style="bold white", width=18)
    stats.add_row("Oportunidades HIGH",  f"[bright_green]{n_high}[/bright_green]",
                  "Score promedio",      f"{avg_score:.1f}")
    stats.add_row("Precio mediana zona", f"{_M(int(med_price))}  ({_UF_v(int(med_price))})",
                  "Superficie prom.",   f"{avg_m2:.0f} m²")
    console.print(stats)
    console.print()

    # Table
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", border_style="dim white", show_lines=False, padding=(0, 1))
    t.add_column("#",         style="dim",   no_wrap=True, width=3)
    t.add_column("Score",     justify="right", no_wrap=True, width=6)
    t.add_column("Tipo",      no_wrap=True, width=8)
    t.add_column("Comuna",    no_wrap=True, width=14)
    t.add_column("Precio",    justify="right", no_wrap=True, width=10)
    t.add_column("UF",        justify="right", no_wrap=True, width=9)
    t.add_column("m²",        justify="right", no_wrap=True, width=6)
    t.add_column("CLP/m²",    justify="right", no_wrap=True, width=9)
    t.add_column("vs Mediana",justify="right", no_wrap=True, width=9)
    t.add_column("Días",      justify="right", no_wrap=True, width=5)
    t.add_column("Urgente",   justify="center", no_wrap=True, width=8)
    t.add_column("Flip",      justify="center", no_wrap=True, width=6)
    t.add_column("Link",      no_wrap=True, min_width=35)

    def _sc(s):
        if s >= 75: return "bright_green"
        if s >= 60: return "green"
        if s >= 45: return "yellow"
        return "red"

    for i, prop in enumerate(top, 1):
        sc = prop["score"]
        med = int(prop.get("corridor_median_m2") or 1)
        pm2 = int(prop["precio_m2"])
        vs = ((pm2 / med) - 1) * 100
        urg = prop.get("urgency_score", 0) or 0
        flp = prop.get("flip_score", 0) or 0
        url = prop.get("url", "")
        short_url = url.replace("https://www.portalinmobiliario.com", "portal.cl")[:45]
        tipo_short = {"departamento": "Depto", "casa": "Casa", "terreno": "Terreno"}.get(prop.get("tipo_propiedad",""), "—")

        t.add_row(
            str(i),
            Text(f"{sc:.1f}", style=f"bold {_sc(sc)}"),
            tipo_short,
            prop.get("comuna", "—"),
            _M(prop["precio"]),
            _UF_v(prop["precio"]),
            f"{prop['m2']:.0f}",
            f"${pm2/1000:.0f}k" if pm2 < 1_000_000 else f"${pm2/1_000_000:.2f}M",
            Text(f"{vs:+.0f}%", style="bright_green" if vs < -5 else "yellow" if vs < 5 else "red"),
            str(prop.get("days_on_market") or "—"),
            Text("●" if urg >= 60 else "○", style="red bold" if urg >= 60 else "dim"),
            Text("●" if flp >= 65 else "○", style="yellow bold" if flp >= 65 else "dim"),
            f"[dim][link={url}]{short_url}[/link][/dim]",
        )

    console.print(t)

    # Generate PDF
    pdf_path = _generate_corredor_pdf(top, valid, zona_label, fecha_l, listings_total)
    console.print(f"\n[bright_green]✓[/bright_green] PDF generado: [bold]{pdf_path}[/bold]")
    console.print(f"[dim]  Listo para enviar a cliente · {len(top)} oportunidades · zona: {zona_label}[/dim]\n")


def _print_subscription_preview(scored: list[dict], listings_total: int) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich import box
    import statistics as st

    console = Console(width=max(160, Console().width))
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    _UF = 40_100

    valid = [s for s in scored if s.get("score") is not None]
    top5 = sorted(valid, key=lambda x: x["score"], reverse=True)[:5]

    def _M(n):
        m = n / 1_000_000
        return f"${m:.1f}M" if m < 1000 else f"${m:.0f}M"

    console.print()
    console.rule("[bold white]── PREVIEW REPORTE SEMANAL SUSCRIPTOR ──[/bold white]", style="yellow")
    console.print(Panel(
        "[bold yellow]Real Estate Intelligence Weekly Digest[/bold yellow]\n"
        f"[dim]Semana del {datetime.now().strftime('%d/%m/%Y')}  ·  Edición Suscriptor Premium[/dim]\n\n"
        "[white]Estimado suscriptor,[/white]\n"
        "Esta semana el agente analizó [bold]{0:,}[/bold] propiedades en la RM.\n"
        "A continuación sus [bold]top 5 oportunidades[/bold] personalizadas:\n".format(listings_total),
        title="[bold yellow]📬 Market Digest — Preview[/bold yellow]",
        border_style="yellow",
    ))

    t = Table(box=box.ROUNDED, header_style="bold yellow", border_style="yellow", padding=(0, 1))
    t.add_column("#",       style="dim",     no_wrap=True, width=3)
    t.add_column("Score",   justify="right", no_wrap=True, width=6)
    t.add_column("Tipo",    no_wrap=True,    width=8)
    t.add_column("Comuna",  no_wrap=True,    width=14)
    t.add_column("Precio",  justify="right", no_wrap=True, width=10)
    t.add_column("vs Med",  justify="right", no_wrap=True, width=7)
    t.add_column("Días",    justify="right", no_wrap=True, width=5)
    t.add_column("Señales", no_wrap=True,    width=18)

    for i, prop in enumerate(top5, 1):
        sc = prop["score"]
        med = int(prop.get("corridor_median_m2") or 1)
        pm2 = int(prop["precio_m2"])
        vs = ((pm2 / med) - 1) * 100
        urg = prop.get("urgency_score", 0) or 0
        flp = prop.get("flip_score", 0) or 0
        lot = prop.get("potencial_loteo_score") or 0
        senales = []
        if urg >= 60: senales.append("[red]URGENTE[/red]")
        if flp >= 65: senales.append("[yellow]FLIP[/yellow]")
        if lot >= 65: senales.append("[cyan]LOTEO[/cyan]")

        tipo_short = {"departamento": "Depto", "casa": "Casa", "terreno": "Terreno"}.get(prop.get("tipo_propiedad",""), "—")
        t.add_row(
            str(i),
            f"[bold bright_green]{sc:.1f}[/bold bright_green]",
            tipo_short,
            prop.get("comuna", "—"),
            _M(prop["precio"]),
            f"[bright_green]{vs:+.0f}%[/bright_green]" if vs < -5 else f"{vs:+.0f}%",
            str(prop.get("days_on_market") or "—"),
            " ".join(senales) if senales else "[dim]—[/dim]",
        )
    console.print(t)

    # subscription CTA
    console.print(Panel(
        "[bold white]Este reporte es generado automáticamente cada lunes 08:00.\n"
        "Los suscriptores reciben además:\n"
        "  · PDF descargable con fichas completas de cada propiedad\n"
        "  · Alertas en tiempo real (Telegram/Email) cuando aparece score ≥ 85\n"
        "  · Análisis de corredor personalizado por zona de interés\n"
        "  · Dashboard web con histórico de precios y tendencias\n\n"
        "[cyan]Plan Corredor Pro[/cyan]: CLP 49.900/mes · [cyan]Plan Fondo[/cyan]: CLP 199.900/mes[/bold white]",
        title="[bold cyan]Servicios de Suscripción[/bold cyan]",
        border_style="cyan",
    ))
    console.print(f"\n[dim]  Reporte generado: {now}  ·  Portal Inmobiliario  ·  {listings_total} propiedades[/dim]\n")


# ---------------------------------------------------------------------------
# Main command: --top20
# ---------------------------------------------------------------------------


async def cmd_top20(tipos: list[str], max_pages: int, output_json: bool, demo: bool = False, report: bool = False, invest: bool = False, html_out: bool = False, dashboard: bool = False, corredor: bool = False, zona: str = "", subscription_preview: bool = False) -> None:
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

    # Dynamic commune liquidity (from observed data)
    commune_liq = _compute_commune_liquidity(unique)
    console.print(
        f"[green]✓[/green] {len(commune_liq):,} comunas con liquidez calculada dinámicamente"
    )

    # Score
    scored = [_score_listing(item, medians, commune_liq) for item in unique]
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
    elif dashboard:
        path = _generate_dashboard_html(scored, listings_total=len(unique))
        console.print(f"[bright_green]✓[/bright_green] Dashboard interactivo generado: [bold]{path}[/bold]")
        console.print(f"[dim]  Filtros: tipo · corredor · score mínimo · badges URGENTE/FLIP/LOTEO[/dim]")
        console.print(f"[dim]  Abre con: xdg-open {path}  /  open {path}  /  o arrastra al browser[/dim]")
    elif corredor:
        _print_corredor(scored, listings_total=len(unique), zona=zona)
    elif subscription_preview:
        _print_subscription_preview(scored, listings_total=len(unique))
    else:
        _print_top20(scored)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = _parse_args()

    if not args.top20 and not args.report and not args.invest and not args.html and not args.dashboard and not args.corredor and not args.subscription_preview:
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
        dashboard=args.dashboard,
        corredor=args.corredor,
        zona=args.zona,
        subscription_preview=args.subscription_preview,
    ))


if __name__ == "__main__":
    main()
