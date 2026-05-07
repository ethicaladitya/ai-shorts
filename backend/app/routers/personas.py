"""app/routers/personas.py — Persona CRUD + account management."""
from __future__ import annotations

import json
import os
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


def _persona_dict(p: PersonaAccount) -> dict:
    return {
        "id": p.id,
        "persona_name": p.persona_name,
        "display_name": p.display_name,
        "bio": p.bio,
        "reference_image_path": p.reference_image_path,
        "instagram_username": p.instagram_username,
        "onlyfans_username": p.onlyfans_username,
        "tiktok_username": p.tiktok_username,
        "voice_provider": p.voice_provider,
        "voice_profile_id": p.voice_profile_id,
        "content_style": p.content_style,
        "dm_auto_reply": p.dm_auto_reply,
        "dm_guardrails": json.loads(p.dm_guardrails or "[]"),
        "total_cost_usd": round(p.total_cost_usd or 0.0, 4),
        "total_posts": p.total_posts or 0,
        "total_videos": p.total_videos or 0,
        "is_active": p.is_active,
    }


@router.get("/", response_class=HTMLResponse)
async def personas_list(request: Request, db: Session = Depends(get_db)):
    personas = db.query(PersonaAccount).order_by(PersonaAccount.created_at.desc()).all()
    total_spend = get_total_spend(db)
    return templates.TemplateResponse(
        "personas.html",
        {
            "request": request,
            "personas": [_persona_dict(p) for p in personas],
            "total_spend": round(total_spend, 4),
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def persona_create_form(request: Request):
    return templates.TemplateResponse("persona_form.html", {"request": request, "persona": None})


@router.get("/{persona_name}", response_class=HTMLResponse)
async def persona_detail(request: Request, persona_name: str, db: Session = Depends(get_db)):
    p = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    if not p:
        return RedirectResponse("/personas/")
    costs = get_recent_events(db, limit=50, persona_name=persona_name)
    jobs = db.query(UGCJob).filter_by(persona_name=persona_name).order_by(UGCJob.created_at.desc()).limit(10).all()
    return templates.TemplateResponse(
        "persona_detail.html",
        {
            "request": request,
            "persona": _persona_dict(p),
            "costs": costs,
            "recent_jobs": [
                {"id": j.id, "topic": j.topic, "status": j.status.value,
                 "platform": j.platform, "cost": j.estimated_cost, "created_at": str(j.created_at)}
                for j in jobs
            ],
        },
    )


@router.get("/{persona_name}/edit", response_class=HTMLResponse)
async def persona_edit_form(request: Request, persona_name: str, db: Session = Depends(get_db)):
    p = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    return templates.TemplateResponse(
        "persona_form.html",
        {"request": request, "persona": _persona_dict(p) if p else None},
    )


# ── API endpoints ──────────────────────────────────────────────────────────────

@router.post("/api/personas")
async def create_persona(
    persona_name: str = Form(...),
    display_name: str = Form(""),
    bio: str = Form(""),
    instagram_username: str = Form(""),
    onlyfans_username: str = Form(""),
    tiktok_username: str = Form(""),
    voice_provider: str = Form("voicebox"),
    voice_profile_id: str = Form(""),
    content_style: str = Form("ugc"),
    dm_auto_reply: bool = Form(False),
    dm_guardrails: str = Form("[]"),
    db: Session = Depends(get_db),
):
    existing = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    if existing:
        return JSONResponse({"error": f"Persona '{persona_name}' already exists"}, status_code=400)

    p = PersonaAccount(
        persona_name=persona_name,
        display_name=display_name or persona_name,
        bio=bio,
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
    instagram_username: str = Form(""),
    onlyfans_username: str = Form(""),
    tiktok_username: str = Form(""),
    voice_provider: str = Form("voicebox"),
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

    dest = settings.assets_dir / "personas" / persona_name
    dest.mkdir(parents=True, exist_ok=True)
    img_path = dest / f"reference{Path(file.filename).suffix}"
    img_path.write_bytes(await file.read())
    p.reference_image_path = str(img_path)
    db.commit()
    return {"path": str(img_path)}


@router.delete("/api/personas/{persona_name}")
async def delete_persona(persona_name: str, db: Session = Depends(get_db)):
    p = db.query(PersonaAccount).filter_by(persona_name=persona_name).first()
    if p:
        db.delete(p)
        db.commit()
    return {"deleted": persona_name}
