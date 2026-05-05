"""Render queue router."""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import RenderJob, Video

router = APIRouter(prefix="/queue")
templates = Jinja2Templates(directory="app/templates")


@router.get("")
async def queue_page(request: Request, db: Session = Depends(get_db)):
    jobs = db.query(RenderJob).order_by(desc(RenderJob.created_at)).limit(50).all()
    # Attach video info
    for job in jobs:
        job.video = db.query(Video).filter(Video.id == job.video_id).first()
    return templates.TemplateResponse("queue.html", {"request": request, "jobs": jobs})


@router.get("/poll")
async def poll_queue(request: Request, db: Session = Depends(get_db)):
    """HTMX polling endpoint for queue updates."""
    jobs = db.query(RenderJob).order_by(desc(RenderJob.created_at)).limit(50).all()
    for job in jobs:
        job.video = db.query(Video).filter(Video.id == job.video_id).first()
    return templates.TemplateResponse("partials/queue_list.html", {"request": request, "jobs": jobs})


@router.get("/{job_id}/log")
async def job_log(job_id: int, db: Session = Depends(get_db)):
    job = db.query(RenderJob).filter(RenderJob.id == job_id).first()
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    return HTMLResponse(
        f'<pre class="text-xs font-mono bg-gray-900 text-green-400 p-4 rounded-lg overflow-auto max-h-96">'
        f'{job.log or "No logs yet..."}</pre>'
    )
