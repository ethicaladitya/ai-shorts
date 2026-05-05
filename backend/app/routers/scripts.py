"""Script editor router."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Video, VideoStatus
from app.services.script_generator import generate_script, regenerate_section, format_script

router = APIRouter(prefix="/videos")
templates = Jinja2Templates(directory="app/templates")


@router.get("/{video_id}/editor")
async def editor_page(request: Request, video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return HTMLResponse("Video not found", status_code=404)
    return templates.TemplateResponse("editor.html", {"request": request, "video": video})


@router.post("/{video_id}/generate-script")
async def gen_script(request: Request, video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return HTMLResponse("Video not found", status_code=404)

    try:
        script = await generate_script(
            video.selected_hook or "",
            video.topic,
            video.source_content or "",
            video.custom_notes or "",
            db,
        )
        video.script_raw = script
        video.status = VideoStatus.SCRIPT_READY
        db.commit()
    except Exception as e:
        return HTMLResponse(
            f'<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">'
            f'<strong>Error:</strong> {str(e)}</div>',
        )

    return templates.TemplateResponse("partials/script_editor.html", {
        "request": request,
        "video": video,
    })


@router.post("/{video_id}/update-script")
async def update_script(
    request: Request,
    video_id: int,
    script: str = Form(...),
    db: Session = Depends(get_db),
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return HTMLResponse("Video not found", status_code=404)

    video.script_raw = script
    db.commit()

    return HTMLResponse('<div class="text-green-600 text-sm font-medium">Script saved</div>')


@router.post("/{video_id}/regenerate-section")
async def regen_section(
    request: Request,
    video_id: int,
    section: str = Form(...),
    instructions: str = Form(""),
    db: Session = Depends(get_db),
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video or not video.script_raw:
        return HTMLResponse("Video or script not found", status_code=404)

    try:
        new_section = await regenerate_section(video.script_raw, section, video.topic, instructions)
        return HTMLResponse(
            f'<div class="bg-green-50 border border-green-200 rounded-lg p-4">'
            f'<h4 class="font-semibold text-green-800 mb-2">Regenerated [{section}]</h4>'
            f'<pre class="whitespace-pre-wrap text-sm text-gray-800">{new_section}</pre>'
            f'<p class="text-xs text-gray-500 mt-2">Copy and paste into your script above.</p></div>'
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">{str(e)}</div>'
        )


@router.post("/{video_id}/format-script")
async def fmt_script(request: Request, video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video or not video.script_raw:
        return HTMLResponse("No script to format", status_code=400)

    try:
        formatted = await format_script(video.script_raw)
        video.script_formatted = formatted
        db.commit()
    except Exception as e:
        return HTMLResponse(f'<div class="text-red-600">{str(e)}</div>')

    return templates.TemplateResponse("partials/formatted_script.html", {
        "request": request,
        "video": video,
    })
