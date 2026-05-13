"""
Portal Inmobiliario scraper — Playwright-based (dynamic site).

Two scraping modes:
  1. Full Región Metropolitana (recommended): single URL per tipo, extracts
     commune from each listing card.  Use scrape_rm_full().
  2. Per-commune (legacy): iterates PRIORITY_COMMUNES.  Use scrape_all_priority_communes().

Falls back from Playwright → httpx + BeautifulSoup4 automatically.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlencode

import structlog

from config import settings, PRIORITY_COMMUNES
from scraper.base import normalize_commune, parse_m2, parse_price_clp, rate_limit, save_raw_html

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# URL slugs
# ---------------------------------------------------------------------------

# Per-tipo slug for Portal Inmobiliario URL paths
_TIPO_SLUG: dict[str, str] = {
    "departamento": "departamento",
    "casa": "casa",
    "terreno": "terreno",
    "oficina": "oficina",
}

# Full RM region slug — no commune filter
_RM_SLUG = "region-metropolitana-de-santiago"

# Portal lists 48 items per page; `_Desde_N` controls offset (0-indexed)
_PAGE_SIZE = 48

# CSS selectors (Portal runs on MercadoLibre infra — selectors stable as of 2025-2026)
_SELECTORS = {
    "listing_cards": "li.ui-search-layout__item",
    "link": "a.poly-component__title, h2.poly-box a",
    "price_fraction": ".poly-price__current .andes-money-amount__fraction, .price-tag-fraction",
    "price_currency": ".poly-price__current .andes-money-amount__currency-symbol, .price-tag-symbol",
    "attributes": ".poly-attributes-list__item, .ui-search-card-attributes__attribute",
    "location": ".poly-component__location, .ui-search-item__location",
    "next_page": ".andes-pagination__button--next a",
}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def scrape_rm_full(
    tipos: list[str] | None = None,
    max_pages: int = 10,
) -> list[dict]:
    """
    Scrape ALL of Región Metropolitana for each tipo — no commune filter.
    This is the recommended mode: widest coverage, extracts commune from each card.
    """
    if tipos is None:
        tipos = ["departamento", "casa", "terreno"]

    all_listings: list[dict] = []
    for tipo in tipos:
        listings = await _scrape_region(tipo, max_pages)
        all_listings.extend(listings)
        log.info("rm_tipo_done", tipo=tipo, count=len(listings))

    log.info("rm_full_scrape_done", total=len(all_listings), tipos=tipos)
    return all_listings


async def scrape_commune(
    commune: str,
    tipo: str = "departamento",
    max_pages: int = 5,
) -> list[dict]:
    """Scrape a specific commune (legacy/targeted mode)."""
    try:
        from playwright.async_api import async_playwright
        return await _playwright_commune(commune, tipo, max_pages)
    except ImportError:
        log.warning("playwright_not_installed", fallback="httpx+bs4")
        return await _httpx_commune(commune, tipo, max_pages)
    except Exception as exc:
        log.error("playwright_failed", commune=commune, error=str(exc))
        try:
            return await _httpx_commune(commune, tipo, max_pages)
        except Exception as exc2:
            log.error("httpx_also_failed", commune=commune, error=str(exc2))
            return []


async def scrape_all_priority_communes(
    tipos: list[str] | None = None,
    max_pages: int = 5,
) -> list[dict]:
    """Scrape only the priority communes from config.PRIORITY_COMMUNES."""
    if tipos is None:
        tipos = ["departamento", "casa"]
    all_listings: list[dict] = []
    for commune in PRIORITY_COMMUNES:
        for tipo in tipos:
            listings = await scrape_commune(commune, tipo, max_pages)
            all_listings.extend(listings)
    return all_listings


# ---------------------------------------------------------------------------
# Region-level scraping (full RM, no commune filter)
# ---------------------------------------------------------------------------


async def _scrape_region(tipo: str, max_pages: int) -> list[dict]:
    """Try Playwright first, fall back to httpx."""
    tipo_slug = _TIPO_SLUG.get(tipo, tipo)
    base_url = f"{settings.portal_base_url}/venta/{tipo_slug}/{_RM_SLUG}"

    try:
        from playwright.async_api import async_playwright
        return await _playwright_region(base_url, tipo, max_pages)
    except ImportError:
        log.warning("playwright_not_installed_region", tipo=tipo, fallback="httpx")
        return await _httpx_region(base_url, tipo, max_pages)
    except Exception as exc:
        log.error("playwright_region_failed", tipo=tipo, error=str(exc))
        try:
            return await _httpx_region(base_url, tipo, max_pages)
        except Exception as exc2:
            log.error("httpx_region_also_failed", tipo=tipo, error=str(exc2))
            return []


async def _playwright_region(base_url: str, tipo: str, max_pages: int) -> list[dict]:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    listings: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()
        current_url = base_url

        for page_num in range(1, max_pages + 1):
            try:
                await page.goto(current_url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector(_SELECTORS["listing_cards"], timeout=10_000)
            except PWTimeout:
                log.warning("playwright_timeout_region", url=current_url)
                break

            html = await page.content()
            save_raw_html(html, "portal_inmobiliario", f"rm_{tipo}_p{page_num}")
            page_items = _parse_portal_html(html, tipo)
            listings.extend(page_items)
            log.info("portal_rm_page", tipo=tipo, page=page_num, found=len(page_items))
            await rate_limit()

            next_btn = page.locator(_SELECTORS["next_page"])
            if await next_btn.count() == 0:
                break
            next_href = await next_btn.get_attribute("href")
            if not next_href:
                break
            current_url = urljoin(settings.portal_base_url, next_href)

        await browser.close()
    return listings


async def _httpx_region(base_url: str, tipo: str, max_pages: int) -> list[dict]:
    import httpx
    from bs4 import BeautifulSoup

    listings: list[dict] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CL,es;q=0.9",
        "Referer": settings.portal_base_url,
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
        for page_num in range(1, max_pages + 1):
            # MercadoLibre-style pagination: _Desde_N offset
            offset = (page_num - 1) * _PAGE_SIZE
            url = base_url if offset == 0 else f"{base_url}/_Desde_{offset + 1}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.error("httpx_region_page_failed", url=url, error=str(exc))
                break

            html = resp.text
            save_raw_html(html, "portal_inmobiliario", f"rm_{tipo}_p{page_num}_httpx")
            page_items = _parse_portal_html(html, tipo)
            if not page_items:
                log.info("portal_rm_empty_page", tipo=tipo, page=page_num)
                break
            listings.extend(page_items)
            log.info("portal_rm_page_httpx", tipo=tipo, page=page_num, found=len(page_items))
            await rate_limit()

    return listings


# ---------------------------------------------------------------------------
# Per-commune scraping (legacy)
# ---------------------------------------------------------------------------


async def _playwright_commune(commune: str, tipo: str, max_pages: int) -> list[dict]:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    listings: list[dict] = []
    slug = _commune_slug(commune)
    tipo_slug = _TIPO_SLUG.get(tipo, tipo)
    base_url = f"{settings.portal_base_url}/venta/{tipo_slug}/{slug}-metropolitana"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        current_url = base_url
        for page_num in range(1, max_pages + 1):
            try:
                await page.goto(current_url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector(_SELECTORS["listing_cards"], timeout=10_000)
            except PWTimeout:
                break
            html = await page.content()
            save_raw_html(html, "portal_inmobiliario", f"{slug}_{tipo}_p{page_num}")
            listings.extend(_parse_portal_html(html, tipo, commune_hint=commune))
            await rate_limit()
            next_btn = page.locator(_SELECTORS["next_page"])
            if await next_btn.count() == 0:
                break
            nxt = await next_btn.get_attribute("href")
            if not nxt:
                break
            current_url = urljoin(settings.portal_base_url, nxt)
        await browser.close()
    return listings


async def _httpx_commune(commune: str, tipo: str, max_pages: int) -> list[dict]:
    import httpx

    listings: list[dict] = []
    slug = _commune_slug(commune)
    tipo_slug = _TIPO_SLUG.get(tipo, tipo)
    base_url = f"{settings.portal_base_url}/venta/{tipo_slug}/{slug}-metropolitana"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-CL,es;q=0.9",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=25) as client:
        for page_num in range(1, max_pages + 1):
            offset = (page_num - 1) * _PAGE_SIZE
            url = base_url if offset == 0 else f"{base_url}/_Desde_{offset + 1}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.error("httpx_commune_failed", url=url, error=str(exc))
                break
            html = resp.text
            save_raw_html(html, "portal_inmobiliario", f"{slug}_{tipo}_p{page_num}_bs4")
            items = _parse_portal_html(html, tipo, commune_hint=commune)
            if not items:
                break
            listings.extend(items)
            await rate_limit()
    return listings


# ---------------------------------------------------------------------------
# HTML parser — shared between Playwright and httpx modes
# ---------------------------------------------------------------------------


def _parse_portal_html(
    html: str,
    tipo: str,
    commune_hint: str | None = None,
) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    for card in soup.select(_SELECTORS["listing_cards"]):
        try:
            item = _parse_card(card, tipo, commune_hint)
            if item:
                results.append(item)
        except Exception as exc:
            log.debug("card_parse_error", error=str(exc))
    return results


def _parse_card(
    card,
    tipo: str,
    commune_hint: str | None,
) -> Optional[dict]:
    # --- Link + external ID ---
    link_tag = card.select_one(_SELECTORS["link"])
    if not link_tag:
        return None
    url: str = link_tag.get("href", "")
    if not url.startswith("http"):
        url = urljoin(settings.portal_base_url, url)

    ext_match = re.search(r"-(\d{6,})-", url) or re.search(r"/(\d{6,})", url)
    external_id = ext_match.group(1) if ext_match else url[-20:]

    # --- Price ---
    price_tag = card.select_one(_SELECTORS["price_fraction"])
    if not price_tag:
        return None
    curr_tag = card.select_one(_SELECTORS["price_currency"])
    currency = (curr_tag.get_text(strip=True) if curr_tag else "").upper()
    precio_raw = parse_price_clp(price_tag.get_text(strip=True))
    if not precio_raw or precio_raw < 100:
        return None

    if "UF" in currency:
        precio_uf = float(precio_raw)
        precio_clp = int(precio_uf * settings.uf_value_clp)
    else:
        precio_clp = precio_raw
        precio_uf = precio_clp / settings.uf_value_clp

    # --- m² ---
    m2: Optional[float] = None
    dormitorios: Optional[int] = None
    banos: Optional[int] = None
    for attr in card.select(_SELECTORS["attributes"]):
        text = attr.get_text(strip=True).lower()
        if ("m²" in text or "m2" in text) and m2 is None:
            m2 = parse_m2(text)
        elif ("dorm" in text or "hab" in text) and dormitorios is None:
            m_int = re.search(r"(\d+)", text)
            dormitorios = int(m_int.group(1)) if m_int else None
        elif ("baño" in text or "bano" in text) and banos is None:
            m_int = re.search(r"(\d+)", text)
            banos = int(m_int.group(1)) if m_int else None
    if not m2 or m2 <= 0:
        return None

    # --- Commune from location tag (critical for full-RM mode) ---
    commune = commune_hint
    if commune is None:
        loc_tag = card.select_one(_SELECTORS["location"])
        if loc_tag:
            commune = _extract_commune_from_location(loc_tag.get_text(strip=True))
    if not commune:
        commune = "Desconocida"
    commune = normalize_commune(commune)

    address_tag = card.select_one(_SELECTORS["location"])
    address = address_tag.get_text(strip=True) if address_tag else None

    return {
        "external_id": external_id,
        "source": "portal_inmobiliario",
        "tipo_propiedad": tipo,
        "comuna": commune,
        "address": address,
        "precio": precio_clp,
        "precio_uf": round(precio_uf, 2),
        "m2": m2,
        "precio_m2": round(precio_clp / m2, 2),
        "dormitorios": dormitorios,
        "banos": banos,
        "url": url,
        "fecha_publicacion": None,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _commune_slug(commune: str) -> str:
    """Convert commune name to Portal Inmobiliario URL slug."""
    return (
        commune.lower()
        .replace(" ", "-")
        .replace("ñ", "n")
        .replace("é", "e")
        .replace("ó", "o")
        .replace("á", "a")
        .replace("í", "i")
        .replace("ú", "u")
        .replace("ü", "u")
    )


# Known commune name fragments that appear in Portal location strings
_COMMUNE_FRAGMENTS: list[tuple[str, str]] = [
    ("las condes", "Las Condes"),
    ("vitacura", "Vitacura"),
    ("lo barnechea", "Lo Barnechea"),
    ("providencia", "Providencia"),
    ("ñuñoa", "Ñuñoa"),
    ("nunoa", "Ñuñoa"),
    ("santiago centro", "Santiago"),
    ("santiago,", "Santiago"),
    ("la florida", "La Florida"),
    ("peñalolén", "Peñalolén"),
    ("penalolen", "Peñalolén"),
    ("puente alto", "Puente Alto"),
    ("la reina", "La Reina"),
    ("san miguel", "San Miguel"),
    ("maipú", "Maipú"),
    ("maipu", "Maipú"),
    ("quilicura", "Quilicura"),
    ("pudahuel", "Pudahuel"),
    ("cerrillos", "Cerrillos"),
    ("estación central", "Estación Central"),
    ("estacion central", "Estación Central"),
    ("macul", "Macul"),
    ("san joaquín", "San Joaquín"),
    ("san joaquin", "San Joaquín"),
    ("pedro aguirre cerda", "Pedro Aguirre Cerda"),
    ("lo espejo", "Lo Espejo"),
    ("lo prado", "Lo Prado"),
    ("cerro navia", "Cerro Navia"),
    ("renca", "Renca"),
    ("conchalí", "Conchalí"),
    ("conchali", "Conchalí"),
    ("huechuraba", "Huechuraba"),
    ("independencia", "Independencia"),
    ("recoleta", "Recoleta"),
    ("quinta normal", "Quinta Normal"),
    ("el bosque", "El Bosque"),
    ("san bernardo", "San Bernardo"),
    ("la pintana", "La Pintana"),
    ("la granja", "La Granja"),
    ("la cisterna", "La Cisterna"),
    ("buin", "Buin"),
    ("colina", "Colina"),
    ("lampa", "Lampa"),
    ("melipilla", "Melipilla"),
    ("pirque", "Pirque"),
    ("isla de maipo", "Isla de Maipo"),
    ("talagante", "Talagante"),
    ("peñaflor", "Peñaflor"),
    ("penaflor", "Peñaflor"),
    ("el monte", "El Monte"),
    ("curacaví", "Curacaví"),
    ("curacavi", "Curacaví"),
    ("tiltil", "Til Til"),
]


def _extract_commune_from_location(location_text: str) -> Optional[str]:
    """
    Try to identify a Chilean commune from a Portal Inmobiliario location string.
    Example inputs: "Las Condes, Región Metropolitana", "Depto. en Vitacura"
    """
    lower = location_text.lower()
    for fragment, canonical in _COMMUNE_FRAGMENTS:
        if fragment in lower:
            return canonical
    # Last attempt: first comma-separated segment title-cased
    parts = location_text.split(",")
    if parts:
        candidate = parts[0].strip()
        if 3 < len(candidate) < 40:
            return candidate
    return None
