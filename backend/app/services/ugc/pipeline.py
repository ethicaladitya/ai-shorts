"""UGC video pipeline orchestrator — scene-planned, fast-cut, motion-animated."""
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
from app.services.cost_tracker import record_cost, estimate_cost as _est_cost
from app.services.ugc.audio_cutter import map_shots_to_audio
from app.services.ugc.checkpoint_ui import run_checkpoint
from app.services.ugc.config.defaults import PLATFORM_PRESETS, estimate_cost, resolve_providers
from app.services.ugc.image_generator import generate_shot_images, generate_scene_images
from app.services.ugc.motion_engine import animate_all_shots
from app.services.ugc.scene_planner import plan_shots
from app.services.ugc.script_generator import UGCScript, generate_ugc_script
from app.services.ugc.talking_head_service import generate_talking_head
from app.services.ugc.utils.job_state import write_state
from app.services.ugc.utils.temp_files import TempJobDir
from app.services.ugc.video_assembler import assemble_from_clips, assemble_ugc_video
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
    db = SessionLocal()
    try:
        job = db.query(UGCJob).filter(UGCJob.id == job_id).first()
        if not job:
            logger.error("UGC job %d not found", job_id)
            return

        providers = resolve_providers(settings)
        if job.script_provider:   providers["script"] = job.script_provider
        if job.image_provider:    providers["image"]  = job.image_provider
        if job.voice_provider:    providers["voice"]  = job.voice_provider
        if job.head_provider:     providers["head"]   = job.head_provider

        job.estimated_cost = estimate_cost(providers)
        db.commit()

        tmp = TempJobDir(job_id)
        tmp_path = tmp.create()

        try:
            await _run_stages(db, job, providers, tmp_path)
            tmp.cleanup()  # Successful completion -> clean up temp files
        except Exception as exc:
            logger.exception("UGC pipeline failed for job %d", job_id)
            _update_job(db, job, UGCJobStatus.FAILED, "failed", job.progress, f"ERROR: {exc}")
    finally:
        db.close()


