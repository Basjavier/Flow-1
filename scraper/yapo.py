"""
Yapo.cl property scraper — requests + BeautifulSoup (semi-structured).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
import structlog
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import settings, PRIORITY_COMMUNES
from scraper.base import normalize_commune, parse_m2, parse_price_clp, random_user_agent, rate_limit, save_raw_html

log = structlog.get_logger(__name__)

YAPO_SEARCH_URL = f"{settings.yapo_base_url}/propiedades/departamentos/venta"

_TIPO_PATH: dict[str, str] = {
    "departamento": "departamentos",
    "casa": "casas",
}


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
)
async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


async def scrape_commune(
    commune: str,
    tipo: str = "departamento",
    max_pages: int = 3,
) -> list[dict]:
    """Scrape Yapo listings for a single commune."""
    slug = commune.lower().replace(" ", "-").replace("ñ", "n")
    tipo_path = _TIPO_PATH.get(tipo, "departamentos")
    base_url = f"{settings.yapo_base_url}/propiedades/{tipo_path}/venta/region-metropolitana/{slug}"

    headers = {
        "User-Agent": random_user_agent(),
        "Accept-Language": "es-CL,es;q=0.9",
    }
    listings: list[dict] = []

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for page_num in range(1, max_pages + 1):
            url = f"{base_url}?page={page_num}" if page_num > 1 else base_url
            try:
                html = await _fetch(client, url)
            except Exception as exc:
                log.error("yapo_fetch_failed", url=url, error=str(exc))
                break

            save_raw_html(html, "yapo", f"{slug}_{tipo}_p{page_num}")
            page_listings = _parse_yapo_html(html, commune, tipo)
            if not page_listings:
                break
            listings.extend(page_listings)
            log.info("yapo_page_scraped", commune=commune, page=page_num, found=len(page_listings))
            await rate_limit()

    log.info("yapo_commune_done", commune=commune, tipo=tipo, total=len(listings))
    return listings


def _parse_yapo_html(html: str, commune: str, tipo: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    results = []

    for card in soup.select("div.card, article.listing-card, div[class*='ad-card']"):
        try:
            item = _parse_card(card, commune, tipo)
            if item:
                results.append(item)
        except Exception as exc:
            log.debug("yapo_card_parse_error", error=str(exc))

    return results


def _parse_card(card, commune: str, tipo: str) -> Optional[dict]:
    # Link
    link_tag = card.select_one("a[href]")
    if not link_tag:
        return None
    url = link_tag.get("href", "")
    if not url.startswith("http"):
        url = urljoin(settings.yapo_base_url, url)

    external_id_match = re.search(r"/(\d+)(?:\?|$|/)", url)
    external_id = external_id_match.group(1) if external_id_match else url[-20:]

    # Price
    price_tag = card.select_one("[class*='price'], [class*='precio']")
    if not price_tag:
        return None
    raw_price_text = price_tag.get_text(strip=True)
    precio_raw = parse_price_clp(raw_price_text)
    if not precio_raw or precio_raw < 1_000:
        return None

    is_uf = "uf" in raw_price_text.lower()
    if is_uf:
        precio_uf = float(precio_raw)
        precio_clp = int(precio_uf * settings.uf_value_clp)
    else:
        precio_clp = precio_raw
        precio_uf = precio_clp / settings.uf_value_clp

    # m²
    m2 = None
    for tag in card.select("[class*='detail'], [class*='feature'], span, li"):
        text = tag.get_text(strip=True).lower()
        if "m²" in text or "m2" in text:
            m2 = parse_m2(text)
            if m2:
                break
    if not m2 or m2 <= 0:
        return None

    # Bedrooms
    dormitorios = None
    for tag in card.select("[class*='detail'], [class*='feature'], span, li"):
        text = tag.get_text(strip=True).lower()
        if "dorm" in text or "hab" in text:
            m = re.search(r"(\d+)", text)
            dormitorios = int(m.group(1)) if m else None
            break

    precio_m2 = precio_clp / m2

    return {
        "external_id": external_id,
        "source": "yapo",
        "tipo_propiedad": tipo,
        "comuna": normalize_commune(commune),
        "address": None,
        "precio": precio_clp,
        "precio_uf": round(precio_uf, 2),
        "m2": m2,
        "precio_m2": round(precio_m2, 2),
        "dormitorios": dormitorios,
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
