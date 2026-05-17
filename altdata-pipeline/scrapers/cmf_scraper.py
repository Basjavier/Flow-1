import httpx
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
from config.settings import DEMO_MODE


# Hechos esenciales demo — categorías variadas para ejercitar el NLP.
DEMO_HECHOS = [
    {
        "empresa": "Falabella S.A.", "rut_emisor": "90.749.000-9",
        "tipo_documento": "Hecho Esencial",
        "descripcion": ("Acuerdo de adquisición del 100% de cadena regional "
                        "de retail por USD 420 millones, sujeto a aprobación."),
        "ticker": "FALABELLA",
    },
    {
        "empresa": "SQM S.A.", "rut_emisor": "93.007.000-9",
        "tipo_documento": "Hecho Esencial",
        "descripcion": ("Colocación de bonos corporativos por USD 800 millones "
                        "para refinanciamiento de deuda y capex."),
        "ticker": "SQM",
    },
    {
        "empresa": "Cencosud S.A.", "rut_emisor": "93.834.000-5",
        "tipo_documento": "Hecho Esencial",
        "descripcion": ("Suscripción de pacto de accionistas y cambio de "
                        "controlador tras ingreso de nuevo socio estratégico."),
        "ticker": "CENCOSUD",
    },
    {
        "empresa": "Empresas Copec S.A.", "rut_emisor": "90.690.000-9",
        "tipo_documento": "Hecho Esencial",
        "descripcion": ("Directorio aprueba reparto de dividendo extraordinario "
                        "con cargo a utilidades retenidas."),
        "ticker": "COPEC",
    },
    {
        "empresa": "Enel Chile S.A.", "rut_emisor": "94.271.000-3",
        "tipo_documento": "Hecho Esencial",
        "descripcion": ("Notificación de sanción regulatoria de la "
                        "Superintendencia del Medio Ambiente con multa."),
        "ticker": "ENELCHILE",
    },
    {
        "empresa": "CMPC S.A.", "rut_emisor": "90.222.000-3",
        "tipo_documento": "Hecho Esencial",
        "descripcion": ("Publicación de estados financieros trimestrales: "
                        "EBITDA en línea con guidance, sin cambios de proyección."),
        "ticker": "CMPC",
    },
]


class CMFScraper:

    BASE_URL  = "https://api.cmfchile.cl/api-sbifv3/recursos_api"
    PORTAL_URL = "https://www.cmfchile.cl/sitio/aplic/serdoc/ver_sgd.php"

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "es-CL,es;q=0.9",
            }
        )

    def _demo_hechos(self, days_back: int) -> list[dict]:
        now = datetime.now()
        out = []
        for i, h in enumerate(DEMO_HECHOS):
            fecha = (now - timedelta(hours=4 * (i + 1))).strftime("%Y-%m-%d")
            out.append({
                "fecha":          fecha,
                "empresa":        h["empresa"],
                "rut_emisor":     h["rut_emisor"],
                "tipo_documento": h["tipo_documento"],
                "descripcion":    h["descripcion"],
                "url_documento":  f"https://www.cmfchile.cl/demo/he/{h['ticker'].lower()}",
                "source":         "CMF_DEMO",
                "scraped_at":     now.isoformat(),
            })
        return out

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def fetch_hechos_esenciales(self, days_back: int = 1) -> list[dict]:
        fecha_desde = (
            datetime.now() - timedelta(days=days_back)
        ).strftime("%Y-%m-%d")

        if DEMO_MODE:
            logger.info("CMF demo: hechos sintéticos")
            return self._demo_hechos(days_back)

        # Intentar API oficial primero
        try:
            result = await self._fetch_api(fecha_desde)
            if result:
                logger.info(f"CMF API: {len(result)} hechos")
                return result
        except Exception as e:
            logger.warning(f"CMF API falló, usando scraping: {e}")

        # Fallback: scraping directo, y si tampoco hay red → demo (autosuficiente)
        hechos = await self._scrape_portal(fecha_desde)
        if hechos:
            return hechos
        logger.warning("CMF sin datos en vivo → fallback demo")
        return self._demo_hechos(days_back)

    async def _fetch_api(self, fecha_desde: str) -> list[dict]:
        """Endpoint oficial CMF (cuando está disponible)."""
        params = {
            "tipo": "HE",
            "fecha_desde": fecha_desde,
            "formato": "json"
        }
        response = await self.client.get(
            f"{self.BASE_URL}/documentos",
            params=params
        )
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}")

        data = response.json()
        hechos = []
        for item in data.get("documentos", []):
            hechos.append({
                "fecha":          item.get("fecha_recepcion"),
                "empresa":        item.get("nombre_emisor"),
                "rut_emisor":     item.get("rut_emisor"),
                "tipo_documento": item.get("tipo_documento"),
                "descripcion":    item.get("titulo"),
                "url_documento":  item.get("url"),
                "source":         "CMF_API",
                "scraped_at":     datetime.now().isoformat(),
            })
        return hechos

    async def _scrape_portal(self, fecha_desde: str) -> list[dict]:
        """Scraping directo del portal CMF con Playwright."""
        try:
            from playwright.async_api import async_playwright
            hechos = []

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--ignore-certificate-errors"]
                )
                context = await browser.new_context(
                    ignore_https_errors=True,
                    extra_http_headers={"Accept-Language": "es-CL,es;q=0.9"}
                )
                page = await context.new_page()

                url = (
                    f"{self.PORTAL_URL}?"
                    f"tipo=HE&fecha_desde={fecha_desde}&formato=html"
                )
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                content = await page.content()
                await browser.close()

            soup = BeautifulSoup(content, "lxml")
            tabla = soup.find("table")
            if not tabla:
                logger.warning("CMF: tabla no encontrada en HTML")
                return []

            for row in tabla.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                link = cols[-1].find("a")
                url_doc = None
                if link and link.get("href"):
                    href = link["href"]
                    url_doc = href if href.startswith("http") else f"https://www.cmfchile.cl{href}"

                hechos.append({
                    "fecha":          cols[0].get_text(strip=True),
                    "empresa":        cols[1].get_text(strip=True),
                    "rut_emisor":     cols[2].get_text(strip=True) if len(cols) > 2 else "",
                    "tipo_documento": cols[3].get_text(strip=True) if len(cols) > 3 else "HE",
                    "descripcion":    cols[-1].get_text(strip=True),
                    "url_documento":  url_doc,
                    "source":         "CMF_SCRAPE",
                    "scraped_at":     datetime.now().isoformat(),
                })

            logger.info(f"CMF scrape: {len(hechos)} hechos")
            return hechos

        except Exception as e:
            logger.error(f"CMF scraping error: {e}")
            return []
