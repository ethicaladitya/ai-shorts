"""Knowledge panel router."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import KnowledgeEntry

router = APIRouter(prefix="/knowledge")
templates = Jinja2Templates(directory="app/templates")

CATEGORIES = [
    ("examples", "Example Scripts"),
    ("tone", "Tone & Voice Instructions"),
    ("sources", "Content Sources (WP/RSS)"),
    ("cta", "CTA Preferences"),
    ("prompts", "Custom Prompts"),
    ("audience", "Audience Profile"),
]


@router.get("")
async def knowledge_page(request: Request, db: Session = Depends(get_db)):
    entries = db.query(KnowledgeEntry).order_by(KnowledgeEntry.category, KnowledgeEntry.id).all()
    grouped = {}
    for cat_key, cat_label in CATEGORIES:
        grouped[cat_key] = {
            "label": cat_label,
            "entries": [e for e in entries if e.category == cat_key],
        }
    return templates.TemplateResponse("knowledge.html", {
        "request": request,
        "grouped": grouped,
        "categories": CATEGORIES,
    })


@router.post("/add")
async def add_entry(
    request: Request,
    category: str = Form(...),
    title: str = Form(""),
    content: str = Form(...),
    db: Session = Depends(get_db),
):
    entry = KnowledgeEntry(category=category, title=title, content=content)
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return HTMLResponse(
        f'<div class="flex items-center justify-between bg-white border border-gray-200 rounded-lg p-3 mb-2" id="entry-{entry.id}">'
        f'<div><span class="font-medium text-gray-900">{entry.title or "Untitled"}</span>'
        f'<p class="text-sm text-gray-600 mt-1">{entry.content[:200]}</p></div>'
        f'<div class="flex gap-2">'
        f'<button hx-post="/knowledge/{entry.id}/toggle" hx-target="#entry-{entry.id}" hx-swap="outerHTML"'
        f' class="text-xs px-2 py-1 bg-gray-100 rounded hover:bg-gray-200">{"Disable" if entry.is_active else "Enable"}</button>'
        f'<button hx-delete="/knowledge/{entry.id}" hx-target="#entry-{entry.id}" hx-swap="outerHTML"'
        f' hx-confirm="Delete this entry?" class="text-xs px-2 py-1 bg-red-50 text-red-600 rounded hover:bg-red-100">Delete</button>'
        f'</div></div>'
    )


@router.post("/{entry_id}/toggle")
async def toggle_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(KnowledgeEntry).filter(KnowledgeEntry.id == entry_id).first()
    if not entry:
        return HTMLResponse("", status_code=404)

    entry.is_active = not entry.is_active
    db.commit()

    active_class = "" if entry.is_active else "opacity-50"
    return HTMLResponse(
        f'<div class="flex items-center justify-between bg-white border border-gray-200 rounded-lg p-3 mb-2 {active_class}" id="entry-{entry.id}">'
        f'<div><span class="font-medium text-gray-900">{entry.title or "Untitled"}</span>'
        f'<p class="text-sm text-gray-600 mt-1">{entry.content[:200]}</p></div>'
        f'<div class="flex gap-2">'
        f'<button hx-post="/knowledge/{entry.id}/toggle" hx-target="#entry-{entry.id}" hx-swap="outerHTML"'
        f' class="text-xs px-2 py-1 bg-gray-100 rounded hover:bg-gray-200">{"Disable" if entry.is_active else "Enable"}</button>'
        f'<button hx-delete="/knowledge/{entry.id}" hx-target="#entry-{entry.id}" hx-swap="outerHTML"'
        f' hx-confirm="Delete this entry?" class="text-xs px-2 py-1 bg-red-50 text-red-600 rounded hover:bg-red-100">Delete</button>'
        f'</div></div>'
    )


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(KnowledgeEntry).filter(KnowledgeEntry.id == entry_id).first()
    if entry:
        db.delete(entry)
        db.commit()
    return HTMLResponse("")
