"""
Real Estate Intelligence Agent — CLI entry point.

Commands:
  python run.py --top20               Scrape Portal RM now, score, print top 20
  python run.py --top20 --tipos dep   Only departamentos (dep|casa|terreno)
  python run.py --top20 --pages 3     Limit pages per tipo (default 5)
  python run.py --top20 --json        Output raw JSON instead of table

All operations run in-memory — no database required.
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

    console = Console()

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

    t = Table(
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        border_style="dim white",
        show_lines=False,
    )
    t.add_column("#",            style="dim",       width=3)
    t.add_column("Score",        justify="right",   width=7)
    t.add_column("Tipo",         width=12)
    t.add_column("Comuna",       width=16)
    t.add_column("Precio CLP",   justify="right",   width=15)
    t.add_column("CLP/m²",       justify="right",   width=10)
    t.add_column("m²",           justify="right",   width=6)
    t.add_column("Med. m²",      justify="right",   width=10)
    t.add_column("vs Med.",      justify="right",   width=8)
    t.add_column("Días",         justify="right",   width=6)
    t.add_column("Link",         min_width=30)

    def _score_color(s: float) -> str:
        if s >= 75: return "bright_green"
        if s >= 60: return "green"
        if s >= 45: return "yellow"
        return "red"

    for i, prop in enumerate(top, 1):
        score = prop["score"]
        precio_m2 = prop["precio_m2"]
        median = prop.get("corridor_median_m2") or 1
        vs_median = ((precio_m2 / median) - 1) * 100

        vs_text = Text(
            f"{vs_median:+.0f}%",
            style="bright_green" if vs_median < -5 else "yellow" if vs_median < 5 else "red",
        )

        url = prop.get("url", "")
        short_url = url.replace("https://www.portalinmobiliario.com", "portal.cl")[:45]

        t.add_row(
            str(i),
            Text(f"{score:.1f}", style=f"bold {_score_color(score)}"),
            prop.get("tipo_propiedad", "—").title(),
            prop.get("comuna", "—"),
            f"${prop['precio']:>14,.0f}".replace(",", "."),
            f"${precio_m2:>8,.0f}".replace(",", "."),
            f"{prop['m2']:.0f}",
            f"${int(median):>7,}".replace(",", "."),
            vs_text,
            str(prop.get("days_on_market") or "—"),
            f"[link={url}]{short_url}[/link]",
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
# Main command: --top20
# ---------------------------------------------------------------------------


async def cmd_top20(tipos: list[str], max_pages: int, output_json: bool) -> None:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

    console = Console()

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

        all_raw: list[dict] = []
        try:
            all_raw = await scrape_rm_full(tipos=tipos, max_pages=max_pages)
        except Exception as exc:
            console.print(f"\n[red]Error scrapeando: {exc}[/red]")
            console.print("[yellow]Tip: verifica conectividad y que Portal Inmobiliario esté accesible.[/yellow]")
            sys.exit(1)
        finally:
            progress.advance(task, len(tipos))

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
    ))


if __name__ == "__main__":
    main()
