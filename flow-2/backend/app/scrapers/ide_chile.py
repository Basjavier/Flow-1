"""Scraper de IDE Chile / Geoportal MINVU vía WFS (OGC).

Dado un punto (lat, lon) y su comuna, consulta la capa de zonificación del Plan
Regulador correspondiente y devuelve los campos del CIP (zona, altura máxima,
coeficientes, usos permitidos).

Es la fuente más automatizable: WFS es un protocolo estándar, sin captcha ni
login. MINVU publica la zonificación POR COMUNA (GeoNode), así que el typename
se resuelve desde ``config/layers.yml`` según la comuna. El modo real arma un
GetFeature con filtro espacial INTERSECTS y parsea la respuesta GeoJSON. El
modo fixture lee un GeoJSON guardado y lo parsea con el mismo normalizador,
así el parser queda cubierto por tests.
"""
from __future__ import annotations

import unicodedata
from typing import Any

import yaml

from .base import CONFIG_DIR, BaseScraper, fixture_mode

LAYERS_CONFIG = CONFIG_DIR / "layers.yml"


def normalizar_comuna(comuna: str) -> str:
    """'San Bernardo' -> 'san_bernardo'; 'Maipú' -> 'maipu'."""
    sin_tildes = (
        unicodedata.normalize("NFKD", comuna).encode("ascii", "ignore").decode("ascii")
    )
    return "_".join(sin_tildes.lower().split())


class ComunaNoMapeada(LookupError):
    """La comuna no tiene typename en layers.yml; agregalo tras validarlo."""


class IDEChileScraper(BaseScraper):
    fuente = "ide_chile"

    def _config(self) -> dict:
        return yaml.safe_load(LAYERS_CONFIG.read_text(encoding="utf-8"))

    def typename_para(self, comuna: str | None, cfg: dict | None = None) -> str:
        capa = (cfg or self._config())["capas"]["zonificacion"]
        if comuna:
            typename = capa["typename_por_comuna"].get(normalizar_comuna(comuna))
            if typename:
                return typename
        default = capa.get("typename_default") or ""
        if default:
            return default
        raise ComunaNoMapeada(
            f"La comuna '{comuna}' no tiene capa de zonificación mapeada en "
            f"{LAYERS_CONFIG}. Encontrá el typename con validar_ide.py "
            f"--capabilities y agregalo a typename_por_comuna."
        )

    def fetch(self, lat: float, lon: float, comuna: str | None = None) -> dict:
        cfg = self._config()
        campos = cfg["capas"]["zonificacion"]["campos"]

        if fixture_mode():
            geojson = self.load_fixture_json(f"{lat}_{lon}")
        else:
            typename = self.typename_para(comuna, cfg)
            geojson = self._get_feature(cfg, typename, lat, lon).json()

        return self._normalize(geojson, campos)

    def _get_feature(self, cfg: dict, typename: str, lat: float, lon: float):
        # WFS espera POINT(lon lat) en EPSG:4326.
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": typename,
            "outputFormat": "application/json",
            "srsName": cfg["default_srs"],
            "count": "1",
            "cql_filter": f"INTERSECTS({cfg['geom_field']}, POINT({lon} {lat}))",
        }
        return self.http_get(cfg["wfs_base_url"], params=params)

    @staticmethod
    def _normalize(geojson: dict, campos: dict[str, str]) -> dict:
        features = geojson.get("features", [])
        if not features:
            return {}
        props = features[0].get("properties", {}) or {}
        cip: dict[str, Any] = {}
        for destino, origen in campos.items():
            if origen in props and props[origen] is not None:
                valor = props[origen]
                if destino == "uso_permitido" and isinstance(valor, str):
                    valor = [u.strip() for u in valor.split(",") if u.strip()]
                cip[destino] = valor
        return cip
