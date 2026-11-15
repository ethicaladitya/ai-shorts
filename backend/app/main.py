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
from app.routers.ugc import router as ugc_router
from app.routers.costs import router as costs_router
from app.routers.personas import router as personas_router
from app.routers.dms import router as dms_router
from app.routers.billing import router as billing_router
from app.routers.faceless import router as faceless_router
from app.models import KnowledgeEntry
import importlib.util
from app.routers.auth import router as auth_router
from app.routers.admin import router as admin_router
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

# Middleware (LIFO order — SessionMiddleware added last, runs first)
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=60 * 60 * 24 * 30)

# Static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

output_dir = settings.output_dir
output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(output_dir)), name="output")

faces_dir = settings.data_dir / "faces"
faces_dir.mkdir(parents=True, exist_ok=True)
app.mount("/faces", StaticFiles(directory=str(faces_dir)), name="faces")

temp_preview_dir = settings.temp_dir
temp_preview_dir.mkdir(parents=True, exist_ok=True)
app.mount("/temp", StaticFiles(directory=str(temp_preview_dir)), name="temp")

# Serve persona avatars
personas_media_dir = settings.data_dir / "personas"
personas_media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/persona_avatars", StaticFiles(directory=str(personas_media_dir)), name="persona_avatars")

# Routers
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(billing_router)
app.include_router(faceless_router)
app.include_router(dashboard.router)
app.include_router(videos.router)
app.include_router(scripts.router)
app.include_router(knowledge.router)
app.include_router(render.router)
app.include_router(settings_router.router)
app.include_router(avatar_router)
app.include_router(ugc_router)
app.include_router(costs_router)
app.include_router(personas_router)
app.include_router(dms_router)

try:
    app.mount("/persona_media", StaticFiles(directory="data/persona_media"), name="persona_media")
except Exception:
    pass

# Persona System (optional sub-app)
try:
    from persona_system.dashboard.api import app as persona_app
    app.mount("/persona", persona_app)
    logger.info("Persona System mounted at /persona/")
except Exception as _pe:
    logger.warning("Persona System not available: %s", _pe)


def seed_knowledge_from_script(db):
    import os, sys
    seed_path = os.path.join(os.path.dirname(__file__), "..", "seed_knowledge.py")
    spec = importlib.util.spec_from_file_location("seed_knowledge", seed_path)
    seed_mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_knowledge"] = seed_mod
    spec.loader.exec_module(seed_mod)
    SEEDS = getattr(seed_mod, "SEEDS", [])
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

    # SQLite column migrations
    import sqlalchemy as sa
    from app.database import engine as _engine
    migrations = [
        ("avatar_jobs",    "voice_provider",  "VARCHAR(100) DEFAULT ''"),
        ("avatar_jobs",    "persona_id",       "INTEGER"),
        ("ugc_jobs",       "persona_id",       "INTEGER"),
        ("users",          "credits",          "INTEGER DEFAULT 0"),
        ("cost_log",       "user_email",       "VARCHAR(300)"),
        ("persona_accounts", "personality",    "TEXT"),
        ("persona_accounts", "avatar_style",   "VARCHAR(50) DEFAULT 'photo'"),
        ("persona_accounts", "tiktok_token_env","VARCHAR(200)"),
        # UGC extended brief fields
        ("ugc_jobs", "product_name",        "VARCHAR(300)"),
        ("ugc_jobs", "product_description", "TEXT"),
        ("ugc_jobs", "product_url",         "VARCHAR(500)"),
        ("ugc_jobs", "creator_name",        "VARCHAR(200)"),
        ("ugc_jobs", "creator_instagram",   "VARCHAR(100)"),
        ("ugc_jobs", "creator_tiktok",      "VARCHAR(100)"),
        ("ugc_jobs", "creator_location",    "VARCHAR(200)"),
        ("ugc_jobs", "creator_languages",   "VARCHAR(200)"),
        ("ugc_jobs", "creator_age_range",   "VARCHAR(20)"),
        ("ugc_jobs", "video_purpose",       "VARCHAR(50)"),
        ("ugc_jobs", "main_goal",           "VARCHAR(50)"),
        ("ugc_jobs", "target_audience",     "VARCHAR(300)"),
        ("ugc_jobs", "cta_text",            "VARCHAR(200)"),
        ("ugc_jobs", "hook_style",          "VARCHAR(50)"),
        ("ugc_jobs", "tone",                "VARCHAR(50)"),
        ("ugc_jobs", "script_language",     "VARCHAR(20) DEFAULT 'en'"),
        ("ugc_jobs", "script_notes",        "TEXT"),
        ("ugc_jobs", "brand_guidelines",    "TEXT"),
        ("ugc_jobs", "example_videos",      "TEXT"),
        ("ugc_jobs", "key_phrases",         "TEXT"),
        ("ugc_jobs", "structure_flags",     "TEXT"),
        ("ugc_jobs", "style_flags",         "TEXT"),
    ]
    with _engine.connect() as _conn:
        for _tbl, _col, _def in migrations:
            try:
                _conn.execute(sa.text(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_def}"))
                _conn.commit()
                logger.info("Migration: added %s.%s", _tbl, _col)
            except Exception:
                pass  # column already exists

    # Ensure super admin exists
    from app.models import User, UserPermissions
    db = next(get_db())
    sa_email = settings.super_admin_email.lower()
    sa_user = db.query(User).filter(User.email == sa_email).first()
    if not sa_user:
        sa_user = User(email=sa_email, name="Super Admin", is_active=True, credits=9999)
        db.add(sa_user)
        db.flush()
        db.add(UserPermissions(
            user_email=sa_email,
            can_create_video=True, can_knowledge=True, can_avatar_video=True,
            can_ugc_video=True, can_personas=True, can_dms=True,
            can_costs=True, can_queue=True, can_settings=True,
        ))
        db.commit()
        logger.info("Super admin created: %s", sa_email)
    elif sa_user.credits is None:
        sa_user.credits = 9999
        db.commit()

    # Recover stale jobs
    from app.models import RenderJob, RenderJobStatus
    db2 = next(get_db())
    stale = db2.query(RenderJob).filter(RenderJob.status == RenderJobStatus.PROCESSING).all()
    for job in stale:
        job.status = RenderJobStatus.FAILED
        job.error_message = "Orphaned at startup"
    from app.models import UGCJob, UGCJobStatus
    stale_ugc = db2.query(UGCJob).filter(
        UGCJob.status.notin_([UGCJobStatus.COMPLETE, UGCJobStatus.FAILED, UGCJobStatus.PENDING])
    ).all()
    for job in stale_ugc:
        job.status = UGCJobStatus.FAILED
        job.error_message = "Orphaned — server restarted"
    if stale or stale_ugc:
        db2.commit()
    db2.close()

    seed_knowledge_from_script(db)
    db.close()

    logger.info("AI Provider: %s", settings.ai_provider)
    logger.info("Voice Provider: %s", settings.voice_provider)

    async def _warmup_whisper():
        try:
            from app.services.subtitle_generator import _get_model
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: _get_model("base"))
            logger.info("Whisper 'base' model ready.")
        except Exception as exc:
            logger.warning("Whisper warmup failed: %s", exc)

    asyncio.create_task(_warmup_whisper())
    logger.info("AI Shorts System ready.")

    try:
        from persona_system.shared.database import init_db as persona_init_db
        persona_init_db()
        logger.info("Persona System DB initialised.")
    except Exception as e:
        logger.warning("Persona System DB init failed: %s", e)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ai_provider": settings.ai_provider,
        "voice_provider": settings.voice_provider,
    }
