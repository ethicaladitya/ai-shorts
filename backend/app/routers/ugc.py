"""UGC Video Engine — HTTP routes."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
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
async def ugc_create_form(request: Request):
    providers = resolve_providers(settings)
    cost = estimate_cost(providers)
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
        },
    )


# ── Submit new job ────────────────────────────────────────────────────────────

@router.post("/jobs")
async def ugc_create_job(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    persona_name: str = Form("default"),
    topic: str = Form(""),
    style: str = Form("ugc"),
    platform: str = Form("tiktok"),
    duration_target: int = Form(30),
):
    providers = resolve_providers(settings)
    cost = estimate_cost(providers)

    job = UGCJob(
        persona_name=persona_name,
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

    output_url: str | None = None
    if job.output_file and Path(job.output_file).exists():
        rel = Path(job.output_file).relative_to(settings.ugc_output_dir.parent)
        output_url = f"/output/{rel}"

    thumbnail_url: str | None = None
    if job.thumbnail_file and Path(job.thumbnail_file).exists():
        rel = Path(job.thumbnail_file).relative_to(settings.ugc_output_dir.parent)
        thumbnail_url = f"/output/{rel}"

    return templates.TemplateResponse(
        "ugc/detail.html",
        {
            "request": request,
            "job": job,
            "output_url": output_url,
            "thumbnail_url": thumbnail_url,
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
    job.approval_decision = None
    if stage == "script":
        job.script_raw = None
        job.script_formatted = None
    db.commit()

    def _run(jid: int):
        asyncio.run(_run_pipeline(jid))

    background_tasks.add_task(_run, job_id)
    return RedirectResponse(url=f"/ugc/jobs/{job_id}", status_code=303)