async def _run_stages(db, job: UGCJob, providers: dict, tmp_path: Path) -> None:
    from persona_system.persona_engine.loader import load_persona
    from app.services.video_renderer import generate_thumbnail
    from app.services.subtitle_generator import generate_ass_subtitles

    persona = load_persona(job.persona_name)
    persona_seed = persona.get("image_gen", {}).get("seed", 0)

    # Determine lipsync/talking head strategies early (so we can optimize Stage 5)
    post_assembly_lipsync = _post_assembly_lipsync_provider(providers)  # syncso/replicate/wav2lip
    single_scene_head = _should_use_talking_head(providers)             # did/sadtalker/replicate/etc.

    # ── Stage 1: Generate script ──────────────────────────────────────────────
    if job.script_raw:
        _update_job(db, job, job.status, "generate_script", 0.12,
                    "Reusing existing generated script")
        script = UGCScript.from_raw(job.script_raw)
    else:
        _update_job(db, job, UGCJobStatus.GENERATING_SCRIPT, "generate_script", 0.05,
                    f"Generating script via {providers['script']}")
        script: UGCScript = await generate_ugc_script(
            persona=persona, topic=job.topic or "personal growth",
            style=job.style, platform=job.platform,
            duration_target=job.duration_target, provider=providers["script"],
        )
        job.script_raw = script.raw
        job.script_formatted = script.raw
        db.commit()
        record_cost(providers["script"], "generate_script",
                    _est_cost(providers["script"], units=script.word_count * 1.3, unit_type="tokens"),
                    persona_name=job.persona_name, job_type="ugc", job_id=job.id,
                    units=script.word_count * 1.3, unit_label="tokens", db=db)
        _update_job(db, job, UGCJobStatus.GENERATING_SCRIPT, "generate_script", 0.12,
                    f"Script: {script.word_count} words")

    # ── Stage 2: Plan shots ───────────────────────────────────────────────────
    _update_job(db, job, job.status, "plan_shots", 0.14,
                "Planning shot sequence")
    shots = plan_shots(
        script_raw=script.raw,
        persona=persona,
        duration_target=job.duration_target,
        style=job.style,
        seed=persona_seed,
    )
    # Score retention and log suggestions (non-blocking)
    from app.services.ugc.retention_optimizer import score_retention, log_retention_score
    rscore = score_retention(script, shots, job.duration_target)
    log_retention_score(rscore, job.id)
    retention_note = f"hook={rscore.hook_score:.2f}, pacing={rscore.pacing_avg_secs:.1f}s"
    if rscore.suggestions:
        retention_note += " | " + "; ".join(rscore.suggestions[:2])

    _update_job(db, job, job.status, "plan_shots", 0.16,
                f"Planned {len(shots)} shots | {retention_note}")

    # ── Stage 3: Generate shot images ─────────────────────────────────────────
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    images_exist = False
    scene_images = []
    if job.scene_images:
        try:
            loaded_paths = json.loads(job.scene_images)
            if loaded_paths and isinstance(loaded_paths, list):
                scene_images = [Path(p) for p in loaded_paths]
                images_exist = all(p.exists() for p in scene_images)
        except Exception:
            pass

    if images_exist:
        _update_job(db, job, job.status, "generate_images", 0.32,
                    f"Reusing {len(scene_images)} existing scene images")
    else:
        _update_job(db, job, UGCJobStatus.GENERATING_IMAGES, "generate_images", 0.18,
                    f"Generating {len(shots)} shot images via {providers['image']}")
        try:
            scene_images = await generate_shot_images(
                persona=persona, shots=shots,
                output_dir=images_dir, provider=providers["image"],
            )
        except Exception as exc:
            logger.warning("Shot-aware image gen failed (%s) — falling back to generic scenes", exc)
            scene_images = await generate_scene_images(
                persona=persona, style=job.style, platform=job.platform,
                output_dir=images_dir, count=len(shots), provider=providers["image"],
            )

        job.scene_images = json.dumps([str(p) for p in scene_images])
        db.commit()
        record_cost(providers["image"], "generate_images",
                    _est_cost(providers["image"], units=len(scene_images), unit_type="image"),
                    persona_name=job.persona_name, job_type="ugc", job_id=job.id,
                    units=len(scene_images), unit_label="image", db=db)
        _update_job(db, job, UGCJobStatus.GENERATING_IMAGES, "generate_images", 0.32,
                    f"Generated {len(scene_images)} images")

    # ── Stage 4: Generate voice ───────────────────────────────────────────────
    voice_exists = False
    audio_path = None
    if job.voice_file:
        audio_path = Path(job.voice_file)
        if audio_path.exists():
            voice_exists = True

    if voice_exists:
        _update_job(db, job, job.status, "generate_voice", 0.43,
                    f"Reusing existing voice file: {audio_path.name}")
    else:
        _update_job(db, job, UGCJobStatus.GENERATING_VOICE, "generate_voice", 0.35,
                    f"Generating voice via {providers['voice']}")
        audio_path = await generate_ugc_voice(
            script_raw=script.raw, output_dir=tmp_path / "audio",
            provider=providers["voice"],
        )
        job.voice_file = str(audio_path)
        db.commit()
        record_cost(providers["voice"], "generate_voice",
                    _est_cost(providers["voice"], units=len(script.raw), unit_type="chars"),
                    persona_name=job.persona_name, job_type="ugc", job_id=job.id,
                    units=len(script.raw), unit_label="chars", db=db)
        _update_job(db, job, UGCJobStatus.GENERATING_VOICE, "generate_voice", 0.43,
                    f"Voice ready: {audio_path.name}")

    # ── Stage 5: Animate shots (image → video clips) ──────────────────────────
    motion_provider = _pick_motion_provider(providers)
    clips_dir = tmp_path / "clips"
    active_shots = shots[:len(scene_images)]

    animated_clips = []
    has_motion = False

    if single_scene_head:
        _update_job(db, job, job.status, "animate_shots", 0.55,
                    "Continuous talking head enabled — bypassing shot animation stage")
    else:
        existing_clips = sorted(list(clips_dir.glob("shot_*.mp4"))) if clips_dir.exists() else []
        if existing_clips and len(existing_clips) == len(active_shots):
            animated_clips = existing_clips
            has_motion = True
            _update_job(db, job, job.status, "animate_shots", 0.55,
                        f"Reusing {len(animated_clips)} existing animated clips")
        else:
            _update_job(db, job, UGCJobStatus.GENERATING_VOICE, "animate_shots", 0.46,
                        f"Animating {len(shots)} shots via {motion_provider}")
            try:
                animated_clips = await animate_all_shots(
                    image_paths=scene_images,
                    shots=active_shots,
                    output_dir=clips_dir,
                    provider=motion_provider,
                    concurrency=2,
                )
                has_motion = True
            except Exception as exc:
                logger.warning("Motion animation failed (%s) — will use slideshow", exc)
                animated_clips = []
                has_motion = False

            _update_job(db, job, job.status, "animate_shots", 0.55,
                        f"Animation: {'done' if has_motion else 'skipped (slideshow fallback)'}")

    # ── Stage 6: APPROVAL CHECKPOINT ─────────────────────────────────────────
    decision = job.approval_decision
    if decision in ("approve", "regenerate_script", "regenerate_image"):
        _update_job(db, job, job.status, "awaiting_approval", 0.57,
                    f"Reusing approval decision: {decision}")
    else:
        _update_job(db, job, UGCJobStatus.AWAITING_APPROVAL, "awaiting_approval", 0.57,
                    "Waiting for checkpoint approval")
        db.commit()

        decision = await run_checkpoint(
            job_id=job.id, script_formatted=script.raw,
            first_image=scene_images[0] if scene_images else None,
            cost_estimate=job.estimated_cost, providers=providers,
        )
        job.approval_decision = decision
        db.commit()

    if decision == "regenerate_script":
        _update_job(db, job, UGCJobStatus.GENERATING_SCRIPT, "regenerate_script", 0.05,
                    "Regenerating script")
        script = await generate_ugc_script(
            persona=persona, topic=job.topic or "personal growth",
            style=job.style, platform=job.platform,
            duration_target=job.duration_target, provider=providers["script"],
        )
        job.script_raw = script.raw
        job.script_formatted = script.raw
        job.voice_file = None  # Reset voice file so it gets regenerated
        job.approval_decision = "approve"  # Reset decision to approved
        db.commit()
        audio_path = await generate_ugc_voice(
            script_raw=script.raw, output_dir=tmp_path / "audio",
            provider=providers["voice"],
        )
        job.voice_file = str(audio_path)
        db.commit()

    elif decision == "regenerate_image":
        _update_job(db, job, UGCJobStatus.GENERATING_IMAGES, "regenerate_image", 0.18,
                    "Regenerating images")
        scene_images = await generate_shot_images(
            persona=persona, shots=active_shots,
            output_dir=images_dir, provider=providers["image"],
        )
        job.scene_images = json.dumps([str(p) for p in scene_images])
        job.approval_decision = "approve"  # Reset decision to approved
        # Reset animated clips directory
        import shutil
        if clips_dir.exists():
            shutil.rmtree(clips_dir, ignore_errors=True)
        db.commit()
        if has_motion:
            animated_clips = await animate_all_shots(
                image_paths=scene_images, shots=active_shots,
                output_dir=clips_dir, provider=motion_provider, concurrency=2,
            )

    # ── Stage 7: Talking head (D-ID / SadTalker / LatentSync) ──────────────────
    talking_head_clip = None
    if job.talking_head_file:
        talking_head_clip = Path(job.talking_head_file)
        if talking_head_clip.exists():
            _update_job(db, job, job.status, "talking_head", 0.66,
                        "Reusing existing talking head clip")

    if not talking_head_clip or not talking_head_clip.exists():
        if single_scene_head:
            portrait = _get_portrait(persona, scene_images)
            if portrait and portrait.exists():
                _update_job(db, job, UGCJobStatus.RENDERING_HEAD, "talking_head", 0.60,
                            f"Talking head via {head_provider}")
                try:
                    from app.services.ugc.talking_head_service import generate_talking_head
                    talking_head_clip = await generate_talking_head(
                        portrait_path=portrait, audio_path=audio_path,
                        output_dir=tmp_path / "head", provider=head_provider,
                    )
                    if talking_head_clip:
                        job.talking_head_file = str(talking_head_clip)
                        db.commit()
                        record_cost(head_provider, "talking_head",
                                    _est_cost(head_provider, units=1, unit_type="video"),
                                    persona_name=job.persona_name, job_type="ugc", job_id=job.id,
                                    units=1, unit_label="video", db=db)
                        _update_job(db, job, UGCJobStatus.RENDERING_HEAD, "talking_head", 0.66,
                                    "Talking head ready")
                except Exception as exc:
                    logger.warning("Talking head failed (%s) — using multi-scene clips", exc)
        elif post_assembly_lipsync:
            _update_job(db, job, UGCJobStatus.RENDERING_HEAD, "motion_clips", 0.66,
                        f"Multi-scene clips ready; lipsync ({post_assembly_lipsync}) applied at assembly")
        else:
            _update_job(db, job, UGCJobStatus.RENDERING_HEAD, "motion_clips", 0.66,
                        f"Using animated clips ({motion_provider}) — no lipsync")

    # ── Stage 8: Audio-driven cut mapping ────────────────────────────────────
    _update_job(db, job, UGCJobStatus.GENERATING_SUBTITLES, "audio_cut", 0.68,
                "Mapping audio-driven cut points")
    timed_shots = map_shots_to_audio(active_shots, audio_path)
    _update_job(db, job, UGCJobStatus.GENERATING_SUBTITLES, "audio_cut", 0.70,
                f"Cut map: {[f'{t.duration:.1f}s' for t in timed_shots]}")

    # ── Stage 9: Subtitles ────────────────────────────────────────────────────
    subtitle_file = None
    if job.subtitle_file:
        subtitle_file = Path(job.subtitle_file)
        if subtitle_file.exists():
            _update_job(db, job, job.status, "subtitles", 0.76,
                        "Reusing existing subtitle file")

    if not subtitle_file or not subtitle_file.exists():
        try:
            subtitle_path = tmp_path / f"subs_{job.id}.ass"
            subtitle_file = await generate_ass_subtitles(audio_path, subtitle_path)
            job.subtitle_file = str(subtitle_file)
            db.commit()
            _update_job(db, job, UGCJobStatus.GENERATING_SUBTITLES, "subtitles", 0.76,
                        "Subtitles ready")
        except Exception as exc:
            logger.warning("Subtitles failed (non-fatal): %s", exc)

    # ── Stage 10: Assemble final video ────────────────────────────────────────
    output_path = None
    if job.output_file:
        output_path = Path(job.output_file)
        if output_path.exists():
            _update_job(db, job, job.status, "assemble", 0.93,
                        f"Reusing existing final video: {output_path.name}")

    if not output_path or not output_path.exists():
        _update_job(db, job, UGCJobStatus.ASSEMBLING, "assemble", 0.78,
                    "Assembling final video")

        out_filename = f"ugc_{job.id}_{uuid.uuid4().hex[:8]}.mp4"
        output_path = settings.ugc_output_dir / out_filename
        settings.ugc_output_dir.mkdir(parents=True, exist_ok=True)

        if talking_head_clip and talking_head_clip.exists():
            # D-ID / SadTalker single talking head (single scene, best quality lipsync)
            await assemble_ugc_video(
                scene_images=scene_images, audio_path=audio_path,
                output_path=output_path, subtitle_file=subtitle_file,
                talking_head_clip=talking_head_clip, platform=job.platform, seed=persona_seed,
            )
        elif has_motion and animated_clips and len(animated_clips) == len(timed_shots):
            # Multi-scene fast-cut mode (Ken-Burns per shot) + optional post-assembly lipsync
            _update_job(db, job, UGCJobStatus.ASSEMBLING, "assemble", 0.80,
                        f"Assembling {len(animated_clips)} clips"
                        + (f" + {post_assembly_lipsync} lipsync" if post_assembly_lipsync else ""))
            await assemble_from_clips(
                timed_shots=timed_shots, clips=animated_clips,
                audio_path=audio_path, output_path=output_path,
                subtitle_file=subtitle_file, platform=job.platform, seed=persona_seed,
                lipsync_provider=post_assembly_lipsync,  # None = no lipsync
            )
        else:
            # Slideshow fallback
            await assemble_ugc_video(
                scene_images=scene_images, audio_path=audio_path,
                output_path=output_path, subtitle_file=subtitle_file,
                talking_head_clip=None, platform=job.platform, seed=persona_seed,
            )

        job.output_file = str(output_path)
        db.commit()
        _update_job(db, job, UGCJobStatus.ASSEMBLING, "assemble", 0.93,
                    f"Video assembled: {out_filename}")

    # ── Stage 11: Thumbnail ───────────────────────────────────────────────────
    thumb_exists = False
    if job.thumbnail_file:
        if Path(job.thumbnail_file).exists():
            thumb_exists = True

    if not thumb_exists:
        try:
            thumb_path = settings.ugc_output_dir / f"thumb_{job.id}_{uuid.uuid4().hex[:6]}.jpg"
            await generate_thumbnail(output_path, thumb_path)
            job.thumbnail_file = str(thumb_path)
            db.commit()
        except Exception as exc:
            logger.warning("Thumbnail failed: %s", exc)

    _update_job(db, job, UGCJobStatus.COMPLETE, "complete", 1.0,
                f"Complete → {output_path.name}")
    logger.info("UGC job %d complete: %s", job.id, output_path)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pick_motion_provider(providers: dict) -> str:
    """
    Pick the animation provider for per-shot clips.
    head provider maps to motion engine provider:
      kenburns    → auto (LivePortrait → SVD → Ken-Burns, all free)
      liveportrait→ liveportrait
      sadtalker   → auto (Ken-Burns for individual shots; SadTalker used for talking head separately)
      kling       → kling (paid, only if key configured)
      latentsync/hedra/syncso → auto (lipsync handled separately, use Ken-Burns for motion)
    """
    head = providers.get("head", "kenburns")
    if head == "kling" and settings.kling_api_key and settings.kling_api_secret:
        return "kling"
    if head == "liveportrait":
        return "liveportrait"
    return "auto"  # LivePortrait → SVD → Ken-Burns, all free


