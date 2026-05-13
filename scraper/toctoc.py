"""
TocToc scraper — uses their partial public API where available,
falls back to HTML scraping.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import settings, PRIORITY_COMMUNES
from scraper.base import normalize_commune, parse_m2, random_user_agent, rate_limit, save_raw_html

log = structlog.get_logger(__name__)

TOCTOC_API_BASE = f"{settings.toctoc_base_url}/api"
TOCTOC_SEARCH = f"{settings.toctoc_base_url}/propiedades"

_COMMUNE_SLUGS: dict[str, str] = {
    "Las Condes": "las-condes",
    "Vitacura": "vitacura",
    "Lo Barnechea": "lo-barnechea",
    "Providencia": "providencia",
    "Ñuñoa": "nunoa",
    "Santiago": "santiago",
    "La Florida": "la-florida",
    "Peñalolén": "penalolen",
    "Puente Alto": "puente-alto",
}


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> Any:
    resp = await client.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


async def scrape_commune(
    commune: str,
    tipo: str = "departamento",
    max_pages: int = 3,
) -> list[dict]:
    """Attempt TocToc API first; fall back to HTML scraping."""
    try:
        return await _scrape_via_api(commune, tipo, max_pages)
    except Exception as exc:
        log.warning("toctoc_api_failed", commune=commune, error=str(exc))
        return await _scrape_via_html(commune, tipo, max_pages)


async def _scrape_via_api(
    commune: str,
    tipo: str,
    max_pages: int,
) -> list[dict]:
    headers = {
        "User-Agent": random_user_agent(),
        "Accept": "application/json",
    }
    listings: list[dict] = []

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            params = {
                "operation": "sale",
                "property_type": tipo,
                "commune": commune,
                "page": page,
                "limit": 48,
            }
            try:
                data = await _get_json(client, f"{TOCTOC_API_BASE}/properties/search", params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    break
                raise

            items = data.get("data", data.get("results", []))
            if not items:
                break

            for item in items:
                parsed = _parse_api_item(item, commune, tipo)
                if parsed:
                    listings.append(parsed)

            await rate_limit()

    return listings


def _parse_api_item(item: dict, commune: str, tipo: str) -> Optional[dict]:
    precio = item.get("price") or item.get("precio")
    m2 = item.get("surface") or item.get("m2") or item.get("area")
    if not precio or not m2:
        return None

    precio = int(precio)
    m2 = float(m2)
    if m2 <= 0:
        return None

    is_uf = str(item.get("currency", "")).upper() == "UF"
    if is_uf:
        precio_uf = float(precio)
        precio_clp = int(precio_uf * settings.uf_value_clp)
    else:
        precio_clp = precio
        precio_uf = precio_clp / settings.uf_value_clp

    url = item.get("url", item.get("link", ""))
    if url and not url.startswith("http"):
        url = f"{settings.toctoc_base_url}{url}"

    return {
        "external_id": str(item.get("id", item.get("external_id", url[-20:]))),
        "source": "toctoc",
        "tipo_propiedad": tipo,
        "comuna": normalize_commune(commune),
        "address": item.get("address") or item.get("direccion"),
        "precio": precio_clp,
        "precio_uf": round(precio_uf, 2),
        "m2": m2,
        "precio_m2": round(precio_clp / m2, 2),
        "dormitorios": item.get("bedrooms") or item.get("dormitorios"),
        "banos": item.get("bathrooms") or item.get("banos"),
        "url": url,
        "fecha_publicacion": None,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


async def _scrape_via_html(
    commune: str,
    tipo: str,
    max_pages: int,
) -> list[dict]:
    from bs4 import BeautifulSoup

    slug = _COMMUNE_SLUGS.get(commune, commune.lower().replace(" ", "-"))
    headers = {"User-Agent": random_user_agent()}
    listings: list[dict] = []

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20) as client:
        for page_num in range(1, max_pages + 1):
            url = f"{TOCTOC_SEARCH}/{tipo}/venta/{slug}?page={page_num}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.error("toctoc_html_fetch_failed", url=url, error=str(exc))
                break

            html = resp.text
            save_raw_html(html, "toctoc", f"{slug}_{tipo}_p{page_num}")
            soup = BeautifulSoup(html, "lxml")

            for card in soup.select("article.property-card, div[class*='PropertyCard'], li.property-item"):
                try:
                    item = _parse_html_card(card, commune, tipo)
                    if item:
                        listings.append(item)
                except Exception as exc:
                    log.debug("toctoc_card_error", error=str(exc))

            await rate_limit()

    return listings


def _parse_html_card(card, commune: str, tipo: str) -> Optional[dict]:
    link = card.select_one("a[href]")
    if not link:
        return None
    url = link.get("href", "")
    if not url.startswith("http"):
        url = f"{settings.toctoc_base_url}{url}"

    external_id = re.search(r"/(\d+)(?:\?|$)", url)
    ext_id = external_id.group(1) if external_id else url[-20:]

    price_tag = card.select_one("[class*='price'], [class*='Price']")
    if not price_tag:
        return None
    raw = price_tag.get_text(strip=True)
    is_uf = "UF" in raw.upper()
    digits = re.sub(r"[^\d.]", "", raw)
    if not digits:
        return None
    precio_num = float(digits)
    if is_uf:
        precio_uf = precio_num
        precio_clp = int(precio_uf * settings.uf_value_clp)
    else:
        precio_clp = int(precio_num)
        precio_uf = precio_clp / settings.uf_value_clp

    m2 = None
    for tag in card.select("[class*='feature'], [class*='attribute'], span"):
        t = tag.get_text(strip=True).lower()
        if "m²" in t or "m2" in t:
            m2 = parse_m2(t)
            if m2:
                break
    if not m2 or m2 <= 0:
        return None

    return {
        "external_id": ext_id,
        "source": "toctoc",
        "tipo_propiedad": tipo,
        "comuna": normalize_commune(commune),
        "address": None,
        "precio": precio_clp,
        "precio_uf": round(precio_uf, 2),
        "m2": m2,
        "precio_m2": round(precio_clp / m2, 2),
        "dormitorios": None,
        "banos": None,
        "url": url,
        "fecha_publicacion": None,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


async def scrape_all_priority_communes(
    tipos: list[str] | None = None,
    max_pages: int = 3,
) -> list[dict]:
    if tipos is None:
        tipos = ["departamento"]
    all_listings: list[dict] = []
    for commune in PRIORITY_COMMUNES:
        for tipo in tipos:
            listings = await scrape_commune(commune, tipo, max_pages)
            all_listings.extend(listings)
    return all_listings
