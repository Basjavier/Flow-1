from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from loguru import logger

from storage.database import Database
from config.settings import API_HOST, API_PORT

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

# La API solo lee. main.py se asegura de que el archivo + schema existan
# antes de levantar este servidor, asi que aqui solo abrimos read-only
# (lo cual permite que el orquestador escriba en paralelo en el mismo
# proceso sin chocar).
db = Database()  # writer: DuckDB permite multiples writers en el mismo proceso


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host=API_HOST, port=API_PORT, reload=True, log_level="info")
