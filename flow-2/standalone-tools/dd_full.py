#!/usr/bin/env python3
"""dd_full — genera un reporte de Due Diligence a partir de un JSON de propiedad.

Uso:
  python dd_full.py ejemplos/las_condes_verde.json
  python dd_full.py ejemplos/maipu_rojo_remate.json --abrir
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

# Permitir importar el backend cuando se corre desde standalone-tools/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app.enrich import enriquecer_propiedad  # noqa: E402
from backend.app.scoring.engine import Resultado, evaluar_propiedad  # noqa: E402

COLORES_TERMINAL = {"VERDE": "\033[92m", "AMARILLO": "\033[93m", "ROJO": "\033[91m"}
RESET = "\033[0m"
COLORES_HTML = {"VERDE": "#1e8e3e", "AMARILLO": "#f9a825", "ROJO": "#d93025"}


def cargar_propiedad(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def imprimir_terminal(propiedad: dict, resultado: Resultado) -> None:
    color = COLORES_TERMINAL.get(resultado.score, "")
    print()
    print(f"  {propiedad.get('direccion', '(sin dirección)')}")
    print(f"  Rol {propiedad.get('rol', '?')} — {propiedad.get('comuna', '?')}")
    print(f"  Score: {color}{resultado.score}{RESET}")
    if resultado.observaciones:
        print("  Observaciones:")
        for o in resultado.observaciones:
            c = COLORES_TERMINAL.get(o.severidad.upper(), "")
            print(f"    - [{c}{o.severidad.upper()}{RESET}] {o.mensaje}")
    else:
        print("  Sin observaciones.")
    fuentes = propiedad.get("_fuentes")
    if fuentes:
        print("  Fuentes:")
        for nombre, estado in fuentes.items():
            print(f"    - {nombre}: {estado}")
    print()


def _fila(label: str, valor) -> str:
    if valor in (None, "", [], {}):
        valor = "—"
    return (
        f"<tr><th>{html_lib.escape(str(label))}</th>"
        f"<td>{html_lib.escape(str(valor))}</td></tr>"
    )


def generar_html(propiedad: dict, resultado: Resultado) -> str:
    color = COLORES_HTML.get(resultado.score, "#5f6368")
    cip = propiedad.get("cip", {}) or {}
    sii = propiedad.get("sii", {}) or {}
    propietario = propiedad.get("propietario", {}) or {}
    usos = ", ".join(cip.get("uso_permitido", [])) if cip.get("uso_permitido") else None

    if resultado.observaciones:
        items = ""
        for o in resultado.observaciones:
            c = COLORES_HTML.get(o.severidad.upper(), "#5f6368")
            items += (
                f'<li><span class="badge" style="background:{c}">'
                f"{html_lib.escape(o.severidad.upper())}</span>"
                f"<span>{html_lib.escape(o.mensaje)}</span></li>"
            )
        obs_html = f'<ul class="obs">{items}</ul>'
    else:
        obs_html = (
            '<p class="ok">Sin observaciones. La propiedad no gatilla banderas '
            "de riesgo según las reglas vigentes.</p>"
        )

    fuentes = propiedad.get("_fuentes") or {}
    if fuentes:
        filas = ""
        for nombre, estado in fuentes.items():
            ok = str(estado) == "ok"
            badge = "#1e8e3e" if ok else "#f9a825"
            etiqueta = "OK" if ok else "PENDIENTE"
            detalle = "" if ok else html_lib.escape(str(estado))
            filas += (
                f'<li><span class="badge" style="background:{badge}">{etiqueta}</span>'
                f"<span><strong>{html_lib.escape(nombre)}</strong>"
                f"{' — ' + detalle if detalle else ''}</span></li>"
            )
        fuentes_html = f"""  <section>
    <h2>Fuentes consultadas</h2>
    <ul class="obs">{filas}</ul>
  </section>
