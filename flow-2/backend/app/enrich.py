"""Enriquecimiento de una propiedad a partir de las fuentes.

Toma un *seed* (datos mínimos que tenés a mano) y completa las secciones del
JSON de propiedad consultando cada scraper. Cada fuente se aísla: si falla o
necesita captura asistida, se registra en ``_fuentes`` y la DD sigue corriendo
con lo que haya (el motor de scoring ya trata los datos ausentes con cuidado).

Seed mínimo esperado:
  {
    "rol": "1234-56",
    "comuna": "Las Condes",
    "direccion": "...",
    "propietario": {"rut": "76.123.456-7"},
    "lat": -33.4096,
    "lon": -70.5681
  }
"""
from __future__ import annotations

import copy
from typing import Callable

from .scrapers.base import AssistedCaptureRequired
from .scrapers.conservador import ConservadorScraper
from .scrapers.diario_oficial import DiarioOficialScraper
from .scrapers.ide_chile import IDEChileScraper
from .scrapers.registro_civil import RegistroCivilScraper
from .scrapers.sii import SIIScraper


def _aplicar(prop: dict, seccion: str, fn: Callable[[], dict]) -> None:
    try:
        data = fn()
        if isinstance(prop.get(seccion), dict) and isinstance(data, dict):
            prop[seccion].update(data)
        else:
            prop[seccion] = data
        prop["_fuentes"][seccion] = "ok"
    except AssistedCaptureRequired as e:
        prop["_fuentes"][seccion] = f"pendiente: {e}"
    except FileNotFoundError as e:
        prop["_fuentes"][seccion] = f"sin_fixture: {e}"
    except Exception as e:  # noqa: BLE001 - aislar cada fuente
        prop["_fuentes"][seccion] = f"error: {type(e).__name__}: {e}"


def enriquecer_propiedad(seed: dict) -> dict:
    prop = copy.deepcopy(seed)
    prop.setdefault("_fuentes", {})

    rol = prop.get("rol")
    comuna = prop.get("comuna")
    rut = (prop.get("propietario") or {}).get("rut")
    lat, lon = prop.get("lat"), prop.get("lon")

    if rol:
        _aplicar(prop, "sii", lambda: SIIScraper().fetch(rol=rol, comuna=comuna))
        _aplicar(prop, "conservador", lambda: ConservadorScraper().fetch(rol=rol))
        _aplicar(
            prop,
            "diario_oficial",
            lambda: DiarioOficialScraper().fetch(termino=rol),
        )
    if rut:
        _aplicar(
            prop,
            "registro_civil",
            lambda: RegistroCivilScraper().fetch(rut=rut),
        )
    if lat is not None and lon is not None:
        _aplicar(
            prop,
            "cip",
            lambda: IDEChileScraper().fetch(lat=lat, lon=lon, comuna=comuna),
        )

    _derivar(prop)
    return prop


def _derivar(prop: dict) -> None:
    """Reglas de negocio que combinan datos de varias fuentes."""
    # Un aviso de remate publicado en el Diario Oficial implica remate activo.
    pubs = (prop.get("diario_oficial") or {}).get("publicaciones") or []
    if any(p.get("tipo") == "aviso_remate" for p in pubs):
        prop.setdefault("remate", {})["en_remate"] = True
