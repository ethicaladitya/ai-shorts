"""
persona_system/video_engine_adapter/adapter.py

Unified video creation interface. Supports 4 modes:
  loop             — static image + audio loop (cheapest, instant)
  animated_loop    — animated clip (SVD/Kling) + audio, no lipsync
  talking          — static image lipsync (LatentSync/Hedra)
  animated_talking — animated clip + lipsync (BEST — default)

Fallback chain when local AI services are unavailable:
  animated_talking → static zoom loop + lipsync attempt → static zoom loop
  animated_loop    → static zoom loop
  talking          → static loop
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Literal

import httpx

from persona_system.config.settings import settings

logger = logging.getLogger(__name__)

VideoMode = Literal["loop", "animated_loop", "talking", "animated_talking", "scene_talking"]
FFMPEG = "ffmpeg"


# ─────────────────────────────────────────────────────────────────────────────
# Audio pre-processing
# ─────────────────────────────────────────────────────────────────────────────
def optimize_audio(
    input_path: Path,
    output_path: Path,
    trim_silence: bool = True,
    speed_factor: float = 1.0,
) -> Path:
    """Trim silence, normalize loudness to -16 LUFS (ideal for Reels/TikTok)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filters = []
    if trim_silence:
        filters.append(
            "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB"
            ":stop_periods=-1:stop_duration=0.5:stop_threshold=-40dB"
        )
    if speed_factor != 1.0:
        filters.append(f"atempo={speed_factor}")
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    cmd = [
        FFMPEG, "-y", "-i", str(input_path),
        "-af", ",".join(filters),
        "-ar", "44100", "-ac", "1",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        logger.warning("Audio optimization warning: %s", result.stderr.decode()[-500:])
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Loop video (image + audio → looping video)
# ─────────────────────────────────────────────────────────────────────────────
async def generate_loop_video(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    add_zoom: bool = True,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Static image + audio → vertical video with optional Ken Burns zoom."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    duration = float(result.stdout.strip() or "30")

    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    if add_zoom:
        vf += (
            f",zoompan=z='min(zoom+0.0003,1.3)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={int(duration * 30)}:s={width}x{height}:fps=30"
        )

    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Loop video render failed: {stderr.decode()[-1000:]}")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Captions overlay
# ─────────────────────────────────────────────────────────────────────────────
async def overlay_captions(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
) -> Path:
    """Burn ASS/SRT subtitles onto video."""
    p = str(subtitle_path.resolve()).replace("\\", "\\\\").replace(":", "\\:")
    ext = subtitle_path.suffix.lower()
    vf = f"ass=filename={p}" if ext == ".ass" else (
        f"subtitles=filename={p}:force_style='"
        "FontName=Inter,FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H80000000,"
        "Outline=2,Bold=1,Alignment=2,MarginV=40'"
    )

    cmd = [
        FFMPEG, "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "copy", "-movflags", "+faststart",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode()
        if any(e in err for e in ("No such filter", "libass", "No option name near")):
            logger.warning("Subtitle filter unavailable — copying without captions")
            shutil.copy2(str(video_path), str(output_path))
            return output_path
        raise RuntimeError(f"Caption overlay failed: {err[-1500:]}")

    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Loop a short clip to match audio duration
# ─────────────────────────────────────────────────────────────────────────────
async def loop_clip_to_audio(clip_path: Path, audio_path: Path, output_path: Path) -> Path:
    """Seamlessly loop a short activity clip to match the full audio duration."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    audio_dur = float(result.stdout.strip() or "30")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y",
        "-stream_loop", "-1", "-i", str(clip_path),
        "-i", str(audio_path),
        "-t", str(audio_dur),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Clip loop failed: {stderr.decode()[-1000:]}")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Main adapter — 4-mode unified interface
# ─────────────────────────────────────────────────────────────────────────────
async def create_video(
    image_path: Path,
    audio_path: Path,
    script: str,
    output_path: Path,
    video_mode: VideoMode | None = None,
    persona_name: str = "default",
    pillar: str | None = None,
    persona: dict | None = None,
    subtitle_path: Path | None = None,
    video_clip_provider: str | None = None,
    lipsync_provider: str | None = None,
    optimize_audio_first: bool = True,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Create a video from image + audio. Falls back gracefully when AI services unavailable."""
    from persona_system.image_engine.generator import preprocess_for_video
    from persona_system.video_engine_adapter.animated_clip import generate_activity_clip
    from persona_system.video_engine_adapter.lipsync import apply_lipsync

    mode = video_mode or settings.default_video_mode
    tmp_dir = output_path.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:8]

    # ── Pre-process audio ─────────────────────────────────────────────────
    logger.info("[%s] Step 1/3: Optimizing audio (normalizing and trimming)...", uid)
    if optimize_audio_first:
        optimized_audio = tmp_dir / f"opt_audio_{uid}.mp3"
        optimize_audio(audio_path, optimized_audio)
        final_audio = optimized_audio
    else:
        final_audio = audio_path

    # ── Pre-process image ─────────────────────────────────────────────────
    logger.info("[%s] Step 2/3: Pre-processing image for video engine...", uid)
    processed_image = tmp_dir / f"proc_img_{uid}.jpg"
    preprocess_for_video(image_path, processed_image, width=width, height=height)

    try:
        raw = tmp_dir / f"raw_{uid}.mp4"

        if mode == "loop":
            # ── LOOP MODE ─────────────────────────────────────────────────
            logger.info("[%s] Mode: loop — static image + audio", uid)
            await generate_loop_video(processed_image, final_audio, raw, width=width, height=height)

        elif mode == "animated_loop":
            # ── ANIMATED LOOP MODE ────────────────────────────────────────
            logger.info("[%s] Mode: animated_loop", uid)
            clip = tmp_dir / f"clip_{uid}.mp4"
            try:
                logger.info("[%s] Generating activity motion clip (provider=%s)...", uid, video_clip_provider or "default")
                await generate_activity_clip(
                    processed_image, clip,
                    pillar=pillar, persona=persona,
                    provider=video_clip_provider,
                )
                await loop_clip_to_audio(clip, final_audio, raw)
                clip.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("[%s] Animated clip failed (%s) — falling back to static zoom loop", uid, e)
                clip.unlink(missing_ok=True)
                await generate_loop_video(processed_image, final_audio, raw, add_zoom=True, width=width, height=height)

        elif mode == "talking":
            # ── TALKING MODE (static image + lipsync) ─────────────────────
            logger.info("[%s] Mode: talking", uid)
            loop_base = tmp_dir / f"loop_base_{uid}.mp4"
            await generate_loop_video(processed_image, final_audio, loop_base, add_zoom=False, width=width, height=height)
            try:
                logger.info("[%s] Applying lipsync...", uid)
                await apply_lipsync(loop_base, final_audio, raw, provider=lipsync_provider)
            except Exception as e:
                logger.warning("[%s] Lipsync failed (%s) — using static loop", uid, e)
                shutil.copy2(str(loop_base), str(raw))
            loop_base.unlink(missing_ok=True)

        elif mode == "scene_talking":
            # ── SCENE TALKING MODE ────────────────────────────────────────
            # 1. Build activity scene slideshow (persona in different locations)
            # 2. Lipsync from portrait (Hedra) → talking head clip
            # 3. Composite: [scene intro 20%] → [talking head 60%] → [scene outro 20%]
            logger.info("[%s] Mode: scene_talking — building scenes + lipsync", uid)

            # Stage 1: generate activity scenes
            from persona_system.video_engine_adapter.scene_video import (
                generate_scene_images, create_scene_slideshow,
            )
            ref_img = Path(settings.persona_reference_image) if settings.persona_reference_image else image_path
            if not ref_img.exists():
                ref_img = image_path

            scenes_dir = tmp_dir / f"scenes_{uid}"
            try:
                scene_imgs = await generate_scene_images(
                    persona_image_path=ref_img,
                    output_dir=scenes_dir,
                    n_scenes=4,
                    pillar=pillar,
                )
            except Exception as e:
                logger.warning("[%s] Scene generation failed (%s) — using single image", uid, e)
                scene_imgs = [ref_img]

            # Stage 2: lipsync talking head from portrait
            from persona_system.video_engine_adapter.lipsync import hedra_from_portrait
            talking_clip = tmp_dir / f"talking_{uid}.mp4"
            try:
                portrait_for_lipsync = scene_imgs[0] if scene_imgs else ref_img
                await hedra_from_portrait(portrait_for_lipsync, final_audio, talking_clip)
                logger.info("[%s] Talking head ready via Hedra", uid)
                raw = talking_clip
            except Exception as e:
                logger.warning("[%s] Hedra lipsync failed (%s) — using scene slideshow only", uid, e)
                try:
                    await create_scene_slideshow(
                        scene_images=scene_imgs,
                        audio_path=final_audio,
                        output_path=raw,
                        width=width,
                        height=height,
                    )
                except Exception as se2:
                    logger.warning("[%s] Scene slideshow also failed (%s) — static zoom", uid, se2)
                    await generate_loop_video(processed_image, final_audio, raw, add_zoom=True, width=width, height=height)

            # Cleanup scenes
            for s in scene_imgs:
                if s != ref_img:
                    s.unlink(missing_ok=True)

        else:
            # ── ANIMATED TALKING MODE (best quality + fallback chain) ──────
            logger.info("[%s] Mode: animated_talking", uid)
            clip = tmp_dir / f"clip_{uid}.mp4"
            looped_clip = tmp_dir / f"looped_{uid}.mp4"

            # Stage 1: Try animated clip (SVD/Kling). Fall back to scene slideshow.
            logger.info("[%s] Step 1/3: Generating activity motion clip...", uid)
            try:
                await generate_activity_clip(
                    processed_image, clip,
                    pillar=pillar, persona=persona,
                    provider=video_clip_provider,
                )
                logger.info("[%s] Step 2/3: Looping motion clip to match audio length...", uid)
                await loop_clip_to_audio(clip, final_audio, looped_clip)
                clip.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("[%s] Animated clip unavailable (%s) — building scene slideshow", uid, e)
                clip.unlink(missing_ok=True)
                # Use gpt-image-2 edits to generate same persona in 4 different scenes
                try:
                    from persona_system.video_engine_adapter.scene_video import (
                        generate_scene_images, create_scene_slideshow,
                    )
                    # Prefer the configured reference image; fall back to the per-piece image
                    ref_img = Path(settings.persona_reference_image) if settings.persona_reference_image else image_path
                    if not ref_img.exists():
                        ref_img = image_path
                    scenes_dir = tmp_dir / f"scenes_{uid}"
                    scene_imgs = await generate_scene_images(
                        persona_image_path=ref_img,
                        output_dir=scenes_dir,
                        n_scenes=4,
                        pillar=pillar,
                    )
                    await create_scene_slideshow(
                        scene_images=scene_imgs,
                        audio_path=final_audio,
                        output_path=looped_clip,
                        width=width,
                        height=height,
                    )
                    # Clean up scene images
                    for s in scene_imgs:
                        s.unlink(missing_ok=True)
                    logger.info("[%s] Scene slideshow ready", uid)
                except Exception as se:
                    logger.warning("[%s] Scene slideshow failed (%s) — falling back to static zoom", uid, se)
                    await generate_loop_video(
                        processed_image, final_audio, looped_clip,
                        add_zoom=True, width=width, height=height,
                    )

            # Stage 2: Lipsync → fallback to looped clip directly
            logger.info("[%s] Step 3/3: Applying lipsync...", uid)
            try:
                await apply_lipsync(looped_clip, final_audio, raw, provider=lipsync_provider)
            except Exception as e:
                logger.warning("[%s] Lipsync failed (%s) — serving video without lipsync", uid, e)
                shutil.copy2(str(looped_clip), str(raw))
            looped_clip.unlink(missing_ok=True)

        # ── Overlay captions ──────────────────────────────────────────────
        if subtitle_path and subtitle_path.exists():
            await overlay_captions(raw, subtitle_path, output_path)
            raw.unlink(missing_ok=True)
        else:
            shutil.move(str(raw), str(output_path))

    finally:
        processed_image.unlink(missing_ok=True)
        if optimize_audio_first:
            final_audio.unlink(missing_ok=True)

    logger.info("[%s] Video complete → %s", uid, output_path.name)
    return output_path
