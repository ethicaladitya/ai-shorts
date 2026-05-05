"""Avatar video pipeline: script → voice → talking head → captions.

Supports two avatar providers controlled by AVATAR_PROVIDER in .env:
  did       — D-ID API (free plan adds watermark; paid plan removes it)
  replicate — SadTalker on Replicate (~$0.07/video; $5 free credits; no watermark)
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AvatarJob, AvatarJobStatus
from app.services.ai_provider import get_ai_provider
from app.services.voice_provider import get_voice_provider
from app.services.subtitle_generator import generate_subtitles
from app.services import d_id_provider as did
from app.services import replicate_avatar_provider as replicate_avatar
from app.services.video_renderer import FFMPEG

logger = logging.getLogger(__name__)

_SCRIPT_SYSTEM = (
    "You are a script writer for short-form talking head videos. "
    "Write scripts that sound completely natural when spoken aloud. "
    "Be conversational, energetic, and direct. "
    "No hashtags, no stage directions, no markdown — just the spoken words."
)


def _update_job(
    db: Session, job: AvatarJob, step: str, progress: float, log_line: str = ""
):
    job.step = step
    job.progress = progress
    if log_line:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        job.log = (job.log or "") + f"[{ts}] {log_line}\n"
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


async def _generate_script(topic: str, notes: str) -> str:
    """Single-shot LLM call: produce a 45-60 second talking head script."""
    provider = get_ai_provider(
        provider_name=settings.ai_provider,
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        deployment=settings.azure_openai_deployment,
        api_version=settings.azure_openai_api_version,
        openai_api_key=settings.openai_api_key,
    )
    prompt = (
        f'Write a 45-60 second talking head video script on this topic: "{topic}"\n\n'
        f"Additional notes: {notes or 'None'}\n\n"
        "Requirements:\n"
        "- Strong hook in the first 5 seconds\n"
        "- Conversational, first-person tone\n"
        "- One clear insight or actionable tip\n"
        "- End with a punchy call to action\n"
        "- 120-150 words max\n"
        "- No stage directions, no [PAUSE], no formatting — just the words to speak"
    )
    return await provider.generate(
        prompt, system_prompt=_SCRIPT_SYSTEM, temperature=0.8, max_tokens=300
    )


async def _overlay_captions(
    video_path: Path, subtitle_path: Path, output_path: Path
) -> Path:
    """FFmpeg: burn SRT captions onto the face video (preserves original audio)."""
    style = (
        "FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H80000000,"
        "Outline=2,Bold=1,Alignment=2,MarginV=30"
    )
    # Escape path for FFmpeg filter-graph (colons are option separators)
    p = str(subtitle_path.resolve()).replace("\\", "\\\\").replace(":", "\\:")
    ext = subtitle_path.suffix.lower()
    if ext == ".ass":
        vf = f"ass=filename={p}"
    else:
        vf = f"subtitles=filename={p}:force_style='{style}'"

    async def _run(vf_str: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-y",
            "-i", str(video_path),
            "-vf", vf_str,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        return proc.returncode, err.decode()

    rc, stderr = await _run(vf)

    # Fallback: if subtitle filter unavailable (no libass), skip captions
    _sub_errors = ("No such filter", "No option name near", "libass")
    if rc != 0 and any(e in stderr for e in _sub_errors):
        logger.warning("Caption overlay: subtitle filter unavailable, copying video without captions.")
        import shutil
        shutil.copy2(str(video_path), str(output_path))
        return output_path

    if rc != 0:
        raise RuntimeError(f"Caption overlay failed: {stderr[-1500:]}")
    return output_path


async def run_avatar_pipeline(job_id: int, db: Session):
    """Run the full avatar video pipeline for an AvatarJob."""
    job = db.query(AvatarJob).filter(AvatarJob.id == job_id).first()
    if not job:
        raise ValueError(f"AvatarJob {job_id} not found")

    provider = (settings.avatar_provider or "did").lower()

    # Validate credentials for chosen provider
    if provider == "replicate" and not settings.replicate_api_key:
        job.status = AvatarJobStatus.FAILED
        job.error_message = (
            "Replicate API key not configured. "
            "Add REPLICATE_API_KEY=r8_xxx to your .env and set AVATAR_PROVIDER=replicate."
        )
        db.commit()
        return
    if provider == "did" and not settings.did_api_key:
        job.status = AvatarJobStatus.FAILED
        job.error_message = (
            "D-ID API key not configured. "
            "Add DID_API_KEY=username:password to your .env and redeploy. "
            "Or switch to AVATAR_PROVIDER=replicate (free tier, no watermark)."
        )
        db.commit()
        return

    uid = str(uuid.uuid4())[:8]
    job.status = AvatarJobStatus.GENERATING_SCRIPT
    db.commit()

    try:
        # ── Step 1: Generate script ──────────────────────────────────────────
        _update_job(db, job, "generate_script", 0.05, "Writing talking head script...")
        script = await _generate_script(job.topic, job.custom_notes or "")
        job.script = script
        db.commit()
        _update_job(db, job, "generate_script", 0.15, "Script ready")

        # ── Step 2: Generate voice audio ─────────────────────────────────────
        _update_job(db, job, "generate_voice", 0.20, "Generating voice audio...")
        job.status = AvatarJobStatus.GENERATING_VOICE
        db.commit()
        voice_path = settings.output_dir / f"avatar_voice_{job.id}_{uid}.mp3"
        vp = get_voice_provider(
            provider_name=settings.voice_provider,
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            tts_endpoint=settings.azure_openai_tts_endpoint,
            tts_api_key=settings.azure_openai_tts_api_key,
            tts_api_version=settings.azure_openai_tts_api_version,
            tts_deployment=settings.azure_openai_tts_deployment,
            tts_voice=settings.azure_openai_tts_voice,
            openai_api_key=settings.openai_api_key,
            elevenlabs_api_key=settings.elevenlabs_api_key,
            elevenlabs_voice_id=settings.elevenlabs_voice_id,
            mai_voice_endpoint=settings.mai_voice_endpoint,
            mai_voice_api_key=settings.mai_voice_api_key,
            mai_voice_name=settings.mai_voice_name,
            azure_speech_region=settings.azure_speech_region,
            azure_speech_api_key=settings.azure_speech_api_key or settings.azure_openai_api_key,
            azure_speech_voice=settings.azure_speech_voice,
            voicebox_url=settings.voicebox_url,
            voicebox_profile_id=settings.voicebox_profile_id,
            voicebox_engine=settings.voicebox_engine,
            kokoro_model_dir=settings.kokoro_model_dir,
            kokoro_voice=settings.kokoro_voice,
        )
        await vp.generate_speech(script, voice_path)
        job.voice_file = str(voice_path)
        db.commit()
        _update_job(db, job, "generate_voice", 0.35, "Voice audio ready")

        # ── Step 3: Animate face (provider-specific) ──────────────────────────
        raw_video = settings.output_dir / f"avatar_raw_{job.id}_{uid}.mp4"

        if provider == "replicate":
            _update_job(db, job, "animating", 0.40, "Submitting to Replicate SadTalker (no watermark)...")
            job.status = AvatarJobStatus.ANIMATING
            db.commit()
            await replicate_avatar.generate_avatar_video(
                image_path=Path(job.face_image_path),
                audio_path=voice_path,
                output_path=raw_video,
                api_key=settings.replicate_api_key,
                model_version=settings.replicate_sadtalker_version,
            )
            _update_job(db, job, "animating", 0.75, "SadTalker render complete")

        else:  # did
            _update_job(db, job, "animating", 0.40, "Uploading photo and audio to D-ID...")
            job.status = AvatarJobStatus.ANIMATING
            db.commit()
            image_url = await did.upload_image(Path(job.face_image_path), settings.did_api_key)
            audio_url = await did.upload_audio(voice_path, settings.did_api_key)
            _update_job(db, job, "animating", 0.50, "Uploaded. Queuing animation render...")

            talk_id = await did.create_talk(image_url, audio_url, settings.did_api_key)
            job.did_talk_id = talk_id
            db.commit()
            _update_job(db, job, "animating", 0.55, f"D-ID job {talk_id} — rendering face video...")

            result_url = await did.wait_for_talk(talk_id, settings.did_api_key)
            _update_job(db, job, "downloading", 0.70, "Render done! Downloading video...")
            await did.download_video(result_url, raw_video)
            _update_job(db, job, "downloading", 0.75, "Downloaded")

        # ── Step 4: Generate SRT subtitles from voice audio ───────────────────
        _update_job(db, job, "adding_captions", 0.82, "Transcribing and adding captions...")
        job.status = AvatarJobStatus.ADDING_CAPTIONS
        db.commit()
        subtitle_path = settings.output_dir / f"avatar_subs_{job.id}_{uid}.srt"
        await generate_subtitles(voice_path, subtitle_path)

        # ── Step 5: Burn captions onto face video ─────────────────────────────
        output_path = settings.output_dir / f"avatar_{job.id}_{uid}.mp4"
        await _overlay_captions(raw_video, subtitle_path, output_path)

        # Clean up intermediates
        raw_video.unlink(missing_ok=True)
        subtitle_path.unlink(missing_ok=True)

        job.output_file = str(output_path)
        job.status = AvatarJobStatus.COMPLETE
        _update_job(db, job, "complete", 1.0, "Avatar video complete!")

    except Exception as e:
        logger.exception(f"Avatar pipeline failed for job {job_id}")
        job.status = AvatarJobStatus.FAILED
        job.error_message = str(e)
        _update_job(db, job, "failed", job.progress, f"ERROR: {e}")
        db.commit()
