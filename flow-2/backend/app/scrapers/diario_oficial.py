"""Scraper del Diario Oficial — búsqueda de avisos (remates, etc.).

La respuesta del Diario Oficial es HTML, no JSON. El modo real pega contra el
buscador y parsea los resultados; el modo fixture lee un HTML guardado y lo
parsea con el mismo selector.

NOTA: el selector de resultados (``_parse``) está escrito contra la estructura
esperada del buscador. Hay que validarlo contra un HTML real capturado y
ajustar el ``BUSCADOR_URL`` y los selectores si cambió el markup.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from .base import BaseScraper, fixture_mode

BUSCADOR_URL = "https://www.diariooficial.interior.gob.cl/edicionelectronica/buscar"

# Palabras que marcan un aviso como remate judicial.
TERMINOS_REMATE = ("remate", "subasta", "pública subasta")


class DiarioOficialScraper(BaseScraper):
    fuente = "diario_oficial"

    def fetch(self, termino: str) -> dict:
        """Busca publicaciones que mencionen ``termino`` (rol, RUT o dirección)."""
        if fixture_mode():
            html = self.load_fixture_text(termino, "html")
        else:
            html = self.http_get(BUSCADOR_URL, params={"q": termino}).text
        return {"publicaciones": self._parse(html)}

    @staticmethod
    def _parse(html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        publicaciones = []
        for row in soup.select("li.resultado, tr.resultado, .resultado-busqueda"):
            titulo_el = row.select_one(".titulo, a")
            fecha_el = row.select_one(".fecha")
            titulo = titulo_el.get_text(strip=True) if titulo_el else ""
            fecha = fecha_el.get_text(strip=True) if fecha_el else ""
            url = ""
            link = row.select_one("a[href]")
            if link:
                url = link["href"]
            texto = titulo.lower()
            tipo = "aviso_remate" if any(t in texto for t in TERMINOS_REMATE) else "otro"
            publicaciones.append(
                {"fecha": fecha, "titulo": titulo, "tipo": tipo, "url": url}
            )
        return publicaciones
