"""Scraper de IDE Chile / Geoportal vía WFS (OGC).

Dado un punto (lat, lon) consulta la capa de zonificación y devuelve los
campos del CIP (zona, altura máxima, coeficientes, usos permitidos).

Es la fuente más automatizable: WFS es un protocolo estándar, sin captcha ni
login. El modo real arma un GetFeature con filtro espacial INTERSECTS y parsea
la respuesta GeoJSON. El modo fixture lee un GeoJSON guardado y lo parsea con
el mismo normalizador, así el parser queda cubierto por tests.
"""
from __future__ import annotations

from typing import Any

import yaml

from .base import CONFIG_DIR, BaseScraper, fixture_mode

LAYERS_CONFIG = CONFIG_DIR / "layers.yml"


class IDEChileScraper(BaseScraper):
    fuente = "ide_chile"

    def _config(self) -> dict:
        return yaml.safe_load(LAYERS_CONFIG.read_text(encoding="utf-8"))

    def fetch(self, lat: float, lon: float) -> dict:
        cfg = self._config()
        campos = cfg["capas"]["zonificacion"]["campos"]

        if fixture_mode():
            geojson = self.load_fixture_json(f"{lat}_{lon}")
        else:
            geojson = self._get_feature(cfg, lat, lon).json()

        return self._normalize(geojson, campos)

    def _get_feature(self, cfg: dict, lat: float, lon: float):
        # WFS espera POINT(lon lat) en EPSG:4326.
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": cfg["capas"]["zonificacion"]["typename"],
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
