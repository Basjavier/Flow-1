import asyncio
import random
from datetime import datetime
from loguru import logger


STEALTH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-CL,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
}

BASE_UF_M2 = {
    "Las Condes":      95, "Providencia": 85,
    "Vitacura":       110, "Ñuñoa":       70,
    "Santiago Centro": 55, "Maipú":       40,
    "Miraflores":      60, "La Florida":  45,
}


class PortalInmobiliarioScraper:

    async def scrape_top20(
        self,
        comuna: str = "Las Condes",
        tipo: str = "departamento",
        demo: bool = False,
    ) -> list[dict]:

        if demo:
            logger.info(f"RE demo data: {comuna} {tipo}")
            return self._generate_demo_data(comuna, tipo)

        try:
            return await self._scrape_live(comuna, tipo)
        except Exception as e:
            logger.warning(f"Portal Inmobiliario fallback demo: {e}")
            return self._generate_demo_data(comuna, tipo)

    async def _scrape_live(self, comuna: str, tipo: str) -> list[dict]:
        from playwright.async_api import async_playwright

        slug = comuna.lower().replace(" ", "-").replace("ñ", "n")
        url = f"https://www.portalinmobiliario.com/venta/{tipo}/{slug}"

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--ignore-certificate-errors",
                ],
            )
            context = await browser.new_context(
                extra_http_headers=STEALTH_HEADERS,
                ignore_https_errors=True,
                viewport={"width": 1280, "height": 800},
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)

            listings = await self._extract(page, comuna, tipo)
            await browser.close()

        logger.info(f"Portal Inmobiliario: {len(listings)} props en {comuna}")
        return listings

    async def _extract(self, page, comuna: str, tipo: str) -> list[dict]:
        listings = []
        cards = await page.query_selector_all('[class*="ui-search-result"]')

        for card in cards[:20]:
            try:
                precio_el = await card.query_selector('[class*="price__fraction"]')
                titulo_el = await card.query_selector('[class*="item__title"]')
                m2_el     = await card.query_selector('[title*="m²"]')
                loc_el    = await card.query_selector('[class*="location"]')

                precio_txt = await precio_el.inner_text() if precio_el else None
                titulo_txt = await titulo_el.inner_text() if titulo_el else None
                m2_txt     = await m2_el.inner_text() if m2_el else None
                loc_txt    = await loc_el.inner_text() if loc_el else None

                precio_uf = self._parse_num(precio_txt)
                m2        = self._parse_num(m2_txt)

                if precio_uf and titulo_txt:
                    listings.append({
                        "titulo":      titulo_txt.strip(),
                        "precio_uf":   precio_uf,
                        "m2":          m2,
                        "precio_uf_m2": round(precio_uf / m2, 1) if m2 and m2 > 0 else None,
                        "ubicacion":   loc_txt.strip() if loc_txt else comuna,
                        "source":      "portal_inmobiliario",
                        "scraped_at":  datetime.now().isoformat(),
                    })
            except Exception:
                continue

        return listings

    def _parse_num(self, texto: str | None) -> float | None:
        if not texto:
            return None
        try:
            clean = "".join(c for c in texto if c.isdigit() or c in ".,")
            clean = clean.replace(".", "").replace(",", ".")
            return float(clean) if clean else None
        except Exception:
            return None

    def _generate_demo_data(self, comuna: str, tipo: str) -> list[dict]:
        base = BASE_UF_M2.get(comuna, 65)
        listings = []
        for i in range(20):
            m2 = random.randint(45, 180)
            uf_m2 = round(base + random.gauss(0, base * 0.12), 1)
            precio = round(m2 * uf_m2)
            listings.append({
                "titulo":      f"{tipo.capitalize()} {m2}m² {comuna} #{i+1}",
                "precio_uf":   precio,
                "m2":          float(m2),
                "precio_uf_m2": uf_m2,
                "ubicacion":   f"{comuna}, RM",
                "source":      "demo",
                "scraped_at":  datetime.now().isoformat(),
            })
        return sorted(listings, key=lambda x: x["precio_uf_m2"])
