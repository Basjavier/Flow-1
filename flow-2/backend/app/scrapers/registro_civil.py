"""Scraper del Registro Civil — estado del propietario (vivo / fallecido).

El certificado de defunción requiere RUT + serie + captcha, así que no es
automatizable headless. El modo real lanza ``AssistedCaptureRequired``; el modo
fixture lee la captura guardada.

Captura asistida (desde tu PC):
  1. Entrá a https://www.registrocivil.cl (Certificados en línea) y consultá
     defunción por RUT.
  2. Guardá un JSON en fixtures/registro_civil/<rut>.json con la forma:
       {"propietario_vivo": true}
     o, si falleció:
       {"propietario_vivo": false, "fecha_defuncion": "2025-11-20"}
"""
from __future__ import annotations

from .base import AssistedCaptureRequired, BaseScraper, fixture_mode


class RegistroCivilScraper(BaseScraper):
    fuente = "registro_civil"

    def fetch(self, rut: str) -> dict:
        if fixture_mode():
            return self.load_fixture_json(rut)
        raise AssistedCaptureRequired(
            f"Registro Civil no es automatizable (captcha). Consultá la defunción"
            f" del RUT {rut} y guardala en {self.fixture_path(rut)}."
        )
