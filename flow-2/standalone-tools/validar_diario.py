#!/usr/bin/env python3
"""validar_diario — captura y valida HTML real del buscador del Diario Oficial.

Se corre **desde tu PC** (con acceso a los endpoints chilenos), no desde el
entorno remoto. Hace dos cosas:

  1. Baja el HTML de una búsqueda (o lee uno que guardaste a mano con el
     navegador: "Guardar como > Página web, solo HTML").
  2. Le corre el parser real del scraper y muestra qué publicaciones extrajo,
     para validar de inmediato si los selectores aguantan contra el markup real.

Si el parser extrae 0 publicaciones de una página que sí muestra resultados,
los selectores de diario_oficial.py necesitan ajuste: guardá el HTML con
--save, pegá un fragmento del markup real en el repo/chat y se ajustan.

Ejemplos:
  # Bajar la búsqueda de un término y ver qué parsea
  python validar_diario.py --termino "5678-90"

  # Igual, guardando el HTML como fixture del scraper
  python validar_diario.py --termino "5678-90" --save

  # Validar contra un HTML que guardaste a mano desde el navegador
  python validar_diario.py --archivo ~/Descargas/busqueda.html --termino "5678-90" --save
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app.scrapers.diario_oficial import (  # noqa: E402
    BUSCADOR_URL,
    DiarioOficialScraper,
)

FIXTURES = ROOT / "backend" / "app" / "scrapers" / "fixtures" / "diario_oficial"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Capturar y validar HTML real del buscador del Diario Oficial"
    )
    p.add_argument("--termino", help="Término a buscar (rol, RUT o dirección)")
    p.add_argument("--archivo", help="HTML ya guardado a mano, en vez de bajarlo")
    p.add_argument("--url", default=BUSCADOR_URL, help=f"URL del buscador (default: {BUSCADOR_URL})")
    p.add_argument("--save", action="store_true", help="Guardar el HTML como fixture del scraper")
    args = p.parse_args()

    if args.archivo:
        html = Path(args.archivo).expanduser().read_text(encoding="utf-8")
        origen = args.archivo
    elif args.termino:
        import requests

        r = requests.get(
            args.url,
            params={"q": args.termino},
            headers={"User-Agent": "flow-2-dd/0.1 (+due-diligence)"},
            timeout=30,
        )
        r.raise_for_status()
        html = r.text
        origen = r.url
    else:
        p.error("Pasá --termino (para bajar la búsqueda) o --archivo (HTML guardado).")

    pubs = DiarioOficialScraper._parse(html)
    print(f"Origen: {origen}")
    print(f"El parser extrajo {len(pubs)} publicación(es):\n")
    for pub in pubs:
        print(f"  [{pub['tipo']}] {pub['fecha']} — {pub['titulo']}")
        if pub["url"]:
            print(f"      {pub['url']}")
    if not pubs:
        print("  (nada — si la página SÍ muestra resultados, hay que ajustar")
        print("   los selectores de diario_oficial.py contra este HTML)\n")
        # Snippet del HTML real para diagnosticar: ¿estamos en el buscador correcto
        # o el endpoint nos devolvió otra cosa (login, error, edición del día)?
        muestra = " ".join(html.split())[:600]
        print(f"  Primeros 600 chars del HTML recibido:\n  {muestra}")

    if args.save:
        if not args.termino:
            p.error("--save necesita --termino para nombrar el fixture.")
        FIXTURES.mkdir(parents=True, exist_ok=True)
        # fixture_path aplica la misma sanitización que usa fetch() al leer.
        destino = DiarioOficialScraper().fixture_path(args.termino, "html")
        destino.write_text(html, encoding="utf-8")
        print(f"\nFixture guardado en {destino}")


if __name__ == "__main__":
    sys.exit(main())
