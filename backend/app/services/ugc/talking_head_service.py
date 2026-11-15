"""Talking head stage — local SadTalker CLI first, slideshow fallback."""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from app.config import settings
from app.services.ugc.utils.retry import async_retry

logger = logging.getLogger(__name__)

# Character consistency note:
# When SadTalker is active: the input portrait is reused for every clip, so
# character consistency is guaranteed by the source image itself (strongest method).
# When slideshow mode is used: consistency comes from using the same generated
# scene images with matching trigger_word + seed in the image generator.


def _find_sadtalker_cli() -> str | None:
    """Locate the SadTalker CLI binary."""
    path = settings.ugc_sadtalker_cli_path
    if path and Path(path).exists():
        return path
    # Try common install locations
    for candidate in ("/opt/homebrew/bin/sadtalker", "/usr/local/bin/sadtalker"):
        if Path(candidate).exists():
            return candidate
    return shutil.which("sadtalker")


@async_retry(max_attempts=2, base_delay=5.0)
async def generate_talking_head(
    portrait_path: Path,
    audio_path: Path,
    output_dir: Path,
    provider: str = "sadtalker",
) -> Path | None:
    """Generate a talking head video clip.

    Returns path to the output MP4, or None if slideshow fallback was used
    (caller handles the None case by using images + audio directly).
    """
    if provider == "slideshow":
        logger.info("Talking head disabled (provider=slideshow) — using image slideshow")
        return None

    if provider in ("replicate", "did"):
        return await _cloud_talking_head(portrait_path, audio_path, output_dir, provider)

    if provider in ("replicate_latentsync", "latentsync", "wav2lip", "syncso"):
        logger.info("Generating continuous HeyGen-style talking head video via lipsync: %s", provider)
        output_dir.mkdir(parents=True, exist_ok=True)
        from app.services.video_renderer import get_audio_duration
        duration = await get_audio_duration(audio_path)

        silent_video = output_dir / f"silent_{uuid.uuid4().hex[:8]}.mp4"
        await _still_to_video(portrait_path, silent_video, duration)

        from persona_system.video_engine_adapter.lipsync import apply_lipsync
        out_path = output_dir / f"talking_head_{uuid.uuid4().hex[:8]}.mp4"
        try:
            await apply_lipsync(silent_video, audio_path, out_path, provider=provider)
            return out_path
        except Exception as exc:
            logger.error("Lipsync talking head generation failed: %s", exc)
            return None
        finally:
            silent_video.unlink(missing_ok=True)

    # Local SadTalker
    cli = _find_sadtalker_cli()
    if not cli:
        logger.warning(
            "SadTalker CLI not found (set SADTALKER_CLI_PATH or install sadtalker). "
            "Falling back to slideshow mode."
        )
        return None

    return await _run_sadtalker(cli, portrait_path, audio_path, output_dir)


async def _run_sadtalker(
    cli_path: str,
    portrait_path: Path,
    audio_path: Path,
    output_dir: Path,
) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        cli_path,
        "--driven_audio", str(audio_path),
        "--source_image", str(portrait_path),
        "--result_dir", str(output_dir),
        "--preprocess", "full",
        "--enhancer", "gfpgan",
    ]
    logger.info("Running SadTalker: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("SadTalker failed (rc=%d): %s", proc.returncode, stderr.decode()[:500])
        return None

    mp4_files = sorted(output_dir.glob("*.mp4"))
    if not mp4_files:
        logger.warning("SadTalker completed but no MP4 found in %s", output_dir)
        return None

    return mp4_files[-1]


async def _cloud_talking_head(
    portrait_path: Path,
    audio_path: Path,
    output_dir: Path,
    provider: str,
) -> Path | None:
    """D-ID or Replicate SadTalker as paid fallback."""
    try:
        if provider == "replicate":
            from app.services.replicate_avatar_provider import generate_avatar_video
            out_path = output_dir / f"talking_head_{uuid.uuid4().hex[:8]}.mp4"
            await generate_avatar_video(
                image_path=portrait_path,
                audio_path=audio_path,
                output_path=out_path,
                api_key=settings.replicate_api_key,
                model_version=settings.replicate_sadtalker_version,
            )
            return out_path

        elif provider == "did":
            from app.services.d_id_provider import upload_image, upload_audio, create_talk, wait_for_talk, download_video
            image_url = await upload_image(portrait_path, settings.did_api_key)
            audio_url = await upload_audio(audio_path, settings.did_api_key)
            talk_id = await create_talk(image_url, audio_url, settings.did_api_key)
            result_url = await wait_for_talk(talk_id, settings.did_api_key)
            out_path = output_dir / f"talking_head_{uuid.uuid4().hex[:8]}.mp4"
            await download_video(result_url, out_path)
            return out_path

    except Exception as exc:
        logger.error("Cloud talking head (%s) failed: %s — falling back to slideshow", provider, exc)

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
