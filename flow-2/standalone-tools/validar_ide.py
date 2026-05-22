#!/usr/bin/env python3
"""validar_ide — descubre y captura datos reales del WFS de IDE Chile / MINVU.

Esta herramienta se corre **desde tu PC** (que sí tiene acceso a los endpoints
chilenos), no desde el entorno remoto. Hace tres cosas:

  1. --capabilities : lista las capas (FeatureType) que publica el servidor WFS,
     para que encuentres el typename real de la zonificación de tu comuna.
  2. consulta un punto (lat/lon) sobre un typename y muestra las propiedades que
     devuelve, para mapearlas a los campos del CIP en config/layers.yml.
  3. --save : guarda la respuesta GeoJSON como fixture en
     fixtures/ide_chile/<lat>_<lon>.json, que es lo que consume el scraper.

Contexto (mayo 2026): el dato de zonificación lo publica el Geoportal Open Data
MINVU (ide.minvu.cl), que es GeoNode. Las capas son POR COMUNA (ej. el visor
expone "MINVU::prc-las-condes-3"), no una sola capa nacional. Usá --capabilities
para encontrar el typename exacto de la comuna que te interesa.

Ejemplos:
  # 1) ¿Qué capas hay? (confirmá la URL del servidor con tu navegador primero)
  python validar_ide.py --capabilities --url https://ide.minvu.cl/geoserver/ows

  # 2) Consultar un punto sobre una capa y ver sus propiedades
  python validar_ide.py --typename geonode:prc_las_condes --lat -33.4096 --lon -70.5681

  # 3) Igual que arriba pero guardando el fixture real
  python validar_ide.py --typename geonode:prc_las_condes --lat -33.4096 --lon -70.5681 --save
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "backend" / "app" / "scrapers" / "config" / "layers.yml"
FIXTURES = ROOT / "backend" / "app" / "scrapers" / "fixtures" / "ide_chile"
HEADERS = {"User-Agent": "flow-2-dd/0.1 (+due-diligence)"}


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def listar_capabilities(url: str) -> None:
    params = {"service": "WFS", "version": "2.0.0", "request": "GetCapabilities"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    # WFS usa namespaces; buscamos FeatureType sin atarnos al prefijo.
    fts = [e for e in root.iter() if e.tag.endswith("}FeatureType") or e.tag == "FeatureType"]
    if not fts:
        print("No se encontraron FeatureType. ¿La URL es un WFS válido?")
        print(r.text[:500])
        return
    print(f"{len(fts)} capas publicadas en {url}:\n")
    for ft in fts:
        name = title = ""
        for hijo in ft:
            tag = hijo.tag.split("}")[-1]
            if tag == "Name":
                name = (hijo.text or "").strip()
            elif tag == "Title":
                title = (hijo.text or "").strip()
        print(f"  {name}\n      {title}")


def consultar_punto(url: str, typename: str, lat: float, lon: float, geom: str, bbox: bool):
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": typename,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "count": "5",
    }
    if bbox:
        d = 0.0005  # ~50 m
        params["bbox"] = f"{lat - d},{lon - d},{lat + d},{lon + d},EPSG:4326"
    else:
        params["cql_filter"] = f"INTERSECTS({geom}, POINT({lon} {lat}))"
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> None:
    p = argparse.ArgumentParser(description="Descubrir y capturar datos del WFS de IDE Chile / MINVU")
    p.add_argument("--url", help="URL del WFS (default: wfs_base_url de layers.yml)")
    p.add_argument("--capabilities", action="store_true", help="Listar las capas del servidor y salir")
    p.add_argument("--typename", help="Capa a consultar (ej. geonode:prc_las_condes)")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--geom", default=None, help="Campo de geometría (default: geom_field de layers.yml)")
    p.add_argument("--bbox", action="store_true", help="Consultar por bounding box en vez de INTERSECTS")
    p.add_argument("--save", action="store_true", help="Guardar la respuesta como fixture")
    args = p.parse_args()

    cfg = _config()
    url = args.url or cfg.get("wfs_base_url")
    geom = args.geom or cfg.get("geom_field", "geom")

    if args.capabilities:
        listar_capabilities(url)
        return

    if not (args.typename and args.lat is not None and args.lon is not None):
        p.error("Para consultar un punto pasá --typename, --lat y --lon (o usá --capabilities).")

    gj = consultar_punto(url, args.typename, args.lat, args.lon, geom, args.bbox)
    feats = gj.get("features", [])
    print(f"{len(feats)} feature(s) en ({args.lat}, {args.lon}) sobre {args.typename}\n")
    if feats:
        props = feats[0].get("properties", {})
        print("Propiedades del primer feature (mapealas en config/layers.yml -> campos):")
        for k, v in props.items():
            print(f"  {k}: {v!r}")

    if args.save:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        destino = FIXTURES / f"{args.lat}_{args.lon}.json"
        destino.write_text(json.dumps(gj, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nFixture guardado en {destino}")
        print("Ahora el scraper en modo fixture devuelve este dato para esa lat/lon.")


if __name__ == "__main__":
    sys.exit(main())
