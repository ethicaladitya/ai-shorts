"""Video creation and management router."""
import asyncio
from fastapi import APIRouter, Request, Depends, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Video, VideoStatus
from app.services.content_fetcher import fetch_wordpress_content
from app.services.script_generator import generate_hooks
from app.services.pipeline import run_pipeline
from app.database import SessionLocal

router = APIRouter(prefix="/videos")
templates = Jinja2Templates(directory="app/templates")


@router.get("/create")
async def create_video_page(request: Request):
    return templates.TemplateResponse("create.html", {"request": request})


@router.post("/create")
async def create_video(
    request: Request,
    background_tasks: BackgroundTasks,
    topic: str = Form(default=""),
    wordpress_url: str = Form(""),
    custom_notes: str = Form(""),
    db: Session = Depends(get_db),
):
    topic = topic.strip()
    if not topic:
        return templates.TemplateResponse(
            "create.html",
            {"request": request, "error": "Topic is required."},
            status_code=422,
        )
    # Fetch WP content if provided
    source_content = ""
    title = topic
    if wordpress_url:
        try:
            data = await fetch_wordpress_content(wordpress_url)
            source_content = data.get("content", "")
            if data.get("title"):
                title = data["title"]
        except Exception:
            pass

    video = Video(
        title=title,
        topic=topic,
        wordpress_url=wordpress_url,
        custom_notes=custom_notes,
        source_content=source_content,
        status=VideoStatus.DRAFT,
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    return RedirectResponse(url=f"/videos/{video.id}/hooks", status_code=303)


@router.get("/{video_id}/hooks")
async def hooks_page(request: Request, video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return HTMLResponse("Video not found", status_code=404)
    return templates.TemplateResponse("hooks.html", {"request": request, "video": video})


@router.post("/{video_id}/generate-hooks")
async def generate_hooks_endpoint(request: Request, video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return HTMLResponse("Video not found", status_code=404)

    try:
        hooks = await generate_hooks(
            video.topic,
            video.source_content or video.custom_notes or "",
            video.custom_notes or "",
            db,
        )
        video.hooks = hooks
        video.status = VideoStatus.HOOKS_GENERATED
        db.commit()
    except Exception as e:
        return HTMLResponse(
            f'<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">'
            f'<strong>Error:</strong> {str(e)}</div>',
            status_code=200,
        )

    # Return hooks HTML fragment for HTMX
    return templates.TemplateResponse("partials/hooks_list.html", {
        "request": request,
        "video": video,
        "hooks": hooks,
    })


@router.post("/{video_id}/select-hook")
async def select_hook(
    request: Request,
    video_id: int,
    hook_text: str = Form(...),
    db: Session = Depends(get_db),
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return HTMLResponse("Video not found", status_code=404)

    video.selected_hook = hook_text
    db.commit()

    return RedirectResponse(url=f"/videos/{video_id}/editor", status_code=303)


@router.post("/{video_id}/run-pipeline")
async def start_pipeline(
    request: Request,
    video_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return HTMLResponse("Video not found", status_code=404)

    def _run(vid: int):
        _db = SessionLocal()
        try:
            asyncio.run(run_pipeline(vid, _db))
        finally:
            _db.close()

    background_tasks.add_task(_run, video_id)

    return RedirectResponse(url=f"/queue", status_code=303)


@router.get("/{video_id}")
async def video_detail(request: Request, video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return HTMLResponse("Video not found", status_code=404)
    return templates.TemplateResponse("video_detail.html", {"request": request, "video": video})
