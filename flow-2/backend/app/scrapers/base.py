"""Base de los scrapers de fuentes para Due Diligence.

Cada scraper opera en dos modos, controlados por la variable de entorno
``SCRAPER_FIXTURE_MODE``:

  - **fixture** (default): lee respuestas guardadas en ``fixtures/<fuente>/``.
    Es el modo para desarrollo y tests, 100% offline.
  - **real** (``SCRAPER_FIXTURE_MODE=0``): pega contra el endpoint de producción.
    Se corre desde una máquina con acceso a las fuentes chilenas.

Fuentes que no son automatizables sin navegador (captcha/login) lanzan
``AssistedCaptureRequired`` en modo real, con instrucciones para capturar el
fixture a mano.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CONFIG_DIR = Path(__file__).parent / "config"


class AssistedCaptureRequired(RuntimeError):
    """La fuente necesita captura asistida (captcha, login o navegador).

    El modo real no puede resolverla solo; hay que capturar el fixture desde
    una sesión humana y guardarlo en ``fixtures/<fuente>/``.
    """


def fixture_mode() -> bool:
    """True salvo que SCRAPER_FIXTURE_MODE esté explícitamente desactivado."""
    val = os.getenv("SCRAPER_FIXTURE_MODE", "1").strip().lower()
    return val not in ("0", "false", "no")


def _safe(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))


class BaseScraper:
    fuente: str = "base"

    def fixture_path(self, key: str, ext: str = "json") -> Path:
        return FIXTURES_DIR / self.fuente / f"{_safe(key)}.{ext}"

    def load_fixture_json(self, key: str) -> Any:
        path = self.fixture_path(key, "json")
        if not path.exists():
            raise FileNotFoundError(
                f"Falta fixture de '{self.fuente}' para '{key}': {path}"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def load_fixture_text(self, key: str, ext: str) -> str:
        path = self.fixture_path(key, ext)
        if not path.exists():
            raise FileNotFoundError(
                f"Falta fixture de '{self.fuente}' para '{key}': {path}"
            )
        return path.read_text(encoding="utf-8")

    def http_get(self, url: str, params: dict | None = None, timeout: int = 20):
        import requests

        headers = {"User-Agent": "flow-2-dd/0.1 (+due-diligence)"}
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp
