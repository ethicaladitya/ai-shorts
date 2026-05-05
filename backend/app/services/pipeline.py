"""Full video pipeline orchestrator."""
import asyncio
import logging
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Video, VideoStatus, RenderJob, RenderJobStatus
from app.services.content_fetcher import fetch_wordpress_content
from app.services.script_generator import generate_hooks, generate_script, format_script
from app.services.voice_provider import get_voice_provider
from app.services.subtitle_generator import generate_subtitles, generate_ass_subtitles
from app.services.video_renderer import render_video, generate_thumbnail
from app.services.video_search import fetch_stock_clips

logger = logging.getLogger(__name__)


def _clean_for_tts(text: str) -> str:
    """Strip all stage-direction markers so TTS only speaks real words."""
    # [SECTION LABELS] and [speed/tone markers]
    text = re.sub(r'\[[^\]]{0,60}\]', '', text)
    # (pause), (short pause), (dramatic), etc.
    text = re.sub(r'\([^)]{0,60}\)', '', text)
    # {beat}, {{beat}}, etc.
    text = re.sub(r'\{+[^}]{0,60}\}+', '', text)
    # *emphasis* markers (keep the word)
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
    # Timestamp markers like [00:05]
    text = re.sub(r'\d+:\d+', '', text)
    # Collapse extra whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def _update_job(db: Session, job: RenderJob, step: str, progress: float, log_line: str = ""):
    job.step = step
    job.progress = progress
    if log_line:
        job.log = (job.log or "") + f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {log_line}\n"
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


