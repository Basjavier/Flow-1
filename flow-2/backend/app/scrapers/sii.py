"""Scraper del SII — avalúo fiscal y destino por rol + comuna.

La consulta de avalúo del SII usa un formulario con captcha, así que no es
automatizable headless. El modo real lanza ``AssistedCaptureRequired`` con
instrucciones; el modo fixture lee la captura guardada.

Captura asistida (desde tu PC, una vez):
  1. Entrá a https://www4.sii.cl/mapasui/internet/#/contenido/index.html
     (Mapas / Avalúos y Certificados) y buscá por comuna + rol.
  2. Anotá el avalúo fiscal (en UF) y el destino.
  3. Guardá un JSON en fixtures/sii/<rol>.json con la forma:
       {"avaluo_fiscal_uf": 8200, "destino": "habitacional"}
"""
from __future__ import annotations

from .base import AssistedCaptureRequired, BaseScraper, fixture_mode


class SIIScraper(BaseScraper):
    fuente = "sii"

    def fetch(self, rol: str, comuna: str | None = None) -> dict:
        if fixture_mode():
            return self.load_fixture_json(rol)
        raise AssistedCaptureRequired(
            f"SII no es automatizable (captcha). Capturá el avalúo del rol {rol}"
            f" ({comuna or 'comuna ?'}) y guardalo en "
            f"{self.fixture_path(rol)}. Ver instrucciones en sii.py."
        )
