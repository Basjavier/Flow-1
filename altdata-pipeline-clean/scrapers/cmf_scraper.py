import httpx
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


class CMFScraper:

    # ATENCION: api.cmfchile.cl/api-sbifv3 es la API del SBIF (datos
    # bancarios/UF/etc.), NO entrega hechos esenciales. Para hechos
    # esenciales hay que ir al portal CMF. Hoy SIEMPRE caemos al
    # scraper del portal hasta que CMF publique un REST publico.
    BASE_URL  = "https://www.cmfchile.cl/portal/principal/613/w3-propertyvalue-18494.html"
    PORTAL_URL = "https://www.cmfchile.cl/institucional/hechos/hechos.php"

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
        return await self._scrape_portal(fecha_desde)

    async def _scrape_portal(self, fecha_desde: str) -> list[dict]:
        """Scraping del portal CMF con Playwright."""
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
