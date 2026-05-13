"""Initial schema — properties, price_history, corridor_stats, sii_avaluos, alerts

Revision ID: 001
Revises:
Create Date: 2026-05-13

"""
from __future__ import annotations
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- ENUM types ---
    property_source = sa.Enum(
        "portal_inmobiliario", "yapo", "toctoc",
        name="propertysource",
    )
    property_type = sa.Enum(
        "departamento", "casa", "oficina", "local", "terreno",
        name="propertytype",
    )
    alert_level = sa.Enum("HIGH", "MEDIUM", name="alertlevel")

    # --- properties ---
    op.create_table(
        "properties",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("source", property_source, nullable=False),
        sa.Column("tipo_propiedad", property_type, nullable=False),
        sa.Column("comuna", sa.String(100), nullable=False),
        sa.Column("corredor", sa.String(100), nullable=True),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("region", sa.String(100), nullable=False, server_default="Región Metropolitana"),
        sa.Column("precio", sa.Integer, nullable=False),
        sa.Column("precio_uf", sa.Float, nullable=True),
        sa.Column("precio_inicial", sa.Integer, nullable=True),
        sa.Column("m2", sa.Float, nullable=False),
        sa.Column("precio_m2", sa.Float, nullable=False),
        sa.Column("dormitorios", sa.Integer, nullable=True),
        sa.Column("banos", sa.Integer, nullable=True),
        sa.Column("estacionamientos", sa.Integer, nullable=True),
        sa.Column("descripcion", sa.Text, nullable=True),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("score_price_m2", sa.Float, nullable=True),
        sa.Column("score_delta_fiscal", sa.Float, nullable=True),
        sa.Column("score_time_on_market", sa.Float, nullable=True),
        sa.Column("score_price_reduction", sa.Float, nullable=True),
        sa.Column(
            "fecha_publicacion",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW() AT TIME ZONE 'UTC'"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW() AT TIME ZONE 'UTC'"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
    )
    op.create_unique_constraint(
        "uq_property_source_external", "properties", ["source", "external_id"]
    )
    op.create_index("ix_properties_comuna_tipo_score", "properties", ["comuna", "tipo_propiedad", "score"])
    op.create_index("ix_properties_created_at", "properties", ["created_at"])
    op.create_index("ix_properties_is_active_score", "properties", ["is_active", "score"])
    op.create_index("ix_properties_corredor", "properties", ["corredor"])

    # --- price_history ---
    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("precio", sa.Integer, nullable=False),
        sa.Column("precio_uf", sa.Float, nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW() AT TIME ZONE 'UTC'"),
            nullable=False,
        ),
    )
    op.create_index("ix_price_history_property_id", "price_history", ["property_id"])

    # --- corridor_stats ---
    op.create_table(
        "corridor_stats",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tipo_propiedad", property_type, nullable=False),
        sa.Column("comuna", sa.String(100), nullable=False),
        sa.Column("m2_min", sa.Float, nullable=False),
        sa.Column("m2_max", sa.Float, nullable=False),
        sa.Column("median_precio_m2", sa.Float, nullable=False),
        sa.Column("mean_precio_m2", sa.Float, nullable=True),
        sa.Column("p25_precio_m2", sa.Float, nullable=True),
        sa.Column("p75_precio_m2", sa.Float, nullable=True),
        sa.Column("n_properties", sa.Integer, nullable=False),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW() AT TIME ZONE 'UTC'"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_corridor_stats_lookup",
        "corridor_stats",
        ["tipo_propiedad", "comuna", "m2_min", "m2_max"],
    )

    # --- sii_avaluos ---
    op.create_table(
        "sii_avaluos",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("rol_propiedad", sa.String(50), nullable=True),
        sa.Column("avaluo_fiscal", sa.Integer, nullable=False),
        sa.Column("avaluo_exento", sa.Integer, nullable=True),
        sa.Column("contribucion_anual", sa.Integer, nullable=True),
        sa.Column(
            "looked_up_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW() AT TIME ZONE 'UTC'"),
            nullable=False,
        ),
    )

    # --- alerts ---
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("property_id", sa.Integer, sa.ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("alert_level", alert_level, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW() AT TIME ZONE 'UTC'"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
    )
    op.create_index("ix_alerts_property_id", "alerts", ["property_id"])
    op.create_index("ix_alerts_created_at_level", "alerts", ["created_at", "alert_level"])
    op.create_index("ix_alerts_is_active", "alerts", ["is_active"])


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("sii_avaluos")
    op.drop_table("corridor_stats")
    op.drop_table("price_history")
    op.drop_table("properties")

    for enum_name in ("alertlevel", "propertytype", "propertysource"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
