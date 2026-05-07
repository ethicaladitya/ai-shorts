"""app/routers/costs.py — Cost tracking API + dashboard page."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from fastapi.templating import Jinja2Templates

from app.services.cost_tracker import (
    get_recent_events,
    get_spend_by_day,
    get_spend_by_persona,
    get_spend_by_provider,
    get_total_spend,
)

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/costs", tags=["costs"])


@router.get("/", response_class=HTMLResponse)
async def costs_page(request: Request, db: Session = Depends(get_db)):
    total = get_total_spend(db)
    by_persona = get_spend_by_persona(db)
    by_provider = get_spend_by_provider(db)
    by_day = get_spend_by_day(db, days=30)
    recent = get_recent_events(db, limit=100)
    return templates.TemplateResponse(
        "costs.html",
        {
            "request": request,
            "total_usd": total,
            "by_persona": by_persona,
            "by_provider": by_provider,
            "by_day": by_day,
            "recent": recent,
        },
    )


@router.get("/api/summary")
async def costs_summary(persona: str | None = None, db: Session = Depends(get_db)):
    return {
        "total_usd": round(get_total_spend(db, persona_name=persona), 4),
        "by_persona": get_spend_by_persona(db),
        "by_provider": get_spend_by_provider(db, persona_name=persona),
        "by_day": get_spend_by_day(db, days=30, persona_name=persona),
    }


@router.get("/api/events")
async def costs_events(persona: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    return get_recent_events(db, limit=limit, persona_name=persona)
