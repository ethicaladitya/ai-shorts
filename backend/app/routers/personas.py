"""app/routers/personas.py — Persona CRUD + social connections."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PersonaAccount, CostLog, UGCJob
from fastapi.templating import Jinja2Templates
from sqlalchemy import func

from app.services.cost_tracker import get_total_spend, get_recent_events

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="/personas", tags=["personas"])

VOICE_OPTIONS = [
    ("maya:aditya",    "Maya — Aditya Shah (Indian-American male)"),
    ("maya:of_model",  "Maya — OF Model (warm, intimate female)"),
    ("maya",           "Maya — Default voice"),
    ("kokoro",         "Kokoro (local ONNX)"),
    ("voicebox",       "Voicebox (local REST)"),
    ("azure_openai",   "Azure OpenAI TTS"),
    ("azure_speech",   "Azure AI Speech (Neural)"),
    ("elevenlabs",     "ElevenLabs"),
]


def _persona_dict(p: PersonaAccount) -> dict:
    return {
        "id": p.id,
        "persona_name": p.persona_name,
        "display_name": p.display_name or p.persona_name,
        "bio": p.bio or "",
        "personality": p.personality or "",
        "reference_image_path": p.reference_image_path,
        "avatar_style": getattr(p, "avatar_style", "photo"),
        "instagram_username": p.instagram_username or "",
        "onlyfans_username": p.onlyfans_username or "",
        "tiktok_username": p.tiktok_username or "",
        "voice_provider": p.voice_provider or "maya",
        "voice_profile_id": p.voice_profile_id or "",
        "content_style": p.content_style or "ugc",
        "dm_auto_reply": p.dm_auto_reply,
        "dm_guardrails": json.loads(p.dm_guardrails or "[]"),
        "total_cost_usd": round(p.total_cost_usd or 0.0, 4),
        "total_posts": p.total_posts or 0,
        "total_videos": p.total_videos or 0,
        "is_active": p.is_active,
    }


def _avatar_url(p: PersonaAccount) -> str:
    if p.reference_image_path:
        name = p.persona_name
        # Serve from /persona_avatars/<name>/...
        path = Path(p.reference_image_path)
        return f"/persona_avatars/{name}/{path.name}" if path.exists() else ""
    return ""


@router.get("/", response_class=HTMLResponse)
async def personas_list(request: Request, db: Session = Depends(get_db)):
    personas = db.query(PersonaAccount).order_by(PersonaAccount.created_at.desc()).all()
    return templates.TemplateResponse("personas.html", {
        "request": request,
        "personas": [_persona_dict(p) for p in personas],
        "voice_options": VOICE_OPTIONS,
    })


@router.get("/new", response_class=HTMLResponse)
async def persona_create_form(request: Request):
    return templates.TemplateResponse("persona_form.html", {
        "request": request,
        "persona": None,
        "voice_options": VOICE_OPTIONS,
    })


@router.get("/{persona_name}", response_class=HTMLResponse)
async def persona_detail(request: Request, persona_name: str, db: Session = Depends(get_db)):
    p = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    if not p:
        return RedirectResponse("/personas/")
    costs = get_recent_events(db, limit=30, persona_name=persona_name)
    jobs = db.query(UGCJob).filter_by(persona_name=persona_name).order_by(UGCJob.created_at.desc()).limit(10).all()
    return templates.TemplateResponse("persona_detail.html", {
        "request": request,
        "persona": _persona_dict(p),
        "avatar_url": _avatar_url(p),
        "voice_options": VOICE_OPTIONS,
        "costs": costs,
        "recent_jobs": [
            {"id": j.id, "topic": j.topic, "status": j.status.value,
             "platform": j.platform, "cost": j.estimated_cost}
            for j in jobs
        ],
    })


@router.get("/{persona_name}/edit", response_class=HTMLResponse)
async def persona_edit_form(request: Request, persona_name: str, db: Session = Depends(get_db)):
    p = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    return templates.TemplateResponse("persona_form.html", {
        "request": request,
        "persona": _persona_dict(p) if p else None,
        "voice_options": VOICE_OPTIONS,
    })


# ── POST endpoints ─────────────────────────────────────────────────────────────

@router.post("/api/personas")
async def create_persona(
    persona_name: str = Form(...),
    display_name: str = Form(""),
    bio: str = Form(""),
    personality: str = Form(""),
    instagram_username: str = Form(""),
    onlyfans_username: str = Form(""),
    tiktok_username: str = Form(""),
    voice_provider: str = Form("maya"),
    voice_profile_id: str = Form(""),
    content_style: str = Form("ugc"),
    dm_auto_reply: bool = Form(False),
    dm_guardrails: str = Form("[]"),
    db: Session = Depends(get_db),
):
    existing = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    if existing:
        return HTMLResponse(
            f'<div class="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">'
            f'Persona "{persona_name}" already exists.</div>'
        )
    p = PersonaAccount(
        persona_name=persona_name,
        display_name=display_name or persona_name,
        bio=bio,
        personality=personality,
        instagram_username=instagram_username,
        onlyfans_username=onlyfans_username,
        tiktok_username=tiktok_username,
        voice_provider=voice_provider,
        voice_profile_id=voice_profile_id,
        content_style=content_style,
        dm_auto_reply=dm_auto_reply,
        dm_guardrails=dm_guardrails,
    )
    db.add(p)
    db.commit()
    return RedirectResponse(f"/personas/{persona_name}", status_code=303)


@router.post("/api/personas/{persona_name}")
async def update_persona(
    persona_name: str,
    display_name: str = Form(""),
    bio: str = Form(""),
    personality: str = Form(""),
    instagram_username: str = Form(""),
    onlyfans_username: str = Form(""),
    tiktok_username: str = Form(""),
    voice_provider: str = Form("maya"),
    voice_profile_id: str = Form(""),
    content_style: str = Form("ugc"),
    dm_auto_reply: bool = Form(False),
    dm_guardrails: str = Form("[]"),
    is_active: bool = Form(True),
    db: Session = Depends(get_db),
):
    p = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    if not p:
        return JSONResponse({"error": "Not found"}, status_code=404)
    p.display_name = display_name or persona_name
    p.bio = bio
    p.personality = personality
    p.instagram_username = instagram_username
    p.onlyfans_username = onlyfans_username
    p.tiktok_username = tiktok_username
    p.voice_provider = voice_provider
    p.voice_profile_id = voice_profile_id
    p.content_style = content_style
    p.dm_auto_reply = dm_auto_reply
    p.dm_guardrails = dm_guardrails
    p.is_active = is_active
    db.commit()
    return RedirectResponse(f"/personas/{persona_name}", status_code=303)


@router.post("/api/personas/{persona_name}/upload_image")
async def upload_persona_image(
    persona_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    from app.config import settings
    p = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    if not p:
        return JSONResponse({"error": "Not found"}, status_code=404)

    dest = settings.data_dir / "personas" / persona_name
    dest.mkdir(parents=True, exist_ok=True)
    img_path = dest / f"avatar{Path(file.filename).suffix}"
    img_path.write_bytes(await file.read())
    p.reference_image_path = str(img_path)
    db.commit()
    return JSONResponse({"path": str(img_path), "url": f"/persona_avatars/{persona_name}/avatar{Path(file.filename).suffix}"})


@router.delete("/api/personas/{persona_name}")
async def delete_persona(persona_name: str, db: Session = Depends(get_db)):
    p = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    if p:
        db.delete(p)
        db.commit()
    return {"deleted": persona_name}
