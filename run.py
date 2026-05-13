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
# Main command: --top20
# ---------------------------------------------------------------------------


async def cmd_top20(tipos: list[str], max_pages: int, output_json: bool, demo: bool = False) -> None:
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
    else:
        _print_top20(scored)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = _parse_args()

    if not args.top20:
        print(__doc__)
        sys.exit(0)

    asyncio.run(cmd_top20(
        tipos=args.tipos,
        max_pages=args.pages,
        output_json=args.output_json,
        demo=args.demo,
    ))


if __name__ == "__main__":
    main()
