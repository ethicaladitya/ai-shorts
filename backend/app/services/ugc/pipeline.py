"""UGC video pipeline orchestrator — 9-stage sequential pipeline with approval checkpoint."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models import UGCJob, UGCJobStatus
from app.services.ugc.checkpoint_ui import run_checkpoint
from app.services.ugc.config.defaults import (
    PLATFORM_PRESETS,
    estimate_cost,
    resolve_providers,
)
from app.services.ugc.image_generator import generate_scene_images
from app.services.ugc.script_generator import UGCScript, generate_ugc_script
from app.services.ugc.talking_head_service import generate_talking_head
from app.services.ugc.utils.job_state import write_state
from app.services.ugc.utils.retry import async_retry
from app.services.ugc.utils.temp_files import TempJobDir
from app.services.ugc.video_assembler import assemble_ugc_video
from app.services.ugc.voice_service import generate_ugc_voice

logger = logging.getLogger(__name__)


def _update_job(db, job: UGCJob, status: UGCJobStatus, step: str, progress: float, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    job.status = status
    job.step = step
    job.progress = progress
    job.log = (job.log or "") + f"\n[{ts}] {msg}"
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    write_state(job.id, status=status.value, step=step, progress=progress, last_log=msg)


async def run_ugc_pipeline(job_id: int) -> None:
    """Run the full UGC video pipeline for a single job."""
    db = SessionLocal()
    try:
        job = db.query(UGCJob).filter(UGCJob.id == job_id).first()
        if not job:
            logger.error("UGC job %d not found", job_id)
            return

        providers = resolve_providers(settings)
        # Apply job-level provider overrides if set (snapshotted at creation)
        if job.script_provider:
            providers["script"] = job.script_provider
        if job.image_provider:
            providers["image"] = job.image_provider
        if job.voice_provider:
            providers["voice"] = job.voice_provider
        if job.head_provider:
            providers["head"] = job.head_provider

        cost = estimate_cost(providers)
        job.estimated_cost = cost
        db.commit()

        tmp = TempJobDir(job_id)
        tmp_path = tmp.create()

        try:
            await _run_stages(db, job, providers, tmp_path)
        except Exception as exc:
            logger.exception("UGC pipeline failed for job %d", job_id)
            job.status = UGCJobStatus.FAILED
            job.error_message = str(exc)
            _update_job(db, job, UGCJobStatus.FAILED, "failed", job.progress, f"ERROR: {exc}")
        finally:
            tmp.cleanup()

    finally:
        db.close()


async def _run_stages(db, job: UGCJob, providers: dict[str, str], tmp_path: Path) -> None:
    from persona_system.persona_engine.loader import load_persona
    from app.services.video_renderer import generate_thumbnail
    from app.services.subtitle_generator import generate_ass_subtitles

    persona = load_persona(job.persona_name)

    # ── Stage 1: Generate script ──────────────────────────────────────────────
    _update_job(db, job, UGCJobStatus.GENERATING_SCRIPT, "generate_script", 0.10,
                f"Generating UGC script via {providers['script']}")
    script: UGCScript = await generate_ugc_script(
        persona=persona,
        topic=job.topic or "personal growth",
        style=job.style,
        platform=job.platform,
        duration_target=job.duration_target,
        provider=providers["script"],
    )
    job.script_raw = script.raw
    job.script_formatted = script.raw  # formatted = cleaned in voice_service
    db.commit()
    _update_job(db, job, UGCJobStatus.GENERATING_SCRIPT, "generate_script", 0.20,
                f"Script ready: {script.word_count} words")

    # ── Stage 2: Generate scene images ────────────────────────────────────────
    _update_job(db, job, UGCJobStatus.GENERATING_IMAGES, "generate_images", 0.25,
                f"Generating scene images via {providers['image']}")
    images_dir = tmp_path / "images"
    scene_images = await generate_scene_images(
        persona=persona,
        style=job.style,
        platform=job.platform,
        output_dir=images_dir,
        count=4,
        provider=providers["image"],
    )
    job.scene_images = json.dumps([str(p) for p in scene_images])
    db.commit()
    _update_job(db, job, UGCJobStatus.GENERATING_IMAGES, "generate_images", 0.35,
                f"Generated {len(scene_images)} scene images")

    # ── Stage 3: Generate voice ───────────────────────────────────────────────
    _update_job(db, job, UGCJobStatus.GENERATING_VOICE, "generate_voice", 0.40,
                f"Generating voice via {providers['voice']}")
    audio_path = await generate_ugc_voice(
        script_raw=script.raw,
        output_dir=tmp_path / "audio",
        provider=providers["voice"],
    )
    job.voice_file = str(audio_path)
    db.commit()
    _update_job(db, job, UGCJobStatus.GENERATING_VOICE, "generate_voice", 0.50,
                f"Voice ready: {audio_path.name}")

    # ── Stage 4: APPROVAL CHECKPOINT ─────────────────────────────────────────
    _update_job(db, job, UGCJobStatus.AWAITING_APPROVAL, "awaiting_approval", 0.52,
                "Waiting for checkpoint approval")
    db.commit()

    decision = await run_checkpoint(
        job_id=job.id,
        script_formatted=script.raw,
        first_image=scene_images[0] if scene_images else None,
        cost_estimate=job.estimated_cost,
        providers=providers,
    )

    # Store the decision for audit trail
    job.approval_decision = decision
    db.commit()

    if decision == "regenerate_script":
        _update_job(db, job, UGCJobStatus.GENERATING_SCRIPT, "regenerate_script", 0.10,
                    "Regenerating script per user request")
        script = await generate_ugc_script(
            persona=persona, topic=job.topic or "personal growth",
            style=job.style, platform=job.platform,
            duration_target=job.duration_target, provider=providers["script"],
        )
        job.script_raw = script.raw
        job.script_formatted = script.raw
        db.commit()
        # Regenerate voice with new script
        audio_path = await generate_ugc_voice(
            script_raw=script.raw, output_dir=tmp_path / "audio",
            provider=providers["voice"],
        )
        job.voice_file = str(audio_path)
        db.commit()

    elif decision == "regenerate_image":
        _update_job(db, job, UGCJobStatus.GENERATING_IMAGES, "regenerate_image", 0.25,
                    "Regenerating scene images per user request")
        scene_images = await generate_scene_images(
            persona=persona, style=job.style, platform=job.platform,
            output_dir=images_dir, count=4, provider=providers["image"],
        )
        job.scene_images = json.dumps([str(p) for p in scene_images])
        db.commit()

    # ── Stage 5: Talking head (optional) ─────────────────────────────────────
    _update_job(db, job, UGCJobStatus.RENDERING_HEAD, "render_head", 0.57,
                f"Generating talking head via {providers['head']}")

    portrait_path: Path | None = None
    if persona.get("reference_image"):
        portrait_path = Path(persona["reference_image"])
    elif scene_images:
        portrait_path = scene_images[0]

    talking_head_clip: Path | None = None
    if portrait_path and portrait_path.exists():
        talking_head_clip = await generate_talking_head(
            portrait_path=portrait_path,
            audio_path=audio_path,
            output_dir=tmp_path / "head",
            provider=providers["head"],
        )

    if talking_head_clip:
        job.talking_head_file = str(talking_head_clip)
        db.commit()
        _update_job(db, job, UGCJobStatus.RENDERING_HEAD, "render_head", 0.65,
                    "Talking head ready")
    else:
        _update_job(db, job, UGCJobStatus.RENDERING_HEAD, "render_head", 0.65,
                    "Using slideshow mode (no talking head)")

    # ── Stage 6: Generate subtitles ───────────────────────────────────────────
    subtitle_file: Path | None = None
    _update_job(db, job, UGCJobStatus.GENERATING_SUBTITLES, "generate_subtitles", 0.70,
                "Generating subtitles via Whisper")
    try:
        subtitle_path = tmp_path / f"subs_{job.id}.ass"
        subtitle_file = await generate_ass_subtitles(audio_path, subtitle_path)
        job.subtitle_file = str(subtitle_file)
        db.commit()
        _update_job(db, job, UGCJobStatus.GENERATING_SUBTITLES, "generate_subtitles", 0.78,
                    "Subtitles ready")
    except Exception as exc:
        logger.warning("Subtitle generation failed (non-fatal): %s", exc)
        _update_job(db, job, UGCJobStatus.GENERATING_SUBTITLES, "generate_subtitles", 0.78,
                    f"Subtitles failed (skipped): {exc}")

    # ── Stage 7: Assemble final video ─────────────────────────────────────────
    _update_job(db, job, UGCJobStatus.ASSEMBLING, "assemble", 0.80,
                "Assembling final video with FFmpeg")

    out_filename = f"ugc_{job.id}_{uuid.uuid4().hex[:8]}.mp4"
    output_path = settings.ugc_output_dir / out_filename
    settings.ugc_output_dir.mkdir(parents=True, exist_ok=True)

    await assemble_ugc_video(
        scene_images=scene_images,
        audio_path=audio_path,
        output_path=output_path,
        subtitle_file=subtitle_file,
        talking_head_clip=talking_head_clip,
        platform=job.platform,
    )
    job.output_file = str(output_path)
    db.commit()
    _update_job(db, job, UGCJobStatus.ASSEMBLING, "assemble", 0.93,
                f"Video assembled: {out_filename}")

    # ── Stage 8: Generate thumbnail ───────────────────────────────────────────
    thumb_path = settings.ugc_output_dir / f"thumb_{job.id}_{uuid.uuid4().hex[:6]}.jpg"
    try:
        await generate_thumbnail(output_path, thumb_path)
        job.thumbnail_file = str(thumb_path)
        db.commit()
    except Exception as exc:
        logger.warning("Thumbnail generation failed: %s", exc)

    _update_job(db, job, UGCJobStatus.COMPLETE, "complete", 1.0,
                f"UGC video complete → {out_filename}")
    logger.info("UGC job %d complete: %s", job.id, output_path)


# ── Batch processing ──────────────────────────────────────────────────────────

async def run_ugc_batch(job_ids: list[int]) -> dict[int, str]:
    """Run multiple UGC jobs sequentially (M4 thermal management — no GPU parallelism).

    Returns mapping of job_id → 'complete' | 'failed'.
    """
    results: dict[int, str] = {}
    for job_id in job_ids:
        logger.info("Starting UGC batch job %d/%d (id=%d)", job_ids.index(job_id) + 1, len(job_ids), job_id)
        try:
            await run_ugc_pipeline(job_id)
            results[job_id] = "complete"
        except Exception as exc:
            logger.error("Batch job %d failed: %s", job_id, exc)
            results[job_id] = "failed"
    return results
