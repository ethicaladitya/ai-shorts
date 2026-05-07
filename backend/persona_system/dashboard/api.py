"""
persona_system/dashboard/api.py
FastAPI server — unified dashboard for the persona system.
Phase 2 upgrade: settings API, live log streaming, media library, platform queues.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from persona_system.shared.database import (
    ContentPiece, ContentStatus, DMThread, GeneratedImage, Platform,
    PostAnalytics, ScheduledPost, SessionLocal, get_db, init_db
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Persona System Dashboard", version="2.0.0")

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
    logger.info("Persona system DB initialised (v2)")


# ── Health ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat(), "version": "2.0"}


# ── Dashboard summary ──────────────────────────────────────────────────────
@app.get("/api/dashboard")
async def dashboard_summary(db: Session = Depends(get_db)):
    try:
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
        scores = db.query(PostAnalytics.performance_score).all()
        avg_score = round(sum(s[0] for s in scores if s[0]) / max(len(scores), 1), 4)
        ig_count = db.query(ContentPiece).filter(ContentPiece.platform == Platform.INSTAGRAM).count()
        of_count = db.query(ContentPiece).filter(ContentPiece.platform == Platform.ONLYFANS).count()

        return {
            "total_content_pieces": total_content,
            "instagram_pieces": ig_count,
            "onlyfans_pieces": of_count,
            "scheduled_posts": scheduled_count,
            "published_posts": published_count,
            "pending_dm_replies": pending_dms,
            "total_images": total_images,
            "avg_performance_score": avg_score,
        }
    except Exception as e:
        logger.error("Error in dashboard_summary: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Settings API ───────────────────────────────────────────────────────────
@app.get("/api/settings")
async def get_settings():
    """Return all persona system settings (non-secret values only)."""
    from persona_system.config.settings import settings
    return {
        "video_clip_provider":  settings.video_clip_provider,
        "lipsync_provider":     settings.lipsync_provider,
        "default_video_mode":   settings.default_video_mode,
        "ig_content_enabled":   settings.ig_content_enabled,
        "of_content_enabled":   settings.of_content_enabled,
        "voice_provider":       settings.voice_provider,
        "image_provider":       getattr(settings, 'image_provider', 'gpt-image-2'),
        "azure_openai_deployment_mini": settings.azure_openai_deployment_mini,
        "sd_base_url":          settings.sd_base_url,
        "svd_base_url":         settings.svd_base_url,
        "svd_motion_bucket_id": settings.svd_motion_bucket_id,
        "svd_num_frames":       settings.svd_num_frames,
        "svd_fps":              settings.svd_fps,
        "kling_model":          settings.kling_model,
        "kling_clip_duration":  settings.kling_clip_duration,
        "kling_api_key_set":    bool(settings.kling_api_key),
        "hedra_api_key":        bool(settings.hedra_api_key),  # True/False only, never expose key
        "elevenlabs_voice_id":  settings.elevenlabs_voice_id,
        "azure_openai_dalle_deployment": settings.azure_openai_dalle_deployment,
        "latentsync_dir":       settings.latentsync_dir,
        "latentsync_dir_exists": bool(settings.latentsync_dir and Path(settings.latentsync_dir).exists()),
        "voicebox_url":         settings.voicebox_url,
        "content_score_min":    settings.content_score_min,
        "active_persona":       settings.active_persona,
        "azure_openai_tts_endpoint":    settings.azure_openai_tts_endpoint,
        "azure_openai_tts_deployment":  settings.azure_openai_tts_deployment,
    }


class SettingsUpdateRequest(BaseModel):
    voice_provider: str | None = None
    video_clip_provider: str | None = None
    lipsync_provider: str | None = None
    default_video_mode: str | None = None
    ig_content_enabled: bool | None = None
    of_content_enabled: bool | None = None
    svd_motion_bucket_id: int | None = None
    kling_clip_duration: int | None = None
    content_score_min: float | None = None
    image_provider: str | None = None
    azure_openai_deployment_mini: str | None = None
    hedra_api_key: str | None = None
    elevenlabs_voice_id: str | None = None


@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    """
    Update .env settings at runtime. Writes back to .env file.
    Changes take effect on next pipeline run (settings are re-read lazily).
    """
    from persona_system.config.settings import settings
    import os

    env_path = settings.project_root / ".env"
    if not env_path.exists():
        raise HTTPException(status_code=404, detail=".env file not found")

    lines = env_path.read_text().splitlines(keepends=True)
    updates = {k: str(v) for k, v in req.dict(exclude_none=True).items() if v is not None}

    # Map Python field names → ENV var names
    field_to_env = {
        "voice_provider":       "VOICE_PROVIDER",
        "video_clip_provider":  "VIDEO_CLIP_PROVIDER",
        "lipsync_provider":     "LIPSYNC_PROVIDER",
        "default_video_mode":   "DEFAULT_VIDEO_MODE",
        "ig_content_enabled":   "IG_CONTENT_ENABLED",
        "of_content_enabled":   "OF_CONTENT_ENABLED",
        "svd_motion_bucket_id": "SVD_MOTION_BUCKET_ID",
        "kling_clip_duration":  "KLING_CLIP_DURATION",
        "content_score_min":    "CONTENT_SCORE_MIN",
        "image_provider":       "IMAGE_PROVIDER",
        "azure_openai_deployment_mini": "AZURE_OPENAI_DEPLOYMENT_MINI",
        "hedra_api_key":        "HEDRA_API_KEY",
        "elevenlabs_voice_id":  "ELEVENLABS_VOICE_ID",
    }

    changed: list[str] = []
    for field, value in updates.items():
        env_key = field_to_env.get(field)
        if not env_key:
            continue
        found = False
        for idx, line in enumerate(lines):
            if line.startswith(f"{env_key}=") or line.startswith(f"# {env_key}="):
                lines[idx] = f"{env_key}={value}\n"
                found = True
                break
        if not found:
            lines.append(f"{env_key}={value}\n")
        changed.append(env_key)

    env_path.write_text("".join(lines))
    logger.info("Settings updated: %s", changed)
    return {"updated": changed, "message": "Settings saved. Restart to apply all changes."}


# ── Content pieces ─────────────────────────────────────────────────────────
def _get_media_url(file_path: str | None) -> str | None:
    if not file_path:
        return None
    p = Path(file_path)
    parts = p.parts
    if "persona_media" in parts:
        idx = parts.index("persona_media")
        return f"/persona_media/{Path(*parts[idx + 1:])}"
    return f"/output/{p.name}"


@app.get("/api/content")
async def list_content(
    persona: str = "default",
    status: str | None = None,
    platform: str | None = None,
    content_tier: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    try:
        query = db.query(ContentPiece).filter(ContentPiece.persona == persona)
        if status:
            query = query.filter(ContentPiece.status == status)
        if platform:
            try:
                query = query.filter(ContentPiece.platform == Platform(platform))
            except ValueError:
                pass
        if content_tier:
            query = query.filter(ContentPiece.content_tier == content_tier)

        items = query.order_by(ContentPiece.created_at.desc()).limit(limit).all()
        results = []
        for c in items:
            img = db.query(GeneratedImage).filter(GeneratedImage.id == c.image_id).first() if c.image_id else None
            results.append({
                "id":            c.id,
                "caption":       c.caption,
                "pillar":        c.pillar,
                "combined_score": c.combined_score,
                "video_file":    c.video_file,
                "video_url":     _get_media_url(c.video_file),
                "thumbnail_url": _get_media_url(c.thumbnail_file),
                "image_id":      c.image_id,
                "image_url":     _get_media_url(img.file_path) if img else None,
                "status":        c.status.value if c.status else None,
                "platform":      c.platform.value if c.platform else None,
                "content_tier":  c.content_tier,
                "video_mode":    c.video_mode,
                "logs":          c.logs,
                "error":         c.error,
                "scheduled_at":  c.scheduled_at.isoformat() if c.scheduled_at else None,
                "created_at":    c.created_at.isoformat() if c.created_at else None,
            })
        return results
    except Exception as e:
        logger.error("Error in list_content: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Live log streaming (SSE) ───────────────────────────────────────────────
@app.get("/api/content/{piece_id}/logs/stream")
async def stream_logs(piece_id: int):
    """Server-Sent Events stream for live pipeline log updates."""
    async def _generator():
        last_log = ""
        stale_count = 0
        while stale_count < 30:  # stop after 60s of no change
            db = SessionLocal()
            try:
                piece = db.query(ContentPiece).filter(ContentPiece.id == piece_id).first()
                if not piece:
                    yield f"data: ERROR: piece {piece_id} not found\n\n"
                    break
                current_log = piece.logs or ""
                if current_log != last_log:
                    # Send only new lines
                    new_lines = current_log[len(last_log):]
                    for line in new_lines.strip().splitlines():
                        yield f"data: {line}\n\n"
                    last_log = current_log
                    stale_count = 0

                if piece.status not in (ContentStatus.PROCESSING, ContentStatus.DRAFT):
                    yield f"data: [DONE] Final status: {piece.status.value}\n\n"
                    break
            finally:
                db.close()

            stale_count += 1
            await asyncio.sleep(2)

    return StreamingResponse(_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# ── Content approve ────────────────────────────────────────────────────────
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


# ── Media Library ──────────────────────────────────────────────────────────
@app.get("/api/media/images")
async def list_images(
    persona: str = "default",
    platform: str | None = None,
    content_tier: str | None = None,
    gallery_id: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """Return generated images for the media library tab."""
    try:
        query = db.query(GeneratedImage).filter(GeneratedImage.persona == persona)
        if platform:
            query = query.filter(GeneratedImage.platform == platform)
        if content_tier:
            query = query.filter(GeneratedImage.content_tier == content_tier)
        if gallery_id:
            query = query.filter(GeneratedImage.gallery_id == gallery_id)

        images = query.order_by(GeneratedImage.created_at.desc()).limit(limit).all()
        return [
            {
                "id":           img.id,
                "file_path":    img.file_path,
                "url":          _get_media_url(img.file_path),
                "platform":     img.platform,
                "content_tier": img.content_tier,
                "gallery_id":   img.gallery_id,
                "activity":     img.activity,
                "preset":       img.preset,
                "width":        img.width,
                "height":       img.height,
                "score":        img.score,
                "tags":         img.tags or [],
                "created_at":   img.created_at.isoformat() if img.created_at else None,
            }
            for img in images
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/media/galleries")
async def list_galleries(persona: str = "default", db: Session = Depends(get_db)):
    """Return distinct gallery sets."""
    try:
        from sqlalchemy import func, distinct
        rows = (
            db.query(
                GeneratedImage.gallery_id,
                func.count(GeneratedImage.id).label("count"),
                func.min(GeneratedImage.created_at).label("created_at"),
                GeneratedImage.platform,
                GeneratedImage.content_tier,
            )
            .filter(
                GeneratedImage.persona == persona,
                GeneratedImage.gallery_id.isnot(None),
            )
            .group_by(GeneratedImage.gallery_id)
            .order_by(func.min(GeneratedImage.created_at).desc())
            .limit(50)
            .all()
        )
        return [
            {
                "gallery_id":   r.gallery_id,
                "count":        r.count,
                "platform":     r.platform,
                "content_tier": r.content_tier,
                "created_at":   r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Pipeline trigger ────────────────────────────────────────────────────────
class PipelineRequest(BaseModel):
    persona: str = "default"
    pillar: str | None = None
    captions_count: int = 20
    scripts_count: int = 3
    image_count: int = 5
    generate_images: bool = True
    generate_gallery: bool = False
    gallery_size: int = 5
    video_mode: str | None = None
    video_clip_provider: str | None = None
    lipsync_provider: str | None = None
    image_provider: str = "azure_dalle"
    voice_provider: str | None = None  # None = use .env / settings default
    platforms: list[str] | None = None
    content_tier: str = "standard"
    reference_image: str | None = None  # absolute path to persona reference image


@app.get("/api/persona-images")
async def list_persona_images(persona: str = "default"):
    """Return all generated persona images as {path, url} objects for the model picker."""
    from persona_system.config.settings import settings
    images_root = settings.media_root / "images" / persona
    result = []
    if images_root.exists():
        for img_path in sorted(images_root.rglob("*.jpg"), reverse=True):
            try:
                rel = img_path.relative_to(settings.media_root)
                url = f"/persona_media/{rel}"
                result.append({"path": str(img_path), "url": url, "name": img_path.name})
            except ValueError:
                pass
    return result[:30]


@app.get("/api/personas")
async def list_personas():
    """List all available persona configs with their display info and reference image URLs."""
    import yaml
    from persona_system.config.settings import settings

    personas = []
    for yaml_path in sorted(settings.personas_dir.glob("*.yaml")):
        try:
            cfg = yaml.safe_load(yaml_path.read_text())
            persona_id = yaml_path.stem  # e.g. "default", "nova_warm"
            display_name = cfg.get("display_name") or cfg.get("name", persona_id)
            ref_img_path = cfg.get("reference_image", "")

            # Build a URL for the reference image if it lives inside media_root
            ref_img_url = None
            if ref_img_path:
                p = Path(ref_img_path)
                try:
                    rel = p.relative_to(settings.media_root)
                    ref_img_url = f"/persona_media/{rel}"
                except ValueError:
                    ref_img_url = None  # outside media_root, can't serve it

            personas.append({
                "id": persona_id,
                "display_name": display_name,
                "reference_image_path": ref_img_path,
                "reference_image_url": ref_img_url,
            })
        except Exception as e:
            logger.warning("Could not load persona %s: %s", yaml_path.name, e)

    return personas


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
                generate_gallery=req.generate_gallery,
                gallery_size=req.gallery_size,
                video_mode=req.video_mode,
                video_clip_provider=req.video_clip_provider,
                lipsync_provider=req.lipsync_provider,
                image_provider=req.image_provider,
                voice_provider=req.voice_provider,
                platforms=req.platforms,
                content_tier=req.content_tier,
                reference_image=req.reference_image,
            )
            logger.info("Pipeline complete: %s", result)
        except Exception as e:
            logger.error("Pipeline failed: %s", e, exc_info=True)

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
            "id":               t.id,
            "fan_username":     t.fan_username,
            "category":         t.category.value if t.category else None,
            "last_message":     t.last_message,
            "reply_text":       t.reply_text,
            "auto_replied":     t.auto_replied,
            "approved_by_human":t.approved_by_human,
            "last_message_at":  t.last_message_at.isoformat() if t.last_message_at else None,
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
            "id":               r.id,
            "platform":         r.platform.value if r.platform else None,
            "views":            r.views,
            "likes":            r.likes,
            "comments":         r.comments,
            "saves":            r.saves,
            "engagement_rate":  r.engagement_rate,
            "performance_score":r.performance_score,
            "fetched_at":       r.fetched_at.isoformat() if r.fetched_at else None,
        }
        for r in rows
    ]


@app.get("/api/analytics/feedback")
async def analytics_feedback(persona: str = "default"):
    from persona_system.analytics_engine.tracker import generate_feedback_prompt_adjustments
    return await generate_feedback_prompt_adjustments(persona)


@app.get("/api/analytics/patterns")
async def analytics_patterns(persona: str = "default"):
    from persona_system.analytics_engine.tracker import analyse_top_patterns
    return analyse_top_patterns(persona)


# ── Content scoring ────────────────────────────────────────────────────────
class ScoreRequest(BaseModel):
    text: str
    content_type: str = "caption"


@app.post("/api/score")
async def score_content(req: ScoreRequest):
    from persona_system.content_engine.generator import score_content as _score
    return await _score(req.text, req.content_type)


# ── Captions generator ─────────────────────────────────────────────────────
class CaptionRequest(BaseModel):
    persona: str = "default"
    count: int = 20
    pillar: str | None = None


@app.post("/api/captions/generate")
async def generate_captions_endpoint(req: CaptionRequest):
    from persona_system.content_engine.generator import generate_captions
    captions = await generate_captions(req.persona, req.count, req.pillar)
    return {"captions": captions, "count": len(captions)}


# ── Persona CRUD ────────────────────────────────────────────────────────────

class PersonaCreateRequest(BaseModel):
    id: str                     # filename stem, e.g. "luna_v1"
    display_name: str
    bio: str = ""
    trigger_word: str = ""
    appearance: dict = {}       # hair, eyes, skin, style, signature_look
    voice: dict = {}            # profile_name, pace, tone, quirks
    content_pillars: list[str] = []
    activity_scenes: dict = {}  # pillar → list of {scene, motion}
    platforms: list[str] = []   # ["instagram", "onlyfans", "tiktok"]
    image_gen: dict = {}        # base_positive, base_negative
    schedule: dict = {}         # posts_per_week per platform


@app.get("/api/personas/{persona_id}")
async def get_persona(persona_id: str):
    import yaml
    from persona_system.config.settings import settings
    yaml_path = settings.personas_dir / f"{persona_id}.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail="Persona not found")
    cfg = yaml.safe_load(yaml_path.read_text())
    return cfg


@app.post("/api/personas")
async def create_persona(req: PersonaCreateRequest):
    import re
    import yaml
    from persona_system.config.settings import settings

    # Sanitise the persona ID to a safe filename
    persona_id = re.sub(r"[^a-z0-9_\-]", "_", req.id.lower().strip())
    if not persona_id:
        raise HTTPException(status_code=400, detail="Invalid persona id")

    yaml_path = settings.personas_dir / f"{persona_id}.yaml"
    if yaml_path.exists():
        raise HTTPException(status_code=409, detail="Persona already exists — use PUT to update")

    cfg = _build_persona_yaml(persona_id, req)
    yaml_path.write_text(yaml.dump(cfg, allow_unicode=True, sort_keys=False))
    logger.info("Created persona %s", persona_id)
    return {"id": persona_id, "status": "created"}


@app.put("/api/personas/{persona_id}")
async def update_persona(persona_id: str, req: PersonaCreateRequest):
    import yaml
    from persona_system.config.settings import settings

    yaml_path = settings.personas_dir / f"{persona_id}.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail="Persona not found")

    existing = yaml.safe_load(yaml_path.read_text())
    updated = _build_persona_yaml(persona_id, req)
    # Preserve reference_image if already set and not overridden
    if existing.get("reference_image") and not updated.get("reference_image"):
        updated["reference_image"] = existing["reference_image"]
    yaml_path.write_text(yaml.dump(updated, allow_unicode=True, sort_keys=False))
    logger.info("Updated persona %s", persona_id)
    return {"id": persona_id, "status": "updated"}


@app.delete("/api/personas/{persona_id}")
async def delete_persona(persona_id: str):
    from persona_system.config.settings import settings

    if persona_id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete the default persona")
    yaml_path = settings.personas_dir / f"{persona_id}.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail="Persona not found")
    yaml_path.unlink()
    logger.info("Deleted persona %s", persona_id)
    return {"status": "deleted"}


@app.post("/api/personas/{persona_id}/reference_image")
async def upload_reference_image(persona_id: str):
    """Placeholder — actual upload handled via multipart below."""
    raise HTTPException(status_code=400, detail="Use multipart upload endpoint")


@app.post("/api/personas/{persona_id}/upload_image")
async def upload_persona_image(persona_id: str, file: UploadFile = File(...)):
    import shutil
    import yaml
    from persona_system.config.settings import settings

    yaml_path = settings.personas_dir / f"{persona_id}.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail="Persona not found")

    dest_dir = settings.media_root / "images" / persona_id / "reference"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "reference.jpg"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    cfg = yaml.safe_load(yaml_path.read_text())
    cfg["reference_image"] = str(dest)
    yaml_path.write_text(yaml.dump(cfg, allow_unicode=True, sort_keys=False))
    return {"reference_image": str(dest), "url": f"/persona_media/images/{persona_id}/reference/reference.jpg"}


def _build_persona_yaml(persona_id: str, req: PersonaCreateRequest) -> dict:
    """Convert PersonaCreateRequest into the YAML dict structure."""
    platforms_cfg = {}
    for platform in (req.platforms or ["instagram", "onlyfans"]):
        if platform in ("instagram", "instagram_feed"):
            platforms_cfg["instagram_feed"] = {"width": 1080, "height": 1350, "aspect": "4:5", "content_tier": "teaser"}
            platforms_cfg["instagram_reels"] = {"width": 1080, "height": 1920, "aspect": "9:16", "content_tier": "teaser"}
        elif platform == "onlyfans":
            platforms_cfg["onlyfans_photo"] = {"width": 1080, "height": 1350, "aspect": "4:5", "content_tier": "standard"}
            platforms_cfg["onlyfans_video"] = {"width": 1080, "height": 1920, "aspect": "9:16", "content_tier": "standard"}
        elif platform == "tiktok":
            platforms_cfg["tiktok"] = {"width": 1080, "height": 1920, "aspect": "9:16", "content_tier": "teaser"}

    trigger = req.trigger_word or f"{persona_id}_v1"
    appearance = req.appearance or {}
    image_gen = req.image_gen or {}

    return {
        "name": req.display_name,
        "display_name": req.display_name,
        "trigger_word": trigger,
        "bio": req.bio,
        "appearance": {
            "hair": appearance.get("hair", ""),
            "eyes": appearance.get("eyes", ""),
            "skin": appearance.get("skin", ""),
            "style": appearance.get("style", ""),
            "signature_look": appearance.get("signature_look", ""),
        },
        "voice": {
            "profile_name": req.voice.get("profile_name", "soft_feminine_v1"),
            "pace": req.voice.get("pace", "medium"),
            "tone": req.voice.get("tone", "warm"),
            "quirks": req.voice.get("quirks", []),
            "emotion_presets": req.voice.get("emotion_presets", {}),
        },
        "caption_style": {
            "case": "lowercase",
            "punctuation": "minimal",
            "emoji_density": "rare",
            "avg_length_words": 12,
            "emotional_register": "authentic",
            "slang": [],
        },
        "contradictions": [],
        "dm_style": {
            "opening": "casual",
            "warm": "opens up slowly",
            "high_value": "playfully elusive",
            "hard_limits": ["never confirm real name", "never share live location"],
        },
        "content_pillars": req.content_pillars or [],
        "activity_scenes": req.activity_scenes or _default_activity_scenes(),
        "platforms": platforms_cfg,
        "image_gen": {
            "base_positive": image_gen.get(
                "base_positive",
                f"{trigger}, photorealistic, 8k, cinematic lighting, detailed skin texture, sharp focus"
            ),
            "base_negative": image_gen.get(
                "base_negative",
                "cartoon, anime, painting, blurry, watermark, logo, duplicate, ugly, bad anatomy, low quality"
            ),
            "lighting_presets": {
                "golden_hour": "warm orange backlight, rim light, bokeh background, golden hour",
                "studio": "soft box key light, white backdrop, even fill light, studio",
                "natural": "window light, soft diffused daylight, clean background, natural",
                "moody": "single practical lamp, high contrast, deep shadows, moody",
                "neon_night": "pink and purple neon reflections, night exterior, neon",
            },
        },
        "schedule": {
            "instagram": {"posts_per_week": 7, "preferred_hours": [8, 12, 18, 21], "randomize_minutes": 30},
            "onlyfans": {"posts_per_week": 14, "preferred_hours": [10, 14, 20, 23], "randomize_minutes": 45},
        },
    }


def _default_activity_scenes() -> dict:
    return {
        "daily life": [
            {"scene": "sitting at café window, coffee in hand, thoughtful gaze outside", "motion": "slow sip, turns head toward camera, eyebrow slightly raised"},
            {"scene": "walking through mall, shopping bags, casual outfit, natural lighting", "motion": "natural walk, glances at camera briefly, slight smile"},
            {"scene": "in kitchen making food, morning light, cosy home", "motion": "stirs something, looks up at camera, soft smile"},
            {"scene": "on park bench, reading or phone, golden hour light", "motion": "looks up from phone, squints slightly in sun, relaxed exhale"},
        ],
        "home vibes": [
            {"scene": "sitting on bed looking at phone glow, late night, relaxed pose", "motion": "slow gentle breathing, phone glow on face, subtle eye movement"},
            {"scene": "lying on sofa, oversized hoodie, watching something off-camera", "motion": "shifts position slightly, glances at camera curiously"},
            {"scene": "getting ready in bathroom mirror, soft morning light", "motion": "reaches forward to fix hair, glances at reflection, soft smile"},
        ],
        "outdoor moments": [
            {"scene": "walking in park, trees behind, casual clothes, dappled light", "motion": "slow walk toward camera, hair moves in breeze, natural smile"},
            {"scene": "at outdoor café patio, sunglasses, summer vibes", "motion": "takes off sunglasses slowly, looks directly at camera"},
            {"scene": "on rooftop or balcony, city behind, golden hour", "motion": "leans on railing, turns head slowly toward camera, contemplative"},
        ],
    }


# ── Serve static dashboard ──────────────────────────────────────────────────
_dashboard_ui = Path(__file__).parent / "static"
if _dashboard_ui.exists():
    app.mount("/", StaticFiles(directory=str(_dashboard_ui), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("persona_system.dashboard.api:app", host="0.0.0.0", port=8100, reload=True)
