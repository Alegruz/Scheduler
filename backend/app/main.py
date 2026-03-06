"""
Life Scheduler — FastAPI application entry point.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1.router import api_router
from .core.config import get_settings
from .jobs.workers import create_scheduler

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background scheduler on startup; shut it down on shutdown."""
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Background scheduler started")
    yield
    scheduler.shutdown(wait=False)
    logger.info("Background scheduler stopped")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Policy-driven, deterministic, adaptive life scheduler API. "
        "Internal DB is canonical source of truth. GCal is an optional projection layer."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "version": settings.APP_VERSION}
