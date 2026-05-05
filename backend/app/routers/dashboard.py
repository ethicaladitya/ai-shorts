"""Dashboard router."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Video, RenderJob

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    videos = db.query(Video).order_by(desc(Video.created_at)).limit(20).all()
    recent_jobs = db.query(RenderJob).order_by(desc(RenderJob.created_at)).limit(10).all()

    stats = {
        "total_videos": db.query(Video).count(),
        "completed": db.query(Video).filter(Video.status == "complete").count(),
        "in_progress": db.query(Video).filter(Video.status.notin_(["complete", "failed", "draft"])).count(),
        "failed": db.query(Video).filter(Video.status == "failed").count(),
    }

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "videos": videos,
        "jobs": recent_jobs,
        "stats": stats,
    })
