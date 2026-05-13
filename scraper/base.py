"""
Shared scraper utilities: rate-limiting, user-agent rotation, HTML persistence.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from config import settings

log = structlog.get_logger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


async def rate_limit() -> None:
    """Sleep between requests — 2-4 second window (configurable)."""
    delay = random.uniform(
        settings.scrape_delay_min_seconds,
        settings.scrape_delay_max_seconds,
    )
    await asyncio.sleep(delay)


def save_raw_html(html: str, source: str, identifier: str) -> Path:
    """
    Persist raw HTML before parsing — allows re-parsing without re-scraping.
    Saved to {raw_html_dir}/{source}/{date}/{identifier}.html
    """
    date_str = datetime.utcnow().strftime("%Y%m%d")
    dest_dir = Path(settings.raw_html_dir) / source / date_str
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_id = identifier.replace("/", "_").replace(":", "_")[:100]
    path = dest_dir / f"{safe_id}.html"
    path.write_text(html, encoding="utf-8")
    return path


def normalize_commune(raw: str) -> str:
    """Normalise commune names to canonical Chilean form."""
    mapping = {
        "las condes": "Las Condes",
        "vitacura": "Vitacura",
        "lo barnechea": "Lo Barnechea",
        "providencia": "Providencia",
        "ñuñoa": "Ñuñoa",
        "nunoa": "Ñuñoa",
        "santiago": "Santiago",
        "santiago centro": "Santiago",
        "la florida": "La Florida",
        "peñalolén": "Peñalolén",
        "penalolen": "Peñalolén",
        "puente alto": "Puente Alto",
    }
    return mapping.get(raw.strip().lower(), raw.strip().title())


def parse_price_clp(text: str) -> Optional[int]:
    """Extract integer CLP price from strings like '$85.000.000' or 'UF 2.300'."""
    import re
    clean = re.sub(r"[^\d]", "", text.replace(".", "").replace(",", ""))
    return int(clean) if clean else None


def parse_m2(text: str) -> Optional[float]:
    """Extract m² value from strings like '65 m²' or '65.5m2'."""
    import re
    m = re.search(r"(\d+[\.,]?\d*)\s*m", text.lower())
    if m:
        return float(m.group(1).replace(",", "."))
    return None
