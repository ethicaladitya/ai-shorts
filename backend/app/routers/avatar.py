"""Avatar (talking head) video router."""
import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, BackgroundTasks, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.config import settings
from app.database import get_db, SessionLocal
from app.models import AvatarJob, AvatarJobStatus
from app.services.avatar_pipeline import run_avatar_pipeline

router = APIRouter(prefix="/avatar")
templates = Jinja2Templates(directory="app/templates")

_ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.get("")
async def avatar_page(request: Request, db: Session = Depends(get_db)):
    recent = (
        db.query(AvatarJob)
        .order_by(desc(AvatarJob.created_at))
        .limit(5)
        .all()
    )
    return templates.TemplateResponse(
        "avatar.html",
        {
            "request": request,
            "recent": recent,
            "did_configured": bool(settings.did_api_key),
        },
    )


@router.post("/create")
async def create_avatar(
    request: Request,
    background_tasks: BackgroundTasks,
    topic: str = Form(...),
    custom_notes: str = Form(""),
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    # Validate content type
    if photo.content_type not in _ALLOWED_MIME:
        return templates.TemplateResponse(
            "avatar.html",
            {
                "request": request,
                "error": "Please upload a JPEG, PNG, or WebP image.",
                "did_configured": bool(settings.did_api_key),
                "topic": topic,
                "custom_notes": custom_notes,
            },
            status_code=422,
        )

    content = await photo.read()

    # Validate file size
    if len(content) > _MAX_BYTES:
        return templates.TemplateResponse(
            "avatar.html",
            {
                "request": request,
                "error": "Image must be smaller than 10 MB.",
                "did_configured": bool(settings.did_api_key),
                "topic": topic,
                "custom_notes": custom_notes,
            },
            status_code=422,
        )

    # Save face image
    faces_dir = settings.data_dir / "faces"
    faces_dir.mkdir(exist_ok=True)
    ext = Path(photo.filename or "photo.jpg").suffix.lower() or ".jpg"
    uid = str(uuid.uuid4())[:8]
    face_path = faces_dir / f"face_{uid}{ext}"
    face_path.write_bytes(content)

    # Create job record
    job = AvatarJob(
        topic=topic,
        custom_notes=custom_notes,
        face_image_path=str(face_path),
        status=AvatarJobStatus.PENDING,
        log="",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Run pipeline in background thread (same pattern as video pipeline)
    def _run(jid: int):
        _db = SessionLocal()
        try:
            asyncio.run(run_avatar_pipeline(jid, _db))
        finally:
            _db.close()

    background_tasks.add_task(_run, job.id)

    return RedirectResponse(url=f"/avatar/{job.id}", status_code=303)


@router.get("/{job_id}")
async def avatar_result(request: Request, job_id: int, db: Session = Depends(get_db)):
    job = db.query(AvatarJob).filter(AvatarJob.id == job_id).first()
    if not job:
        return HTMLResponse("Avatar job not found", status_code=404)
    return templates.TemplateResponse("avatar_result.html", {"request": request, "job": job})


@router.get("/{job_id}/status")
async def avatar_status_partial(
    request: Request, job_id: int, db: Session = Depends(get_db)
):
    """HTMX polling endpoint — returns the status partial fragment."""
    job = db.query(AvatarJob).filter(AvatarJob.id == job_id).first()
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    return templates.TemplateResponse(
        "partials/avatar_status.html", {"request": request, "job": job}
    )
