"""
Portal Inmobiliario scraper — Playwright-based (dynamic site).

Scrapes listings from portalinmobiliario.com by commune and property type.
Falls back to httpx + BeautifulSoup if Playwright is unavailable.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import structlog

from config import settings, PRIORITY_COMMUNES
from scraper.base import normalize_commune, parse_m2, parse_price_clp, rate_limit, save_raw_html

log = structlog.get_logger(__name__)

# URL pattern: /venta/{tipo}/{commune}-metropolitana
_TIPO_SLUG: dict[str, str] = {
    "departamento": "departamento",
    "casa": "casa",
    "oficina": "oficina",
}

# CSS selectors — as of 2025-2026 portal structure
_SELECTORS = {
    "listing_cards": "li.ui-search-layout__item",
    "title": "h2.poly-box a, h2.ui-search-item__title",
    "price": ".poly-price__current .andes-money-amount__fraction, .price-tag-fraction",
    "price_currency": ".andes-money-amount__currency-symbol",
    "m2": ".poly-attributes-list__item",
    "location": ".poly-component__location",
    "link": "h2.poly-box a, a.ui-search-item__title-label",
    "date": "time[datetime]",
    "bedrooms": "[data-testid='bedrooms'], .poly-attributes-list__item",
    "bathrooms": "[data-testid='bathrooms']",
    "next_page": ".andes-pagination__button--next a",
}


# ---------------------------------------------------------------------------
# Playwright scraper (primary)
# ---------------------------------------------------------------------------


async def scrape_commune(
    commune: str,
    tipo: str = "departamento",
    max_pages: int = 5,
) -> list[dict]:
    """
    Scrape listings for a single commune + property type.
    Returns list of raw property dicts.
    """
    try:
        from playwright.async_api import async_playwright
        return await _scrape_with_playwright(commune, tipo, max_pages)
    except ImportError:
        log.warning("playwright_not_available", fallback="httpx+bs4")
        return await _scrape_with_httpx(commune, tipo, max_pages)
    except Exception as exc:
        log.error("playwright_scrape_failed", commune=commune, tipo=tipo, error=str(exc))
        log.info("playwright_fallback_to_httpx")
        try:
            return await _scrape_with_httpx(commune, tipo, max_pages)
        except Exception as exc2:
            log.error("httpx_scrape_also_failed", error=str(exc2))
            return []


async def _scrape_with_playwright(
    commune: str,
    tipo: str,
    max_pages: int,
) -> list[dict]:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    listings: list[dict] = []
    slug = commune.lower().replace(" ", "-").replace("ñ", "n").replace("é", "e").replace("ó", "o")
    tipo_slug = _TIPO_SLUG.get(tipo, tipo)
    base_url = f"{settings.portal_base_url}/venta/{tipo_slug}/{slug}-metropolitana"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       f"(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        current_url = base_url
        for page_num in range(1, max_pages + 1):
            try:
                await page.goto(current_url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector(_SELECTORS["listing_cards"], timeout=10_000)
            except PWTimeout:
                log.warning("playwright_timeout", url=current_url, page=page_num)
                break

            html = await page.content()
            save_raw_html(html, "portal_inmobiliario", f"{slug}_{tipo}_p{page_num}")

            page_listings = _parse_portal_html(html, commune, tipo)
            listings.extend(page_listings)
            log.info("portal_page_scraped", commune=commune, tipo=tipo, page=page_num, found=len(page_listings))

            await rate_limit()

            # Navigate to next page
            next_btn = page.locator(_SELECTORS["next_page"])
            if await next_btn.count() == 0:
                break
            next_href = await next_btn.get_attribute("href")
            if not next_href:
                break
            current_url = urljoin(settings.portal_base_url, next_href)

        await browser.close()

    log.info("portal_commune_done", commune=commune, tipo=tipo, total=len(listings))
    return listings


async def _scrape_with_httpx(
    commune: str,
    tipo: str,
    max_pages: int,
) -> list[dict]:
    import httpx
    from bs4 import BeautifulSoup

    listings: list[dict] = []
    slug = commune.lower().replace(" ", "-")
    tipo_slug = _TIPO_SLUG.get(tipo, tipo)
    base_url = f"{settings.portal_base_url}/venta/{tipo_slug}/{slug}-metropolitana"

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept-Language": "es-CL,es;q=0.9",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20) as client:
        current_url = base_url
        for page_num in range(1, max_pages + 1):
            try:
                resp = await client.get(current_url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.error("httpx_fetch_failed", url=current_url, error=str(exc))
                break

            html = resp.text
            save_raw_html(html, "portal_inmobiliario", f"{slug}_{tipo}_p{page_num}_bs4")
            page_listings = _parse_portal_html(html, commune, tipo)
            listings.extend(page_listings)
            await rate_limit()

            soup = BeautifulSoup(html, "lxml")
            next_a = soup.select_one(_SELECTORS["next_page"])
            if not next_a or not next_a.get("href"):
                break
            current_url = urljoin(settings.portal_base_url, next_a["href"])

    return listings


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------


def _parse_portal_html(html: str, commune: str, tipo: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    results = []

    for card in soup.select(_SELECTORS["listing_cards"]):
        try:
            item = _parse_card(card, commune, tipo)
            if item:
                results.append(item)
        except Exception as exc:
            log.debug("card_parse_error", error=str(exc))

    return results


def _parse_card(card, commune: str, tipo: str) -> Optional[dict]:
    from bs4 import Tag

    # Link + external ID
    link_tag = card.select_one("h2 a, a.poly-component__title")
    if not link_tag:
        return None
    url = link_tag.get("href", "")
    if not url.startswith("http"):
        url = urljoin(settings.portal_base_url, url)

    external_id_match = re.search(r"/(\d+)-", url)
    external_id = external_id_match.group(1) if external_id_match else url[-20:]

    # Price
    price_tag = card.select_one(".poly-price__current .andes-money-amount__fraction, .price-tag-fraction")
    if not price_tag:
        return None
    currency_tag = card.select_one(".andes-money-amount__currency-symbol, .price-tag-symbol")
    currency = (currency_tag.get_text(strip=True) if currency_tag else "").upper()

    raw_price = price_tag.get_text(strip=True)
    precio_raw = parse_price_clp(raw_price)
    if not precio_raw:
        return None

    # Convert UF to CLP if needed
    from config import settings as cfg
    if "UF" in currency or currency == "UF":
        precio_clp = int(precio_raw * cfg.uf_value_clp)
        precio_uf = float(precio_raw)
    else:
        precio_clp = precio_raw
        precio_uf = precio_raw / cfg.uf_value_clp

    # m²
    m2 = None
    for attr in card.select(".poly-attributes-list__item, .ui-search-card-attributes__attribute"):
        text = attr.get_text(strip=True).lower()
        if "m²" in text or "m2" in text:
            m2 = parse_m2(text)
            break
    if not m2 or m2 <= 0:
        return None

    # Location
    location_tag = card.select_one(".poly-component__location, .ui-search-item__location")
    address = location_tag.get_text(strip=True) if location_tag else None

    # Bedrooms / bathrooms
    dormitorios = None
    banos = None
    for attr in card.select(".poly-attributes-list__item"):
        text = attr.get_text(strip=True).lower()
        if "dorm" in text or "hab" in text:
            m = re.search(r"(\d+)", text)
            dormitorios = int(m.group(1)) if m else None
        elif "baño" in text or "bano" in text:
            m = re.search(r"(\d+)", text)
            banos = int(m.group(1)) if m else None

    precio_m2 = precio_clp / m2

    return {
        "external_id": external_id,
        "source": "portal_inmobiliario",
        "tipo_propiedad": tipo,
        "comuna": normalize_commune(commune),
        "address": address,
        "precio": precio_clp,
        "precio_uf": round(precio_uf, 2),
        "m2": m2,
        "precio_m2": round(precio_m2, 2),
        "dormitorios": dormitorios,
        "banos": banos,
        "url": url,
        "fecha_publicacion": None,  # Portal doesn't expose publish date in listing cards
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry point: scrape all priority communes
# ---------------------------------------------------------------------------


async def scrape_all_priority_communes(
    tipos: list[str] | None = None,
    max_pages: int = 5,
) -> list[dict]:
    if tipos is None:
        tipos = ["departamento", "casa"]

    all_listings: list[dict] = []
    for commune in PRIORITY_COMMUNES:
        for tipo in tipos:
            listings = await scrape_commune(commune, tipo, max_pages)
            all_listings.extend(listings)

    log.info("portal_full_scrape_done", total=len(all_listings))
    return all_listings
