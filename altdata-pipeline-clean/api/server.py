from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from loguru import logger

from storage.database import Database
from connectors.bloomberg_connector import BloombergConnector
from config.settings import API_HOST, API_PORT, TICKERS_BBG

app = FastAPI(
    title="SysFund AltData API",
    description="Systematic Fund - Alternative Data Signal Server",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# main.py crea el archivo + schema (bootstrap) antes de levantar este server.
# Abrimos en read-write: DuckDB permite varias conexiones read-write al mismo
# archivo dentro del mismo proceso, asi el orquestador (en otro thread) escribe
# en paralelo sin chocar.
db = Database()

# Bloomberg es opcional: sin Terminal/blpapi el connector devuelve datos demo,
# asi que /prices funciona igual para alimentar el dashboard.
bbg = BloombergConnector()

# (key interno BCCh/BBG, label que muestra el dashboard)
PRICE_TICKERS = [
    ("ipsa", "IPSA"), ("falabella", "FALABELLA"), ("sqm", "SQM/B"),
    ("cencosud", "CENCOSUD"), ("copec", "COPEC"), ("banco_chile", "CHILE"),
    ("clp_usd", "USDCLP"), ("tpm", "BCH TPM"),
]


@app.get("/")
def root():
    return {
        "status": "online",
        "time": datetime.now().isoformat(),
        "version": "0.1.0",
    }


@app.get("/signals")
def get_signals(
    min_confidence: float = Query(0.70, ge=0, le=1),
    urgency: str = Query(None, description="HIGH|MEDIUM|LOW"),
    hours: int = Query(48, ge=1, le=168),
):
    signals = db.get_active_signals(
        min_confidence=min_confidence,
        urgency=urgency,
        hours=hours,
    )
    return {
        "count": len(signals),
        "signals": signals,
        "as_of": datetime.now().isoformat(),
    }


@app.get("/signals/high")
def get_high_urgency():
    signals = db.get_active_signals(min_confidence=0.75, urgency="HIGH", hours=24)
    return {"count": len(signals), "signals": signals}


@app.get("/macro")
def get_macro():
    return db.get_macro_snapshot()


@app.get("/real-estate/{comuna}")
def get_re_stats(comuna: str):
    stats = db.get_re_stats(comuna)
    if stats["n"] == 0:
        raise HTTPException(status_code=404, detail=f"Sin datos para {comuna}")
    return {
        "comuna": comuna,
        "stats": stats,
        "as_of": datetime.now().isoformat(),
    }


@app.get("/pipeline/status")
def pipeline_status():
    rows = db.conn.execute("""
        SELECT source, status, started_at, records, error
        FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT 20
    """).fetchall()
    return {
        "runs": [
            {
                "source":     r[0],
                "status":     r[1],
                "started_at": str(r[2]),
                "records":    r[3],
                "error":      r[4],
            }
            for r in rows
        ]
    }


@app.get("/cost")
def cost_estimate():
    total_signals = db.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    total_hechos  = db.conn.execute("SELECT COUNT(*) FROM hechos_esenciales").fetchone()[0]
    tokens_used = total_signals * 850
    cost_usd = tokens_used / 1_000_000 * 3
    return {
        "total_signals_processed": total_signals,
        "total_hechos_scraped": total_hechos,
        "estimated_tokens": tokens_used,
        "estimated_cost_usd": round(cost_usd, 4),
    }


@app.get("/prices")
def get_prices():
    keys = [k for k, _ in PRICE_TICKERS]
    raw = bbg.get_current_price(keys)
    tickers = [
        {
            "key":   label,
            "name":  TICKERS_BBG.get(k, label),
            "price": (raw.get(k) or {}).get("price"),
            "chg":   (raw.get(k) or {}).get("chg_pct") or 0,
        }
        for k, label in PRICE_TICKERS
    ]
    return {
        "tickers": tickers,
        "source":  "bloomberg" if bbg.available else "demo",
    }


@app.get("/prices/{ticker_key}/history")
def price_history(ticker_key: str, days: int = Query(120, ge=10, le=365)):
    df = bbg.get_historical(ticker_key, fields=["PX_LAST"])
    if df is None or df.empty:
        return {"ticker": ticker_key, "series": []}
    df = df.tail(int(days))
    series = [
        {"date": idx.strftime("%d %b"), "price": round(float(val), 2)}
        for idx, val in df["PX_LAST"].items()
    ]
    return {
        "ticker": ticker_key,
        "series": series,
        "source": "bloomberg" if bbg.available else "demo",
    }


@app.get("/real-estate")
def re_feed(comuna: str = "Las Condes", limit: int = Query(8, ge=1, le=50)):
    stats  = db.get_re_stats(comuna)
    median = stats.get("median_uf_m2") or 0
    rows = db.conn.execute(
        """
        SELECT titulo, precio_uf, m2, precio_uf_m2
        FROM real_estate
        WHERE comuna = ? AND precio_uf_m2 IS NOT NULL
        ORDER BY precio_uf_m2 ASC
        LIMIT ?
        """,
        [comuna, int(limit)],
    ).fetchall()
    deals = [
        {
            "title":     r[0],
            "uf":        round(r[1]) if r[1] is not None else None,
            "m2":        round(r[2]) if r[2] is not None else None,
            "uf_m2":     round(r[3], 1),
            "delta_pct": round((r[3] / median - 1) * 100, 1) if median else 0.0,
        }
        for r in rows
    ]
    return {
        "comuna":       comuna,
        "median_uf_m2": median,
        "n":            stats.get("n", 0),
        "deals":        deals,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host=API_HOST, port=API_PORT, reload=True, log_level="info")
