"""
SysFund AltData Pipeline — Entry Point

Modos de uso:
  python main.py              → pipeline completo con scheduler
  python main.py --once       → corre todo una vez y sale
  python main.py --demo       → modo demo sin internet
  python main.py --api-only   → solo levanta el servidor API
  python main.py --test       → test de conectividad
"""

import sys
import asyncio
import os
from loguru import logger

# Configurar logging
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level="INFO",
)
logger.add(
    "data/pipeline.log",
    rotation="10 MB",
    retention="30 days",
    level="DEBUG",
)


def run_pipeline(once: bool = False):
    from orchestrator.pipeline import AltDataPipeline
    pipeline = AltDataPipeline()

    if once:
        asyncio.run(pipeline.run_all_now())
        logger.success("Pipeline completado (modo --once)")
    else:
        pipeline.start(run_now=True)


def run_api():
    import uvicorn
    from config.settings import API_HOST, API_PORT
    logger.info(f"Iniciando API en http://{API_HOST}:{API_PORT}")
    uvicorn.run(
        "api.server:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="warning",
    )


async def run_tests():
    """Test rápido de todas las conexiones."""
    logger.info("=== TEST DE CONECTIVIDAD ===")
    results = {}

    from config.settings import anthropic_live, bcch_live

    # Test Anthropic
    if not anthropic_live():
        results["anthropic"] = "⚠️  demo — sin API key real; NLP usa fallback por reglas"
    else:
        try:
            from anthropic import Anthropic
            from config.settings import ANTHROPIC_API_KEY
            client = Anthropic(api_key=ANTHROPIC_API_KEY)
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            results["anthropic"] = "✅ OK"
        except Exception as e:
            results["anthropic"] = f"❌ {e}"

    # Test BCCh
    try:
        from scrapers.bcch_scraper import BCChScraper
        bcch = BCChScraper()
        data = await bcch.get_serie("tpm", first_date="2024-01-01")
        tag = "OK" if bcch_live() else "OK (demo)"
        results["bcch"] = f"✅ {tag} — {len(data)} registros"
    except Exception as e:
        results["bcch"] = f"❌ {e}"

    # Test DuckDB
    try:
        from storage.database import Database
        db = Database()
        db.conn.execute("SELECT 1")
        results["duckdb"] = "✅ OK"
    except Exception as e:
        results["duckdb"] = f"❌ {e}"

    # Test Bloomberg (opcional)
    try:
        from connectors.bloomberg_connector import BloombergConnector
        bbg = BloombergConnector()
        prices = bbg.get_current_price(["ipsa"])
        results["bloomberg"] = f"✅ OK — IPSA: {prices.get('ipsa', {}).get('price')}"
        bbg.disconnect()
    except Exception as e:
        results["bloomberg"] = f"⚠️  {e} (requiere Terminal abierto)"

    logger.info("\n" + "─"*40)
    for k, v in results.items():
        logger.info(f"  {k:15} {v}")
    logger.info("─"*40)


if __name__ == "__main__":
    args = sys.argv[1:]

    # Crear dirs necesarios
    os.makedirs("data", exist_ok=True)

    if "--demo" in args:
        os.environ["DEMO_MODE"] = "true"
        logger.info("Modo DEMO activado")

    if "--test" in args:
        asyncio.run(run_tests())

    elif "--api-only" in args:
        run_api()

    elif "--once" in args:
        run_pipeline(once=True)

    else:
        # Modo completo: pipeline + API en paralelo
        import threading
        api_thread = threading.Thread(target=run_api, daemon=True)
        api_thread.start()
        logger.info("API iniciada en background")
        run_pipeline(once=False)
