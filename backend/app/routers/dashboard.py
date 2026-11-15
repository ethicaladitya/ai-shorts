"""Dashboard router."""
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Video, AvatarJob, UGCJob, PersonaAccount, User

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    email = request.session.get("user_email", "")

    # Recent activity across all job types
    recent_avatar = db.query(AvatarJob).order_by(desc(AvatarJob.created_at)).limit(5).all()
    recent_ugc = db.query(UGCJob).order_by(desc(UGCJob.created_at)).limit(5).all()
    recent_videos = db.query(Video).order_by(desc(Video.created_at)).limit(5).all()

    # Stats
    stats = {
        "total_videos": (db.query(Video).count() +
                         db.query(AvatarJob).count() +
                         db.query(UGCJob).count()),
        "completed": (db.query(Video).filter(Video.status == "complete").count() +
                      db.query(AvatarJob).filter(AvatarJob.status == "complete").count() +
                      db.query(UGCJob).filter(UGCJob.status == "complete").count()),
        "personas": db.query(PersonaAccount).filter_by(is_active=True).count(),
    }

    # User credits
    user = db.query(User).filter(User.email == email).first() if email else None
    credits = user.credits if user else 0

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "recent_avatar": recent_avatar,
        "recent_ugc": recent_ugc,
        "recent_videos": recent_videos,
        "stats": stats,
        "credits": credits,
    })
