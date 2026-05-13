"""
SII Bienes Raíces avalúo fiscal lookup.

homer.sii.cl has no public API — requires scraping with a roll number (rol).
Rate-limited to respect SII servers.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

import httpx
import structlog
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

SII_BASE = "https://homer.sii.cl"
SII_SEARCH_URL = f"{SII_BASE}/miiic/contrib/identificacion_del_contribuyente_predial_action.php"

# CLP → UF conversion helper (use value from config)
from config import settings


def clp_to_uf(clp: int) -> float:
    return clp / settings.uf_value_clp


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
async def fetch_avaluo_by_rol(
    client: httpx.AsyncClient,
    comuna_sii: str,
    manzana: str,
    predio: str,
) -> Optional[dict]:
    """
    Look up avalúo fiscal for a property rol on homer.sii.cl.

    Args:
        client: shared httpx async client
        comuna_sii: SII commune code (2-digit string)
        manzana: block number
        predio: lot number

    Returns dict with keys: avaluo_fiscal, avaluo_exento, contribucion_anual, rol
    or None if lookup fails.
    """
    params = {
        "RUT_PROPIETARIO": "",
        "CODIGO_COMUNA": comuna_sii,
        "NUMERO_MANZANA": manzana,
        "NUMERO_PREDIO": predio,
        "BOTON_BUSCAR": "Buscar",
    }
    try:
        resp = await client.get(SII_SEARCH_URL, params=params, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.warning("sii_http_error", status=exc.response.status_code, rol=f"{manzana}-{predio}")
        return None

    await asyncio.sleep(settings.scrape_delay_min_seconds)
    return _parse_sii_response(resp.text)


def _parse_sii_response(html: str) -> Optional[dict]:
    """Parse avalúo fiscal from SII HTML response."""
    soup = BeautifulSoup(html, "lxml")

    # SII table rows contain "Avalúo Total", "Avalúo Exento", "Contribución"
    result: dict = {}

    def _extract_clp(text: str) -> Optional[int]:
        clean = re.sub(r"[^\d]", "", text)
        return int(clean) if clean else None

    for row in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        label, *values = cells
        label_lower = label.lower()
        if "avalúo total" in label_lower or "avaluo total" in label_lower:
            result["avaluo_fiscal"] = _extract_clp(values[0]) if values else None
        elif "avalúo exento" in label_lower or "avaluo exento" in label_lower:
            result["avaluo_exento"] = _extract_clp(values[0]) if values else None
        elif "contribución" in label_lower or "contribucion" in label_lower:
            result["contribucion_anual"] = _extract_clp(values[0]) if values else None

    # Extract rol from page
    rol_match = re.search(r"Rol\s*[:\-]?\s*(\d+\s*[-/]\s*\d+)", html, re.IGNORECASE)
    if rol_match:
        result["rol"] = rol_match.group(1).replace(" ", "")

    if not result.get("avaluo_fiscal"):
        log.debug("sii_no_avaluo_found")
        return None

    return result


async def batch_lookup_avaluos(
    properties: list[dict],
) -> dict[int, Optional[dict]]:
    """
    Lookup SII avalúo for multiple properties.
    Properties must have 'id', 'rol_manzana', 'rol_predio', 'comuna_sii_code'.
    Returns mapping of property_id → avaluo result dict.
    """
    results: dict[int, Optional[dict]] = {}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for prop in properties:
            prop_id = prop["id"]
            try:
                result = await fetch_avaluo_by_rol(
                    client,
                    comuna_sii=prop.get("comuna_sii_code", ""),
                    manzana=prop.get("rol_manzana", ""),
                    predio=prop.get("rol_predio", ""),
                )
                results[prop_id] = result
            except Exception as exc:
                log.error("sii_lookup_failed", property_id=prop_id, error=str(exc))
                results[prop_id] = None
            await asyncio.sleep(settings.scrape_delay_min_seconds)
    return results
