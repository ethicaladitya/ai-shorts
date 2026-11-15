"""Faceless reels router — AI voiceover + B-roll stock footage."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PersonaAccount

router = APIRouter(prefix="/faceless", tags=["faceless"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def faceless_create(request: Request, db: Session = Depends(get_db)):
    personas = db.query(PersonaAccount).filter_by(is_active=True).all()
    return templates.TemplateResponse("faceless/create.html", {
        "request": request,
        "personas": personas,
    })


@router.post("/create")
async def faceless_create_post(
    request: Request,
    topic: str = Form(...),
    persona_id: str = Form(""),
    voice_provider: str = Form("maya:aditya"),
    style: str = Form("informational"),
    platform: str = Form("instagram"),
    db: Session = Depends(get_db),
):
    # For now redirect to the standard video creation with notes
    # TODO: implement full faceless pipeline with stock footage
    return RedirectResponse(f"/videos/create?topic={topic}&voice={voice_provider}", status_code=303)
