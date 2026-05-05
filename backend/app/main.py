"""AI Shorts System - Main Application."""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import init_db, get_db
from app.routers import dashboard, videos, scripts, knowledge, render, settings as settings_router
from app.routers.avatar import router as avatar_router
from app.models import KnowledgeEntry
import importlib.util
from app.routers.auth import router as auth_router
from app.middleware.auth import AuthMiddleware

# Logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.data_dir / "app.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, docs_url="/api/docs", redoc_url=None)

# In Starlette, middlewares execute in REVERSE registration order (LIFO).
# SessionMiddleware must be added LAST so it runs FIRST (before AuthMiddleware reads request.session).
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=60 * 60 * 24 * 30)

# Static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Serve output files
output_dir = settings.output_dir
output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(output_dir)), name="output")

# Serve uploaded face images
faces_dir = settings.data_dir / "faces"
faces_dir.mkdir(parents=True, exist_ok=True)
app.mount("/faces", StaticFiles(directory=str(faces_dir)), name="faces")

# Routers
app.include_router(auth_router)
app.include_router(dashboard.router)
app.include_router(videos.router)
app.include_router(scripts.router)
app.include_router(knowledge.router)
app.include_router(render.router)
app.include_router(settings_router.router)
app.include_router(avatar_router)



def seed_knowledge_from_script(db):
    # Dynamically import SEEDS from seed_knowledge.py
    import os
    import sys
    seed_path = os.path.join(os.path.dirname(__file__), "..", "seed_knowledge.py")
    spec = importlib.util.spec_from_file_location("seed_knowledge", seed_path)
    seed_mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_knowledge"] = seed_mod
    spec.loader.exec_module(seed_mod)
    SEEDS = getattr(seed_mod, "SEEDS", [])

    # Remove all existing entries and re-seed
    db.query(KnowledgeEntry).delete()
    for entry in SEEDS:
        db.add(KnowledgeEntry(**entry))
    db.commit()

@app.on_event("startup")
async def startup():
    import asyncio
    from app.models import RenderJob, RenderJobStatus

    logger.info("Initializing database...")
    init_db()

    # Recover stale PROCESSING jobs — any job still PROCESSING at startup was
    # orphaned by a previous restart and will never finish on its own.
    db = next(get_db())
    stale = db.query(RenderJob).filter(RenderJob.status == RenderJobStatus.PROCESSING).all()
    if stale:
        for job in stale:
            logger.warning(f"Recovering stale job {job.id} (was PROCESSING at startup)")
            job.status = RenderJobStatus.FAILED
            job.step = "failed"
            job.error_message = "Orphaned — worker killed mid-run, pipeline not completed"
        db.commit()
        logger.info(f"Marked {len(stale)} stale job(s) as FAILED")

    # Seed knowledge from script on every startup
    seed_knowledge_from_script(db)
    db.close()

    logger.info(f"AI Provider: {settings.ai_provider}")
    logger.info(f"Voice Provider: {settings.voice_provider}")
    logger.info(f"Azure Endpoint: {settings.azure_openai_endpoint[:30]}..." if settings.azure_openai_endpoint else "Azure: Not configured")

    # Pre-warm the Whisper model so first pipeline run doesn't hang downloading it.
    async def _warmup_whisper():
        try:
            from app.services.subtitle_generator import _get_model
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: _get_model("base"))
            logger.info("Whisper 'base' model ready.")
        except Exception as exc:
            logger.warning(f"Whisper warmup failed (subtitles may be slow on first run): {exc}")

    asyncio.create_task(_warmup_whisper())
    logger.info("AI Shorts System ready.")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ai_provider": settings.ai_provider,
        "voice_provider": settings.voice_provider,
        "azure_configured": bool(settings.azure_openai_endpoint and settings.azure_openai_api_key),
    }
