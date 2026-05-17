import duckdb
import json
import uuid
import hashlib
from datetime import datetime
from loguru import logger
from config.settings import DUCKDB_PATH


class Database:
    def __init__(self):
        self.conn = duckdb.connect(DUCKDB_PATH)
        self._create_tables()
        logger.info(f"DuckDB conectado: {DUCKDB_PATH}")

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS hechos_esenciales (
                id           VARCHAR PRIMARY KEY,
                fecha        DATE,
                empresa      VARCHAR,
                rut_emisor   VARCHAR,
                tipo         VARCHAR,
                descripcion  TEXT,
                url          VARCHAR,
                source       VARCHAR,
                scraped_at   TIMESTAMP,
                processed    BOOLEAN DEFAULT FALSE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id               VARCHAR PRIMARY KEY,
                source_type      VARCHAR,
                source_date      DATE,
                processed_at     TIMESTAMP,
                signal           VARCHAR,
                confidence       FLOAT,
                category         VARCHAR,
                time_horizon     VARCHAR,
                affected_ticker  VARCHAR,
                affected_sector  VARCHAR,
                reasoning        TEXT,
                action           TEXT,
                urgency          VARCHAR,
                raw_json         VARCHAR
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_series (
                fecha       DATE,
                serie       VARCHAR,
                valor       FLOAT,
                scraped_at  TIMESTAMP,
                PRIMARY KEY (fecha, serie)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS real_estate (
                id          VARCHAR PRIMARY KEY,
                titulo      VARCHAR,
                precio_uf   FLOAT,
                m2          FLOAT,
                precio_uf_m2 FLOAT,
                ubicacion   VARCHAR,
                comuna      VARCHAR,
                tipo        VARCHAR,
                source      VARCHAR,
                scraped_at  TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id         VARCHAR PRIMARY KEY,
                source     VARCHAR,
                started_at TIMESTAMP,
                ended_at   TIMESTAMP,
                status     VARCHAR,
                records    INTEGER,
                error      TEXT
            )
        """)

    # ─── HECHOS ───────────────────────────────────────
    def insert_hecho(self, hecho: dict) -> str:
        hecho_id = hashlib.md5(
            f"{hecho.get('rut_emisor','')}_{hecho.get('fecha','')}_{hecho.get('descripcion','')[:50]}"
            .encode()
        ).hexdigest()
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO hechos_esenciales
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, [
                hecho_id,
                hecho.get("fecha"),
                hecho.get("empresa"),
                hecho.get("rut_emisor"),
                hecho.get("tipo_documento"),
                hecho.get("descripcion"),
                hecho.get("url_documento"),
                hecho.get("source"),
                hecho.get("scraped_at", datetime.now().isoformat()),
                False,
            ])
        except Exception as e:
            logger.error(f"insert_hecho error: {e}")
        return hecho_id

    def get_unprocessed_hechos(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(f"""
            SELECT id, fecha, empresa, rut_emisor, tipo, descripcion
            FROM hechos_esenciales
            WHERE processed = FALSE
            ORDER BY fecha DESC
            LIMIT {limit}
        """).fetchall()
        return [
            {
                "id": r[0], "fecha": str(r[1]),
                "empresa": r[2], "rut_emisor": r[3],
                "tipo_documento": r[4], "descripcion": r[5],
            }
            for r in rows
        ]

    def mark_hecho_processed(self, hecho_id: str):
        self.conn.execute(
            "UPDATE hechos_esenciales SET processed=TRUE WHERE id=?",
            [hecho_id]
        )

    # ─── SIGNALS ──────────────────────────────────────
    def insert_signal(self, signal: dict):
        signal_id = str(uuid.uuid4())
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO signals VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [
                signal_id,
                signal.get("source_type", "CMF"),
                signal.get("source_date"),
                signal.get("processed_at", datetime.now().isoformat()),
                signal.get("signal"),
                signal.get("confidence"),
                signal.get("category"),
                signal.get("time_horizon"),
                signal.get("affected_ticker"),
                signal.get("affected_sector"),
                signal.get("reasoning"),
                signal.get("action"),
                signal.get("urgency"),
                json.dumps(signal),
            ])
        except Exception as e:
            logger.error(f"insert_signal error: {e}")

    def get_active_signals(
        self,
        min_confidence: float = 0.70,
        urgency: str = None,
        hours: int = 48,
    ) -> list[dict]:
        query = f"""
            SELECT id, source_type, source_date, processed_at,
                   signal, confidence, category, time_horizon,
                   affected_ticker, affected_sector, reasoning,
                   action, urgency
            FROM signals
            WHERE processed_at >= NOW() - INTERVAL '{hours} hours'
            AND confidence >= {min_confidence}
        """
        if urgency:
            query += f" AND urgency = '{urgency}'"
        query += " ORDER BY confidence DESC, processed_at DESC"

        rows = self.conn.execute(query).fetchall()
        cols = [
            "id","source_type","source_date","processed_at",
            "signal","confidence","category","time_horizon",
            "affected_ticker","affected_sector","reasoning",
            "action","urgency"
        ]
        return [dict(zip(cols, r)) for r in rows]

    # ─── MACRO ────────────────────────────────────────
    def upsert_macro(self, fecha: str, serie: str, valor: float):
        self.conn.execute("""
            INSERT OR REPLACE INTO macro_series VALUES (?,?,?,?)
        """, [fecha, serie, valor, datetime.now().isoformat()])

    def get_macro_snapshot(self) -> dict:
        rows = self.conn.execute("""
            SELECT serie, valor, fecha
            FROM (
                SELECT serie, valor, fecha,
                       ROW_NUMBER() OVER (PARTITION BY serie ORDER BY fecha DESC) as rn
                FROM macro_series
            ) t WHERE rn = 1
        """).fetchall()
        return {r[0]: {"valor": r[1], "fecha": str(r[2])} for r in rows}

    # ─── REAL ESTATE ──────────────────────────────────
    def insert_property(self, prop: dict, comuna: str, tipo: str):
        prop_id = hashlib.md5(
            f"{prop.get('titulo','')}_{prop.get('precio_uf','')}_{prop.get('m2','')}"
            .encode()
        ).hexdigest()
        self.conn.execute("""
            INSERT OR IGNORE INTO real_estate VALUES (?,?,?,?,?,?,?,?,?,?)
        """, [
            prop_id,
            prop.get("titulo"),
            prop.get("precio_uf"),
            prop.get("m2"),
            prop.get("precio_uf_m2"),
            prop.get("ubicacion"),
            comuna,
            tipo,
            prop.get("source", "portal_inmobiliario"),
            datetime.now().isoformat(),
        ])

    def get_re_stats(self, comuna: str) -> dict:
        row = self.conn.execute("""
            SELECT 
                AVG(precio_uf_m2) as avg_uf_m2,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY precio_uf_m2) as median_uf_m2,
                MIN(precio_uf_m2) as min_uf_m2,
                MAX(precio_uf_m2) as max_uf_m2,
                COUNT(*) as n
            FROM real_estate
            WHERE comuna = ? AND precio_uf_m2 IS NOT NULL
            AND scraped_at >= NOW() - INTERVAL '7 days'
        """, [comuna]).fetchone()
        return {
            "avg_uf_m2":    round(row[0] or 0, 1),
            "median_uf_m2": round(row[1] or 0, 1),
            "min_uf_m2":    round(row[2] or 0, 1),
            "max_uf_m2":    round(row[3] or 0, 1),
            "n":            row[4] or 0,
        }

    # ─── PIPELINE RUNS ────────────────────────────────
    def log_run(
        self, source: str, started_at: datetime,
        status: str, records: int = 0, error: str = None
    ):
        self.conn.execute("""
            INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?)
        """, [
            str(uuid.uuid4()), source,
            started_at.isoformat(), datetime.now().isoformat(),
            status, records, error,
        ])