async def run_pipeline(video_id: int, db: Session):
    """Run the full video generation pipeline for a video."""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise ValueError(f"Video {video_id} not found")

    # Create render job
    job = RenderJob(video_id=video_id, status=RenderJobStatus.PROCESSING)
    db.add(job)
    db.commit()

    uid = str(uuid.uuid4())[:8]

    try:
        # Step 1: Fetch content if WordPress URL provided
        _update_job(db, job, "fetch_content", 0.05, "Fetching content...")
        if video.wordpress_url:
            try:
                data = await fetch_wordpress_content(video.wordpress_url)
                video.source_content = data.get("content", "")
                if not video.title or video.title == "Untitled":
                    video.title = data.get("title", video.title)
                db.commit()
                _update_job(db, job, "fetch_content", 0.1, f"Fetched: {data.get('title', 'content')}")
            except Exception as e:
                _update_job(db, job, "fetch_content", 0.1, f"Warning: Could not fetch WP content: {e}")

        content = video.source_content or video.custom_notes or video.topic

        # Step 2: Generate hooks
        _update_job(db, job, "generate_hooks", 0.15, "Generating hooks...")
        hooks = await generate_hooks(video.topic, content, video.custom_notes or "", db)
        video.hooks = hooks
        video.status = VideoStatus.HOOKS_GENERATED

        # Auto-select first hook if none selected
        if not video.selected_hook and hooks:
            video.selected_hook = hooks[0].get("text", "") if isinstance(hooks[0], dict) else str(hooks[0])
        db.commit()
        _update_job(db, job, "generate_hooks", 0.25, f"Generated {len(hooks)} hooks")

        # Step 3: Generate script
        _update_job(db, job, "generate_script", 0.3, "Generating script...")
        script = await generate_script(video.selected_hook, video.topic, content, video.custom_notes or "", db)
        video.script_raw = script
        db.commit()
        _update_job(db, job, "generate_script", 0.4, "Script generated")

        # Step 4: Format script
        _update_job(db, job, "format_script", 0.45, "Formatting script...")
        formatted = await format_script(script)
        video.script_formatted = formatted
        video.status = VideoStatus.SCRIPT_READY
        db.commit()
        _update_job(db, job, "format_script", 0.5, "Script formatted")

        # Step 5: Generate voice
        _update_job(db, job, "generate_voice", 0.55, "Generating voice...")
        video.status = VideoStatus.VOICE_GENERATING

        voice_path = settings.output_dir / f"voice_{video.id}_{uid}.mp3"
        # Clean all stage-direction markers before passing to TTS
        voice_text = _clean_for_tts(formatted or script)

        try:
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
            await vp.generate_speech(voice_text, voice_path)
            video.voice_file = str(voice_path)
            video.status = VideoStatus.VOICE_READY
            db.commit()
            _update_job(db, job, "generate_voice", 0.65, "Voice generated")
        except Exception as e:
            _update_job(db, job, "generate_voice", 0.65, f"Voice generation failed: {e}. Continuing without voice.")
            video.status = VideoStatus.SCRIPT_READY
            db.commit()
            job.status = RenderJobStatus.COMPLETE
            _update_job(db, job, "complete", 0.65, "Pipeline stopped - no voice provider configured")
            return

        # Step 5.5: Fetch stock video clips
        stock_clips: list[Path] = []
        if settings.pexels_api_key:
            _update_job(db, job, "fetch_clips", 0.66, "Fetching stock video clips...")
            try:
                clip_dir = settings.output_dir / "clips"
                clip_dir.mkdir(exist_ok=True)
                stock_clips = await fetch_stock_clips(
                    video.topic, voice_text, 60.0, settings.pexels_api_key, clip_dir
                )
                _update_job(db, job, "fetch_clips", 0.68, f"Downloaded {len(stock_clips)} stock clips")
            except Exception as e:
                _update_job(db, job, "fetch_clips", 0.68, f"Stock clip warning: {e}")

        # Step 6: Generate subtitles (TikTok-style ASS captions)
        _update_job(db, job, "generate_subtitles", 0.7, "Generating subtitles...")
        video.status = VideoStatus.SUBTITLING
        subtitle_path = settings.output_dir / f"subs_{video.id}_{uid}.ass"
        try:
            await generate_ass_subtitles(voice_path, subtitle_path)
            video.subtitle_file = str(subtitle_path)
            db.commit()
            _update_job(db, job, "generate_subtitles", 0.8, "Subtitles generated")
        except Exception as e:
            _update_job(db, job, "generate_subtitles", 0.8, f"Subtitle warning: {e}")
            subtitle_path = None

        # Step 7: Render video
        _update_job(db, job, "render_video", 0.85, "Rendering video...")
        video.status = VideoStatus.RENDERING
        output_path = settings.output_dir / f"video_{video.id}_{uid}.mp4"
        await render_video(
            voice_path, subtitle_path, output_path,
            background_clips=stock_clips if stock_clips else None,
        )
        video.output_file = str(output_path)
        db.commit()
        _update_job(db, job, "render_video", 0.95, "Video rendered")

        # Step 8: Thumbnail
        thumb_path = settings.output_dir / f"thumb_{video.id}_{uid}.jpg"
        try:
            await generate_thumbnail(output_path, thumb_path)
            video.thumbnail_file = str(thumb_path)
        except Exception:
            pass

        # Done
        video.status = VideoStatus.COMPLETE
        job.status = RenderJobStatus.COMPLETE
        _update_job(db, job, "complete", 1.0, "Pipeline complete!")
        db.commit()

        # Step 9: Optional n8n trigger
        if settings.n8n_webhook_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(settings.n8n_webhook_url, json={
                        "video_id": video.id,
                        "title": video.title,
                        "status": "complete",
                        "output_file": video.output_file,
                    })
                _update_job(db, job, "complete", 1.0, "n8n webhook triggered")
            except Exception as e:
                _update_job(db, job, "complete", 1.0, f"n8n webhook failed: {e}")

    except Exception as e:
        logger.exception(f"Pipeline failed for video {video_id}")
        video.status = VideoStatus.FAILED
        video.error_message = str(e)
        job.status = RenderJobStatus.FAILED
        job.error_message = str(e)
        _update_job(db, job, "failed", job.progress, f"ERROR: {e}")
        db.commit()
