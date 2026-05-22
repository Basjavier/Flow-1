import asyncio
from datetime import datetime
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from storage.database import Database
from scrapers.cmf_scraper import CMFScraper
from scrapers.bcch_scraper import BCChScraper
from scrapers.portal_inmobiliario import PortalInmobiliarioScraper
from processors.nlp_processor import NLPProcessor
from config.settings import (
    SCRAPE_START_HOUR, SCRAPE_END_HOUR, DEMO_MODE,
    ALERT_WEBHOOK_URL
)


class AltDataPipeline:

    COMUNAS_RE = [
        "Las Condes", "Providencia", "Vitacura",
        "Nunoa", "Santiago Centro"
    ]

    def __init__(self):
        self.db       = Database()
        self.cmf      = CMFScraper()
        self.bcch     = BCChScraper()
        self.portal   = PortalInmobiliarioScraper()
        self.nlp      = NLPProcessor()
        self.scheduler = AsyncIOScheduler(timezone="America/Santiago")
        logger.info("Pipeline inicializado")

    # --- CMF ---
    async def run_cmf_pipeline(self):
        started = datetime.now()
        logger.info("CMF pipeline iniciando...")
        count = 0
        try:
            hechos = await self.cmf.fetch_hechos_esenciales(days_back=1)
            for h in hechos:
                self.db.insert_hecho(h)
                count += 1

            unprocessed = self.db.get_unprocessed_hechos(limit=25)
            high_urgency = []
            for hecho in unprocessed:
                signal = await self.nlp.extract_signal_cmf(hecho)
                if signal:
                    self.db.insert_signal(signal)
                    self.db.mark_hecho_processed(hecho["id"])
                    if signal.get("urgency") == "HIGH":
                        high_urgency.append(signal)
                await asyncio.sleep(0.3)

            if high_urgency:
                await self._send_alerts(high_urgency)

            self.db.log_run("CMF", started, "OK", count)
            logger.success(f"CMF done - {count} hechos, {len(high_urgency)} HIGH urgency")
        except Exception as e:
            self.db.log_run("CMF", started, "ERROR", 0, str(e))
            logger.error(f"CMF pipeline error: {e}")

    # --- BCCH ---
    async def run_bcch_pipeline(self):
        started = datetime.now()
        logger.info("BCCh pipeline iniciando...")
        count = 0
        try:
            snapshot = await self.bcch.get_macro_snapshot()
            for serie, data in snapshot.items():
                if data and data.get("valor") is not None:
                    self.db.upsert_macro(data["fecha"], serie, data["valor"])
                    count += 1
            self.db.log_run("BCCH", started, "OK", count)
            logger.success(f"BCCh done - {count} series actualizadas")
        except Exception as e:
            self.db.log_run("BCCH", started, "ERROR", 0, str(e))
            logger.error(f"BCCh pipeline error: {e}")

    # --- REAL ESTATE ---
    async def run_real_estate_pipeline(self):
        started = datetime.now()
        logger.info("Real Estate pipeline iniciando...")
        count = 0
        try:
            for comuna in self.COMUNAS_RE:
                for tipo in ["departamento", "casa"]:
                    listings = await self.portal.scrape_top20(
                        comuna=comuna, tipo=tipo, demo=DEMO_MODE
                    )
                    stats = self.db.get_re_stats(comuna)
                    for prop in listings:
                        scored = await self.nlp.score_property(prop, stats)
                        prop.update(scored)
                        self.db.insert_property(prop, comuna, tipo)
                        count += 1
                    await asyncio.sleep(2)
            self.db.log_run("RE", started, "OK", count)
            logger.success(f"Real Estate done - {count} propiedades")
        except Exception as e:
            self.db.log_run("RE", started, "ERROR", 0, str(e))
            logger.error(f"RE pipeline error: {e}")

    # --- ALERTS ---
    async def _send_alerts(self, signals: list[dict]):
        for sig in signals:
            msg = (
                f"\nHIGH URGENCY SIGNAL\n"
                f"{'-'*40}\n"
                f"Empresa:    {sig.get('affected_ticker', 'N/A')}\n"
                f"Senal:      {sig.get('signal')}\n"
                f"Accion:     {sig.get('action')}\n"
                f"Razon:      {sig.get('reasoning')}\n"
                f"Confianza:  {sig.get('confidence', 0)*100:.0f}%\n"
                f"{'-'*40}"
            )
            logger.warning(msg)
            if ALERT_WEBHOOK_URL:
                try:
                    import httpx
                    async with httpx.AsyncClient() as client:
                        await client.post(ALERT_WEBHOOK_URL, json={"text": msg}, timeout=5)
                except Exception as e:
                    logger.error(f"Webhook error: {e}")

    # --- SCHEDULER ---
    def _setup_schedule(self):
        self.scheduler.add_job(
            self.run_cmf_pipeline,
            CronTrigger(day_of_week="mon-fri",
                        hour=f"{SCRAPE_START_HOUR}-{SCRAPE_END_HOUR}",
                        minute=5),
            id="cmf_hourly", replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_bcch_pipeline,
            CronTrigger(day_of_week="mon-fri", hour=18, minute=30),
            id="bcch_daily", replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_real_estate_pipeline,
            CronTrigger(day_of_week="mon-fri", hour="9,15", minute=30),
            id="re_twice_daily", replace_existing=True,
        )
        logger.info("Scheduler configurado")

    async def run_all_now(self):
        logger.info("Running all pipelines now...")
        await self.run_bcch_pipeline()
        await self.run_cmf_pipeline()
        await self.run_real_estate_pipeline()

    async def _serve_forever(self, run_now: bool):
        # AsyncIOScheduler.start() requiere event loop ya corriendo,
        # por eso lo arrancamos dentro de la corutina.
        self._setup_schedule()
        self.scheduler.start()
        logger.info("Scheduler activo")
        if run_now:
            await self.run_all_now()
        stop = asyncio.Event()
        try:
            await stop.wait()
        except asyncio.CancelledError:
            pass

    def start(self, run_now: bool = True):
        # asyncio.run() reemplaza al deprecated asyncio.get_event_loop()
        # (que en 3.12+ falla si no hay un loop corriendo).
        try:
            asyncio.run(self._serve_forever(run_now))
        except KeyboardInterrupt:
            logger.info("Pipeline detenido por usuario")
            self.scheduler.shutdown(wait=False)
