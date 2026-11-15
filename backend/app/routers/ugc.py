"""UGC Video Engine — HTTP routes."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import UGCJob, UGCJobStatus
from app.services.ugc.config.defaults import (
    PLATFORM_PRESETS,
    estimate_cost,
    resolve_providers,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ugc", tags=["ugc"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


# ── List jobs ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def ugc_list(request: Request, db: Session = Depends(get_db)):
    jobs = db.query(UGCJob).order_by(UGCJob.created_at.desc()).limit(50).all()
    providers = resolve_providers(settings)
    cost = estimate_cost(providers)
    return templates.TemplateResponse(
        "ugc/list.html",
        {"request": request, "jobs": jobs, "providers": providers, "estimated_cost": cost},
    )


# ── Create job form ───────────────────────────────────────────────────────────

@router.get("/create", response_class=HTMLResponse)
async def ugc_create_form(request: Request, db: Session = Depends(get_db)):
    from pathlib import Path as _Path
    from app.models import PersonaAccount
    providers = resolve_providers(settings)
    cost = estimate_cost(providers)

    # DB personas with image URL pre-computed
    raw_personas = db.query(PersonaAccount).filter_by(is_active=True).all()
    db_personas = []
    for p in raw_personas:
        image_url = None
        if p.reference_image_path:
            filename = _Path(p.reference_image_path).name
            image_url = f"/persona_avatars/{p.slug}/{filename}"
        db_personas.append({
            "id": p.id,
            "slug": p.slug,
            "display_name": p.display_name,
            "voice_provider": p.voice_provider or "",
            "instagram_username": p.instagram_username or "",
            "tiktok_username": p.tiktok_username or "",
            "image_url": image_url,
        })

    # Discover available YAML personas (legacy)
    personas_dir = _Path(settings.data_dir).parent / "persona_system" / "config" / "personas"
    persona_slugs = sorted(
        p.stem for p in personas_dir.glob("*.yaml") if not p.stem.startswith("_")
    ) if personas_dir.exists() else ["default"]

    return templates.TemplateResponse(
        "ugc/create.html",
        {
            "request": request,
            "platforms": list(PLATFORM_PRESETS.keys()),
            "durations": [15, 30, 60],
            "styles": ["ugc", "selfie", "casual-vlog"],
            "providers": providers,
            "estimated_cost": cost,
            "ugc_mode": settings.ugc_mode,
            "personas": persona_slugs,
            "db_personas": db_personas,
        },
    )


# ── Submit new job ────────────────────────────────────────────────────────────

@router.post("/jobs")
async def ugc_create_job(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    # Core
    persona_name: str = Form("default"),
    persona_id: Optional[str] = Form(""),
    topic: str = Form(""),
    style: str = Form("ugc"),
    platform: str = Form("tiktok"),
    duration_target: int = Form(30),
    # Providers
    script_provider: str = Form(""),
    image_provider: str = Form(""),
    voice_provider: str = Form(""),
    head_provider: str = Form(""),
    # Product
    product_name: str = Form(""),
    product_description: str = Form(""),
    product_url: str = Form(""),
    # Creator
    creator_name: str = Form(""),
    creator_instagram: str = Form(""),
    creator_tiktok: str = Form(""),
    creator_location: str = Form(""),
    creator_languages: str = Form(""),
    creator_age_range: str = Form(""),
    # Campaign
    video_purpose: str = Form("organic"),
    main_goal: str = Form("sales"),
    target_audience: str = Form(""),
    cta_text: str = Form(""),
    # Script brief
    hook_style: str = Form("bold_claim"),
    tone: str = Form("energetic"),
    script_language: str = Form("en"),
    script_notes: str = Form(""),
    brand_guidelines: str = Form(""),
    example_videos: str = Form(""),
    key_phrases: str = Form(""),
    # Structure checkboxes
    structure_hook: Optional[str] = Form(None),
    structure_problem: Optional[str] = Form(None),
    structure_product_demo: Optional[str] = Form(None),
    structure_personal_story: Optional[str] = Form(None),
    structure_benefits: Optional[str] = Form(None),
    structure_cta: Optional[str] = Form(None),
    # Style checkboxes
    flag_tiktok_style: Optional[str] = Form(None),
    flag_trending_audio: Optional[str] = Form(None),
    flag_broll_needed: Optional[str] = Form(None),
    flag_captions_burned: Optional[str] = Form(None),
):
    # Start from env-configured defaults, then override with any per-job selections
    providers = resolve_providers(settings)
    if script_provider:
        providers["script"] = script_provider
    if image_provider:
        providers["image"] = image_provider
    if voice_provider:
        providers["voice"] = voice_provider
    if head_provider:
        providers["head"] = head_provider
    cost = estimate_cost(providers)

    # Build structure/style flag JSON
    structure_flags = json.dumps({
        "hook": bool(structure_hook),
        "problem": bool(structure_problem),
        "product_demo": bool(structure_product_demo),
        "personal_story": bool(structure_personal_story),
        "benefits": bool(structure_benefits),
        "cta": bool(structure_cta),
    })
    style_flags = json.dumps({
        "tiktok_style": bool(flag_tiktok_style),
        "trending_audio": bool(flag_trending_audio),
        "broll_needed": bool(flag_broll_needed),
        "captions_burned": bool(flag_captions_burned),
    })

    # Resolve persona_id
    pid = int(persona_id) if persona_id and persona_id.strip().isdigit() else None

    job = UGCJob(
        persona_name=persona_name,
        persona_id=pid,
        topic=topic or "personal story",
        style=style,
        platform=platform,
        duration_target=duration_target,
        script_provider=providers["script"],
        image_provider=providers["image"],
        voice_provider=providers["voice"],
        head_provider=providers["head"],
        status=UGCJobStatus.PENDING,
        estimated_cost=cost,
        # Extended brief
        product_name=product_name,
        product_description=product_description,
        product_url=product_url,
        creator_name=creator_name,
        creator_instagram=creator_instagram,
        creator_tiktok=creator_tiktok,
        creator_location=creator_location,
        creator_languages=creator_languages,
        creator_age_range=creator_age_range,
        video_purpose=video_purpose,
        main_goal=main_goal,
        target_audience=target_audience,
        cta_text=cta_text,
        hook_style=hook_style,
        tone=tone,
        script_language=script_language,
        script_notes=script_notes,
        brand_guidelines=brand_guidelines,
        example_videos=example_videos,
        key_phrases=key_phrases,
        structure_flags=structure_flags,
        style_flags=style_flags,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    def _run(job_id: int):
        asyncio.run(_run_pipeline(job_id))

    background_tasks.add_task(_run, job.id)
    logger.info("UGC job %d queued (persona=%s, topic=%s)", job.id, persona_name, topic)

    return RedirectResponse(url=f"/ugc/jobs/{job.id}", status_code=303)


async def _run_pipeline(job_id: int):
    from app.services.ugc.pipeline import run_ugc_pipeline
    await run_ugc_pipeline(job_id)


# ── Job detail / progress ─────────────────────────────────────────────────────

@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def ugc_job_detail(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.query(UGCJob).filter(UGCJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="UGC job not found")

    return templates.TemplateResponse(
        "ugc/detail.html",
        {
            "request": request,
            "job": job,
        },
    )


# ── HTMX poll for live progress ───────────────────────────────────────────────

@router.get("/jobs/{job_id}/poll", response_class=HTMLResponse)
async def ugc_job_poll(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.query(UGCJob).filter(UGCJob.id == job_id).first()
    if not job:
        return HTMLResponse("<p>Job not found</p>")
    return templates.TemplateResponse(
        "ugc/partials/progress.html",
        {"request": request, "job": job},
    )


# ── Approval endpoint (for webhook / web UI mode) ────────────────────────────

@router.post("/jobs/{job_id}/approve")
async def ugc_approve(
    job_id: int,
    db: Session = Depends(get_db),
    decision: str = Form("approve"),
):
    """Set approval decision — unblocks pipeline when UGC_APPROVAL_MODE=webhook."""
    job = db.query(UGCJob).filter(UGCJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="UGC job not found")
    if job.status != UGCJobStatus.AWAITING_APPROVAL:
        raise HTTPException(status_code=400, detail="Job is not awaiting approval")
    if decision not in ("approve", "regenerate_script", "regenerate_image"):
        raise HTTPException(status_code=400, detail="Invalid decision value")

    job.approval_decision = decision
    db.commit()
    logger.info("UGC job %d approval set: %s", job_id, decision)
    return RedirectResponse(url=f"/ugc/jobs/{job_id}", status_code=303)


# ── Re-generate (resets job to earlier stage) ────────────────────────────────

@router.post("/jobs/{job_id}/regenerate")
async def ugc_regenerate(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    stage: str = Form("script"),
):
    """Re-run the pipeline from the given stage for a completed or failed job."""
    job = db.query(UGCJob).filter(UGCJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="UGC job not found")

    job.status = UGCJobStatus.PENDING
    job.error_message = None

    if stage == "script":
        job.script_raw = None
        job.script_formatted = None
        job.scene_images = None
        job.voice_file = None
        job.talking_head_file = None
        job.subtitle_file = None
        job.output_file = None
        job.thumbnail_file = None
        job.approval_decision = None

        # Clean up temp directory on disk
        from app.services.ugc.utils.temp_files import TempJobDir
        try:
            TempJobDir(job_id).cleanup()
        except Exception as e:
            logger.warning("Failed to clean up temp dir on reset: %s", e)

    elif stage == "resume":
        # Keep everything on job, reset only the status so it retries
        pass

    db.commit()

    def _run(jid: int):
        asyncio.run(_run_pipeline(jid))

    background_tasks.add_task(_run, job_id)
    return RedirectResponse(url=f"/ugc/jobs/{job_id}", status_code=303)
