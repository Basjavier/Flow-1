"""Scraper del Conservador de Bienes Raíces (CBR) — gravámenes y prohibiciones.

El CBR es por conservador, de pago y con login, así que no es automatizable de
forma genérica. El modo real lanza ``AssistedCaptureRequired``; el modo fixture
lee la captura guardada.

Captura asistida (desde tu PC):
  1. Pedí el Certificado de Hipotecas y Gravámenes en el CBR que corresponda.
  2. Guardá un JSON en fixtures/conservador/<rol>.json con la forma:
       {
         "hipotecas":     [{"acreedor": "...", "monto_uf": 0}],
         "prohibiciones": [],
         "embargos":      [],
         "litigios":      []
       }
"""
from __future__ import annotations

from .base import AssistedCaptureRequired, BaseScraper, fixture_mode


class ConservadorScraper(BaseScraper):
    fuente = "conservador"

    def fetch(self, rol: str) -> dict:
        if fixture_mode():
            return self.load_fixture_json(rol)
        raise AssistedCaptureRequired(
            f"El Conservador requiere certificado de pago. Solicitá el de"
            f" hipotecas y gravámenes del rol {rol} y guardalo en "
            f"{self.fixture_path(rol)}."
        )
