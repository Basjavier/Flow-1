import httpx
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def fetch_hechos_esenciales(self, days_back: int = 1) -> list[dict]:
        fecha_desde = (
            datetime.now() - timedelta(days=days_back)
        ).strftime("%Y-%m-%d")

        # Intentar API oficial primero
        try:
            result = await self._fetch_api(fecha_desde)
            if result:
                logger.info(f"CMF API: {len(result)} hechos")
                return result
        except Exception as e:
            logger.warning(f"CMF API falló, usando scraping: {e}")

        # Fallback: scraping directo
        return await self._scrape_portal(fecha_desde)

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
