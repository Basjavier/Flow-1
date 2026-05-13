"""
SQLAlchemy 2.0 ORM models.

Conventions:
- Never DELETE — use is_active = False (soft delete)
- All timestamps are UTC
- Indices on: (comuna, tipo_propiedad, score, created_at)
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PropertySource(str, enum.Enum):
    PORTAL_INMOBILIARIO = "portal_inmobiliario"
    YAPO = "yapo"
    TOCTOC = "toctoc"


class PropertyType(str, enum.Enum):
    DEPARTAMENTO = "departamento"
    CASA = "casa"
    OFICINA = "oficina"
    LOCAL = "local"
    TERRENO = "terreno"


class AlertLevel(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[PropertySource] = mapped_column(SAEnum(PropertySource), nullable=False)
    tipo_propiedad: Mapped[PropertyType] = mapped_column(SAEnum(PropertyType), nullable=False)

    # Location
    comuna: Mapped[str] = mapped_column(String(100), nullable=False)
    corredor: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    region: Mapped[str] = mapped_column(String(100), nullable=False, default="Región Metropolitana")

    # Price (stored in CLP; UF snapshot at scrape time also saved)
    precio: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_uf: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precio_inicial: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Size
    m2: Mapped[float] = mapped_column(Float, nullable=False)
    precio_m2: Mapped[float] = mapped_column(Float, nullable=False)  # CLP/m²

    # Characteristics
    dormitorios: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    banos: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estacionamientos: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    descripcion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)

    # Scoring
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_price_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_delta_fiscal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_time_on_market: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_price_reduction: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Dates
    fecha_publicacion: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    price_history: Mapped[List["PriceHistory"]] = relationship(
        back_populates="property", cascade="all, delete-orphan", lazy="selectin"
    )
    alerts: Mapped[List["Alert"]] = relationship(
        back_populates="property", cascade="all, delete-orphan", lazy="selectin"
    )
    sii_avaluo: Mapped[Optional["SIIAvaluo"]] = relationship(
        back_populates="property", uselist=False, lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_property_source_external"),
        Index("ix_properties_comuna_tipo_score", "comuna", "tipo_propiedad", "score"),
        Index("ix_properties_created_at", "created_at"),
        Index("ix_properties_is_active_score", "is_active", "score"),
        Index("ix_properties_corredor", "corredor"),
    )

    @property
    def days_on_market(self) -> Optional[int]:
        if self.fecha_publicacion is None:
            return None
        delta = datetime.utcnow() - self.fecha_publicacion.replace(tzinfo=None)
        return delta.days

    @property
    def price_reduction_pct(self) -> Optional[float]:
        if self.precio_inicial and self.precio_inicial > self.precio:
            return (self.precio_inicial - self.precio) / self.precio_inicial
        return None


# ---------------------------------------------------------------------------
# Price History
# ---------------------------------------------------------------------------


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False
    )
    precio: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_uf: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    property: Mapped["Property"] = relationship(back_populates="price_history")

    __table_args__ = (Index("ix_price_history_property_id", "property_id"),)


# ---------------------------------------------------------------------------
# Corridor Statistics — pre-computed medians per (type, commune, m² bucket)
# ---------------------------------------------------------------------------


class CorridorStat(Base):
    __tablename__ = "corridor_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo_propiedad: Mapped[PropertyType] = mapped_column(SAEnum(PropertyType), nullable=False)
    comuna: Mapped[str] = mapped_column(String(100), nullable=False)
    m2_min: Mapped[float] = mapped_column(Float, nullable=False)
    m2_max: Mapped[float] = mapped_column(Float, nullable=False)  # 9999 = unbounded
    median_precio_m2: Mapped[float] = mapped_column(Float, nullable=False)
    mean_precio_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p25_precio_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p75_precio_m2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    n_properties: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_corridor_stats_lookup",
            "tipo_propiedad",
            "comuna",
            "m2_min",
            "m2_max",
        ),
    )


# ---------------------------------------------------------------------------
# SII Avalúo Fiscal
# ---------------------------------------------------------------------------


class SIIAvaluo(Base):
    __tablename__ = "sii_avaluos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    rol_propiedad: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    avaluo_fiscal: Mapped[int] = mapped_column(Integer, nullable=False)  # CLP
    avaluo_exento: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contribucion_anual: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    looked_up_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    property: Mapped["Property"] = relationship(back_populates="sii_avaluo")


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    alert_level: Mapped[AlertLevel] = mapped_column(SAEnum(AlertLevel), nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    property: Mapped["Property"] = relationship(back_populates="alerts")

    __table_args__ = (
        Index("ix_alerts_property_id", "property_id"),
        Index("ix_alerts_created_at_level", "created_at", "alert_level"),
        Index("ix_alerts_is_active", "is_active"),
    )