def _post_assembly_lipsync_provider(providers: dict) -> str | None:
    """
    Return the lipsync provider to apply POST-ASSEMBLY (on the full assembled video).
    If the provider is already used as the head_provider (generating a continuous
    talking head), we return None here to avoid running it twice.
    """
    return None


def _should_use_talking_head(providers: dict) -> bool:
    """True if the head provider generates a full single-scene talking head (D-ID, SadTalker, LatentSync, etc.)."""
    return providers.get("head", "") in (
        "sadtalker", "did", "replicate", "replicate_latentsync", "latentsync", "wav2lip", "syncso"
    )


def _get_portrait(persona: dict, scene_images: list[Path]) -> Path | None:
    """Get best portrait for lipsync: persona reference image → first scene image."""
    ref = persona.get("reference_image", "")
    if ref:
        p = Path(ref)
        if p.exists():
            return p
    if scene_images:
        return scene_images[0]
    return None


async def _still_to_video(image_path: Path, output_path: Path, duration: float) -> None:
    """Convert a still image to a silent video (for lipsync input)."""
    from app.services.video_renderer import FFMPEG
    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-i", str(image_path),
        "-t", str(duration),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=25",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-an", str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"still_to_video failed: {stderr.decode()[:300]}")


# ── Batch ─────────────────────────────────────────────────────────────────────

async def run_ugc_batch(job_ids: list[int]) -> dict[int, str]:
    results: dict[int, str] = {}
    for job_id in job_ids:
        logger.info("Batch job %d/%d (id=%d)", job_ids.index(job_id) + 1, len(job_ids), job_id)
        try:
            await run_ugc_pipeline(job_id)
            results[job_id] = "complete"
        except Exception as exc:
            logger.error("Batch job %d failed: %s", job_id, exc)
            results[job_id] = "failed"
    return results