"""
    else:
        fuentes_html = ""

    generado = datetime.now().strftime("%Y-%m-%d %H:%M")
    direccion = html_lib.escape(str(propiedad.get("direccion", "(sin dirección)")))
    rol = html_lib.escape(str(propiedad.get("rol", "?")))
    comuna = html_lib.escape(str(propiedad.get("comuna", "?")))

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DD — {direccion}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, system-ui, "Segoe UI", Roboto, Arial, sans-serif;
    margin: 0; background: #f5f6f8; color: #202124; }}
  .wrap {{ max-width: 820px; margin: 32px auto; background: #fff;
    border-radius: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.12); overflow: hidden; }}
  header {{ display: flex; justify-content: space-between; align-items: center;
    gap: 16px; padding: 28px 32px; border-bottom: 1px solid #eceef1; }}
  header h1 {{ font-size: 22px; margin: 0; }}
  .sub {{ margin: 6px 0 0; color: #5f6368; font-size: 14px; }}
  .score {{ color: #fff; font-weight: 700; letter-spacing: .5px; font-size: 18px;
    padding: 12px 22px; border-radius: 999px; white-space: nowrap; }}
  section {{ padding: 22px 32px; border-bottom: 1px solid #f1f2f4; }}
  section h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .6px;
    color: #5f6368; margin: 0 0 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{ text-align: left; color: #5f6368; font-weight: 500; width: 46%;
    padding: 7px 0; vertical-align: top; }}
  td {{ padding: 7px 0; }}
  .obs {{ list-style: none; margin: 0; padding: 0; }}
  .obs li {{ display: flex; gap: 12px; align-items: flex-start; padding: 10px 0;
    border-bottom: 1px solid #f4f5f6; font-size: 14px; line-height: 1.5; }}
  .obs li:last-child {{ border-bottom: 0; }}
  .badge {{ color: #fff; font-size: 11px; font-weight: 700; letter-spacing: .4px;
    padding: 3px 9px; border-radius: 999px; flex-shrink: 0; }}
  .ok {{ color: #1e8e3e; font-size: 14px; margin: 0; }}
  footer {{ padding: 18px 32px; color: #9aa0a6; font-size: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>{direccion}</h1>
      <p class="sub">Rol {rol} · {comuna}</p>
    </div>
    <div class="score" style="background:{color}">{html_lib.escape(resultado.score)}</div>
  </header>
  <section>
    <h2>Identificación</h2>
    <table>
      {_fila("Propietario", propietario.get("nombre"))}
      {_fila("RUT", propietario.get("rut"))}
      {_fila("Comuna", propiedad.get("comuna"))}
      {_fila("Rol SII", propiedad.get("rol"))}
      {_fila("Avalúo fiscal (UF)", sii.get("avaluo_fiscal_uf"))}
      {_fila("Destino SII", sii.get("destino"))}
    </table>
  </section>
  <section>
    <h2>Información territorial (CIP)</h2>
    <table>
      {_fila("Zona", cip.get("zona"))}
      {_fila("Altura máxima (m)", cip.get("altura_maxima_m"))}
      {_fila("Coef. ocupación de suelo", cip.get("coef_ocupacion_suelo"))}
      {_fila("Coef. constructibilidad", cip.get("coef_constructibilidad"))}
      {_fila("Usos permitidos", usos)}
    </table>
  </section>
  <section>
    <h2>Observaciones</h2>
    {obs_html}
  </section>
{fuentes_html}  <footer>Generado por flow-2 · dd_full · {generado} · Reporte preliminar,
  no constituye asesoría legal.</footer>
</div>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reporte de Due Diligence inmobiliaria"
    )
    parser.add_argument("propiedad", help="Ruta al JSON de la propiedad (o seed con --enrich)")
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Tratar el input como seed y completarlo con los scrapers antes de evaluar",
    )
    parser.add_argument(
        "--abrir", action="store_true", help="Abrir el reporte HTML en el navegador"
    )
    parser.add_argument(
        "--output", "-o", default="output", help="Directorio de salida (default: output/)"
    )
    args = parser.parse_args()

    propiedad = cargar_propiedad(args.propiedad)
    if args.enrich:
        propiedad = enriquecer_propiedad(propiedad)
    resultado = evaluar_propiedad(propiedad)
    imprimir_terminal(propiedad, resultado)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dd_{Path(args.propiedad).stem}.html"
    out_path.write_text(generar_html(propiedad, resultado), encoding="utf-8")
    print(f"  Reporte: {out_path.resolve()}")

    if args.abrir:
        webbrowser.open(out_path.resolve().as_uri())


if __name__ == "__main__":
    main()
