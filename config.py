"""
Central configuration — loaded from .env via pydantic-settings.
All hardcoded URLs, secrets, and tuneable constants live here.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost/real_estate"

    # Anthropic
    anthropic_api_key: str = ""

    # Scraping
    portal_base_url: str = "https://www.portalinmobiliario.com"
    yapo_base_url: str = "https://www.yapo.cl"
    toctoc_base_url: str = "https://www.toctoc.com"
    scrape_interval_hours: int = 6
    scrape_delay_min_seconds: float = 2.0
    scrape_delay_max_seconds: float = 4.0

    # Raw HTML storage (save before parsing)
    raw_html_dir: str = "./raw_html"

    # Alerts
    alert_email: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Scoring thresholds
    score_high_threshold: float = 75.0
    score_medium_threshold: float = 60.0

    # Finance
    uf_value_clp: float = 38500.0  # approximate UF → CLP; update periodically

    # Logging
    log_level: str = "INFO"


settings = Settings()

# ---------------------------------------------------------------------------
# Corredor mapping — communes grouped by metro expansion zone
# ---------------------------------------------------------------------------

CORREDORES: dict[str, list[str]] = {
    "linea_7": ["Vitacura", "Las Condes", "Lo Barnechea"],
    "linea_8": ["Puente Alto", "La Florida", "Peñalolén"],
    "expansion": ["Ñuñoa", "Providencia", "Santiago"],
}

# Reverse lookup: commune → corredor name
COMMUNE_TO_CORREDOR: dict[str, str] = {
    commune: corredor
    for corredor, communes in CORREDORES.items()
    for commune in communes
}

# Priority communes for scraping
PRIORITY_COMMUNES = [
    "Vitacura", "Las Condes", "Lo Barnechea",
    "Puente Alto", "La Florida", "Peñalolén",
    "Ñuñoa", "Providencia", "Santiago",
]

# All property types to scrape (includes terrenos for full RM sweep)
ALL_TIPOS = ["departamento", "casa", "terreno"]
DEFAULT_TIPOS = ["departamento", "casa"]

# m² buckets for corridor statistics
M2_BUCKETS: list[tuple[float, float]] = [
    (0.0, 50.0),
    (50.0, 80.0),
    (80.0, 120.0),
    (120.0, float("inf")),
]
