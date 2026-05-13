"""
FastAPI application entry point.

Run with:
  uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from api.routes import alerts, properties, reports
from api.schemas import ScrapeRequest, ScrapeResponse
from config import settings

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown hooks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app_startup", version="0.1.0")
    yield
    log.info("app_shutdown")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


app = FastAPI(
    title="Real Estate Intelligence Agent",
    description="Automated scraping, scoring, and alerting for the Chilean property market.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(properties.router)
app.include_router(alerts.router)
app.include_router(reports.router)


# ---------------------------------------------------------------------------
# Utility routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/scrape", response_model=ScrapeResponse, tags=["scraper"])
async def trigger_scrape(
    request: ScrapeRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger a manual scrape job in the background.
    The job runs asynchronously; use /reports/summary to see updated counts.
    """
    job_id = str(uuid.uuid4())[:8]
    background_tasks.add_task(_run_scrape_job, job_id, request)
    log.info("scrape_triggered", job_id=job_id, sources=request.sources)
    return ScrapeResponse(
        job_id=job_id,
        status="queued",
        message=f"Scrape job {job_id} queued for sources: {request.sources}",
    )


async def _run_scrape_job(job_id: str, req: ScrapeRequest) -> None:
    """Background scrape + score + alert generation pipeline."""
    from scheduler.jobs import run_full_pipeline

    log.info("scrape_job_started", job_id=job_id)
    try:
        await run_full_pipeline(
            sources=req.sources,
            communes=req.communes,
            tipos=req.tipos,
            max_pages=req.max_pages,
        )
        log.info("scrape_job_done", job_id=job_id)
    except Exception as exc:
        log.error("scrape_job_failed", job_id=job_id, error=str(exc))
