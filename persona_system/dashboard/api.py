"""
persona_system/dashboard/api.py
FastAPI server — unified dashboard for the persona system.
Serves: content management, scheduling, DM inbox, analytics, and pipeline triggers.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from persona_system.shared.database import (
    ContentPiece, ContentStatus, DMThread, GeneratedImage, Platform,
    PostAnalytics, ScheduledPost, SessionLocal, get_db, init_db
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Persona System Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup ────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    logger.info("Persona system DB initialised")


# ── Health ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


# ── Dashboard summary ──────────────────────────────────────────────────────
@app.get("/api/dashboard")
async def dashboard_summary(db: Session = Depends(get_db)):
    total_content = db.query(ContentPiece).count()
    scheduled_count = db.query(ScheduledPost).filter(
        ScheduledPost.status == ContentStatus.SCHEDULED
    ).count()
    published_count = db.query(ScheduledPost).filter(
        ScheduledPost.status == ContentStatus.PUBLISHED
    ).count()
    pending_dms = db.query(DMThread).filter(
        DMThread.reply_sent == False,
        DMThread.auto_replied == False,
    ).count()
    total_images = db.query(GeneratedImage).count()

    # Avg performance score
    scores = db.query(PostAnalytics.performance_score).all()
    avg_score = round(sum(s[0] for s in scores if s[0]) / max(len(scores), 1), 4)

    return {
        "total_content_pieces": total_content,
        "scheduled_posts": scheduled_count,
        "published_posts": published_count,
        "pending_dm_replies": pending_dms,
        "total_images": total_images,
        "avg_performance_score": avg_score,
    }


# ── Content pieces ─────────────────────────────────────────────────────────
@app.get("/api/content")
async def list_content(
    persona: str = "default",
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(ContentPiece).filter(ContentPiece.persona == persona)
    if status:
        query = query.filter(ContentPiece.status == status)
    items = query.order_by(ContentPiece.created_at.desc()).limit(limit).all()
    return [
        {
            "id": c.id,
            "caption": c.caption,
            "pillar": c.pillar,
            "combined_score": c.combined_score,
            "video_file": c.video_file,
            "status": c.status.value if c.status else None,
            "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in items
    ]


class ContentApproveRequest(BaseModel):
    content_ids: list[int]


@app.post("/api/content/approve")
async def approve_content(req: ContentApproveRequest, db: Session = Depends(get_db)):
    updated = 0
    for cid in req.content_ids:
        piece = db.query(ContentPiece).filter(ContentPiece.id == cid).first()
        if piece:
            piece.status = ContentStatus.APPROVED
            updated += 1
    db.commit()
    return {"approved": updated}


# ── Pipeline trigger ────────────────────────────────────────────────────────
class PipelineRequest(BaseModel):
    persona: str = "default"
    pillar: str | None = None
    captions_count: int = 20
    scripts_count: int = 3
    image_count: int = 5
    generate_images: bool = True
    video_type: str = "loop"


@app.post("/api/pipeline/run")
async def trigger_pipeline(req: PipelineRequest, background_tasks: BackgroundTasks):
    """Trigger the full content generation pipeline in the background."""
    async def _run():
        from persona_system.scripts.run_pipeline import run_full_pipeline
        try:
            result = await run_full_pipeline(
                persona_name=req.persona,
                pillar=req.pillar,
                captions_count=req.captions_count,
                scripts_count=req.scripts_count,
                generate_images=req.generate_images,
                image_count=req.image_count,
                video_type=req.video_type,
            )
            logger.info("Pipeline complete: %s", result)
        except Exception as e:
            logger.error("Pipeline failed: %s", e)

    background_tasks.add_task(_run)
    return {"status": "started", "message": "Pipeline running in background"}


# ── Scheduling ─────────────────────────────────────────────────────────────
@app.get("/api/schedule")
async def get_schedule(days: int = 7, db: Session = Depends(get_db)):
    from persona_system.scheduler.scheduler import get_upcoming_posts
    return get_upcoming_posts(days)


class ScheduleRequest(BaseModel):
    persona: str = "default"
    platform: str = "instagram"
    content_ids: list[int]
    days_ahead: int = 7


@app.post("/api/schedule")
async def create_schedule(req: ScheduleRequest):
    from persona_system.scheduler.scheduler import schedule_content_batch
    platform = Platform(req.platform)
    result = schedule_content_batch(req.persona, platform, req.content_ids, req.days_ahead)
    return {"scheduled": result}


# ── Publisher trigger ───────────────────────────────────────────────────────
@app.post("/api/publish/run")
async def run_publisher(background_tasks: BackgroundTasks):
    async def _publish():
        from persona_system.automation.publisher import publish_due_posts
        await publish_due_posts()
    background_tasks.add_task(_publish)
    return {"status": "started"}


# ── DM management ──────────────────────────────────────────────────────────
@app.get("/api/dms")
async def list_dms(
    persona: str = "default",
    pending_only: bool = True,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(DMThread).filter(DMThread.persona == persona)
    if pending_only:
        query = query.filter(DMThread.reply_sent == False)
    threads = query.order_by(DMThread.last_message_at.desc()).limit(limit).all()
    return [
        {
            "id": t.id,
            "fan_username": t.fan_username,
            "category": t.category.value if t.category else None,
            "last_message": t.last_message,
            "reply_text": t.reply_text,
            "auto_replied": t.auto_replied,
            "approved_by_human": t.approved_by_human,
            "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        }
        for t in threads
    ]


class ApproveReplyRequest(BaseModel):
    variant_index: int = 0


@app.post("/api/dms/{thread_id}/approve")
async def approve_dm_reply(thread_id: int, req: ApproveReplyRequest):
    from persona_system.dm_engine.handler import approve_and_queue_reply
    chosen = await approve_and_queue_reply(thread_id, req.variant_index)
    return {"thread_id": thread_id, "chosen_reply": chosen}


# ── Analytics ───────────────────────────────────────────────────────────────
@app.get("/api/analytics")
async def get_analytics(persona: str = "default", limit: int = 30, db: Session = Depends(get_db)):
    rows = (
        db.query(PostAnalytics)
        .filter(PostAnalytics.persona == persona)
        .order_by(PostAnalytics.fetched_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "platform": r.platform.value if r.platform else None,
            "views": r.views,
            "likes": r.likes,
            "comments": r.comments,
            "saves": r.saves,
            "engagement_rate": r.engagement_rate,
            "performance_score": r.performance_score,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        }
        for r in rows
    ]


@app.get("/api/analytics/feedback")
async def analytics_feedback(persona: str = "default"):
    from persona_system.analytics_engine.tracker import generate_feedback_prompt_adjustments
    result = await generate_feedback_prompt_adjustments(persona)
    return result


@app.get("/api/analytics/patterns")
async def analytics_patterns(persona: str = "default"):
    from persona_system.analytics_engine.tracker import analyse_top_patterns
    return analyse_top_patterns(persona)


# ── Content scoring (ad-hoc) ────────────────────────────────────────────────
class ScoreRequest(BaseModel):
    text: str
    content_type: str = "caption"


@app.post("/api/score")
async def score_content(req: ScoreRequest):
    from persona_system.content_engine.generator import score_content as _score
    return await _score(req.text, req.content_type)


# ── Captions batch generator ────────────────────────────────────────────────
class CaptionRequest(BaseModel):
    persona: str = "default"
    count: int = 20
    pillar: str | None = None


@app.post("/api/captions/generate")
async def generate_captions_endpoint(req: CaptionRequest):
    from persona_system.content_engine.generator import generate_captions
    captions = await generate_captions(req.persona, req.count, req.pillar)
    return {"captions": captions, "count": len(captions)}


# ── Serve static dashboard ──────────────────────────────────────────────────
_dashboard_ui = Path(__file__).parent / "static"
if _dashboard_ui.exists():
    app.mount("/", StaticFiles(directory=str(_dashboard_ui), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("persona_system.dashboard.api:app", host="0.0.0.0", port=8100, reload=True)
