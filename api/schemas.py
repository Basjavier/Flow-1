"""
Pydantic v2 request/response schemas for the FastAPI layer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Property schemas
# ---------------------------------------------------------------------------


class PropertyBase(BaseModel):
    external_id: str
    source: str
    tipo_propiedad: str
    comuna: str
    corredor: Optional[str] = None
    address: Optional[str] = None
    region: str = "Región Metropolitana"
    precio: int
    precio_uf: Optional[float] = None
    m2: float
    precio_m2: float
    dormitorios: Optional[int] = None
    banos: Optional[int] = None
    estacionamientos: Optional[int] = None
    descripcion: Optional[str] = None
    url: str
    fecha_publicacion: Optional[datetime] = None


class PropertyCreate(PropertyBase):
    precio_inicial: Optional[int] = None


class PropertyResponse(PropertyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    precio_inicial: Optional[int] = None
    score: Optional[float] = None
    score_price_m2: Optional[float] = None
    score_delta_fiscal: Optional[float] = None
    score_time_on_market: Optional[float] = None
    score_price_reduction: Optional[float] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PropertyListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PropertyResponse]


class PropertyFilter(BaseModel):
    comuna: Optional[str] = None
    tipo_propiedad: Optional[str] = None
    corredor: Optional[str] = None
    min_score: Optional[float] = None
    max_precio: Optional[int] = None
    min_m2: Optional[float] = None
    max_m2: Optional[float] = None
    source: Optional[str] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


# ---------------------------------------------------------------------------
# Alert schemas
# ---------------------------------------------------------------------------


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    property_id: int
    score: float
    alert_level: str
    sent_at: Optional[datetime] = None
    created_at: datetime
    is_active: bool
    property: Optional[PropertyResponse] = None


class AlertListResponse(BaseModel):
    total: int
    items: list[AlertResponse]


# ---------------------------------------------------------------------------
# Report schemas
# ---------------------------------------------------------------------------


class PortfolioSummary(BaseModel):
    total_active: int
    high_opportunity: int
    medium_opportunity: int
    avg_score: Optional[float] = None
    avg_precio_m2: Optional[float] = None
    n_comunas: int
    last_scraped_at: Optional[datetime] = None


class CommuneSummary(BaseModel):
    comuna: str
    corredor: Optional[str] = None
    n_properties: int
    avg_score: Optional[float] = None
    avg_precio_m2: Optional[float] = None
    median_precio_m2: Optional[float] = None
    min_precio: Optional[int] = None
    max_precio: Optional[int] = None


class CorridorStatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo_propiedad: str
    comuna: str
    m2_min: float
    m2_max: float
    median_precio_m2: float
    mean_precio_m2: Optional[float] = None
    p25_precio_m2: Optional[float] = None
    p75_precio_m2: Optional[float] = None
    n_properties: int
    calculated_at: datetime


# ---------------------------------------------------------------------------
# Scraper trigger schemas
# ---------------------------------------------------------------------------


class ScrapeRequest(BaseModel):
    sources: list[str] = Field(
        default=["portal_inmobiliario"],
        description="Sources to scrape: portal_inmobiliario, yapo, toctoc",
    )
    communes: Optional[list[str]] = None
    tipos: list[str] = Field(default=["departamento"])
    max_pages: int = Field(default=5, ge=1, le=20)


class ScrapeResponse(BaseModel):
    job_id: str
    status: str
    message: str
