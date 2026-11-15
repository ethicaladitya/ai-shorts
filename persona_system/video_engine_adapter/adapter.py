"""
persona_system/video_engine_adapter/adapter.py
Wraps the existing ai-shorts video system (pipeline + avatar pipeline).
Supports CLI and Python usage. Accepts image + audio + script → final video.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Literal

import httpx

from persona_system.config.settings import settings

logger = logging.getLogger(__name__)

VideoType = Literal["talking", "loop"]


# ─────────────────────────────────────────────────────────────────────────────
# Audio pre-processing
# ─────────────────────────────────────────────────────────────────────────────
def optimize_audio(
    input_path: Path,
    output_path: Path,
    target_duration: float | None = None,
    trim_silence: bool = True,
    speed_factor: float = 1.0,
) -> Path:
    """
    Trim silence, normalize, and optionally adjust speed.
    Uses ffmpeg (available on M1/M2 Mac via brew).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filters = []

    if trim_silence:
        # silenceremove: trim leading/trailing silence > 0.5s below -40dB
        filters.append("silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB"
                        ":stop_periods=-1:stop_duration=0.5:stop_threshold=-40dB")

    if speed_factor != 1.0:
        # atempo only supports 0.5–2.0; chain for values outside range
        filters.append(f"atempo={speed_factor}")

    # Normalize loudness to -16 LUFS (ideal for Reels/TikTok)
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    vf = ",".join(filters) if filters else "anull"
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-af", vf,
        "-ar", "44100", "-ac", "1",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        logger.warning("Audio optimization partial failure: %s", result.stderr.decode()[-500:])
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Loop video (image + audio → looping video)
# ─────────────────────────────────────────────────────────────────────────────
async def generate_loop_video(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    add_zoom: bool = True,
) -> Path:
    """
    Create a simple loop video: static/ken-burns image + audio.
    Extremely cheap — runs locally in seconds.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get audio duration
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True
    )
    duration = float(result.stdout.strip() or "30")

    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    if add_zoom:
        # Ken Burns effect: slow zoom in
        vf += f",zoompan=z='min(zoom+0.0003,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*30)}:s=1080x1920:fps=30"

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Loop video render failed: {stderr.decode()[-1000:]}")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Talking video via existing ai-shorts AvatarJob API
# ─────────────────────────────────────────────────────────────────────────────
async def generate_talking_video(
    image_path: Path,
    audio_path: Path,
    script: str,
    output_path: Path,
    persona_name: str = "default",
) -> Path:
    """
    Trigger the existing ai-shorts avatar pipeline via its REST API.
    The ai-shorts system handles D-ID / Replicate / SadTalker.
    Downloads the final video to output_path when complete.
    """
    base = settings.ai_shorts_api_url.rstrip("/")
    headers = {}
    if settings.ai_shorts_api_key:
        headers["Authorization"] = f"Bearer {settings.ai_shorts_api_key}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Upload image
        with image_path.open("rb") as f:
            img_resp = await client.post(
                f"{base}/api/avatar/upload-image",
                files={"file": (image_path.name, f, "image/jpeg")},
                headers=headers,
            )
        img_resp.raise_for_status()
        image_url_or_path = img_resp.json().get("path") or img_resp.json().get("url", "")

        # 2. Create AvatarJob
        job_resp = await client.post(
            f"{base}/api/avatar/jobs",
            json={
                "topic": persona_name,
                "custom_notes": script[:500],
                "face_image_path": image_url_or_path,
                "voice_file": str(audio_path),
            },
            headers=headers,
        )
        job_resp.raise_for_status()
        job_id = job_resp.json()["id"]

    # 3. Poll until complete (max 10 min)
    logger.info("Waiting for ai-shorts avatar job %d...", job_id)
    for _ in range(120):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=15.0) as client:
            status_resp = await client.get(f"{base}/api/avatar/jobs/{job_id}", headers=headers)
            status_resp.raise_for_status()
            job = status_resp.json()

        status = job.get("status", "")
        if status == "complete":
            # 4. Download output
            output_file = job.get("output_file", "")
            if output_file:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    dl = await client.get(f"{base}/media/{Path(output_file).name}", headers=headers)
                    dl.raise_for_status()
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(dl.content)
                logger.info("Talking video downloaded → %s", output_path)
                return output_path
            break
        elif status == "failed":
            raise RuntimeError(f"Avatar job {job_id} failed: {job.get('error_message', 'unknown')}")

    raise TimeoutError(f"Avatar job {job_id} did not complete in time")


# ─────────────────────────────────────────────────────────────────────────────
# Main adapter — unified interface
# ─────────────────────────────────────────────────────────────────────────────
async def create_video(
    image_path: Path,
    audio_path: Path,
    script: str,
    output_path: Path,
    video_type: VideoType = "talking",
    persona_name: str = "default",
    optimize_audio_first: bool = True,
    variants: int = 1,
) -> list[Path]:
    """
    Unified video creation with automatic fallback.
    Returns list of output paths (one per variant).
    """
    from persona_system.image_engine.generator import preprocess_for_video

    results: list[Path] = []

    # Pre-process image for video input
    processed_image = output_path.parent / f"preprocessed_{uuid.uuid4().hex[:6]}.jpg"
    preprocess_for_video(image_path, processed_image)

    # Pre-process audio
    if optimize_audio_first:
        optimized_audio = output_path.parent / f"optimized_{uuid.uuid4().hex[:6]}.mp3"
        optimize_audio(audio_path, optimized_audio)
        final_audio = optimized_audio
    else:
        final_audio = audio_path

    for v in range(variants):
        variant_path = output_path.parent / f"{output_path.stem}_v{v+1}{output_path.suffix}"

        if video_type == "talking":
            try:
                result = await generate_talking_video(
                    processed_image, final_audio, script, variant_path, persona_name
                )
                results.append(result)
                continue
            except Exception as e:
                logger.warning("Talking video failed (%s) — falling back to loop video", e)

        # Loop video (primary or fallback)
        result = await generate_loop_video(processed_image, final_audio, variant_path)
        results.append(result)

    # Cleanup temp files
    processed_image.unlink(missing_ok=True)
    if optimize_audio_first:
        optimized_audio.unlink(missing_ok=True)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────
def cli():
    """
    Usage:
      python -m persona_system.video_engine_adapter.adapter \\
        --image /path/to/face.jpg \\
        --audio /path/to/voice.mp3 \\
        --script "your script here" \\
        --output /path/to/output.mp4 \\
        --type talking \\
        --variants 2
    """
    import argparse

    parser = argparse.ArgumentParser(description="Video Engine Adapter CLI")
    parser.add_argument("--image",    required=True)
    parser.add_argument("--audio",    required=True)
    parser.add_argument("--script",   required=True)
    parser.add_argument("--output",   required=True)
    parser.add_argument("--type",     choices=["talking", "loop"], default="talking")
    parser.add_argument("--persona",  default="default")
    parser.add_argument("--variants", type=int, default=1)
    args = parser.parse_args()

    outputs = asyncio.run(create_video(
        image_path=Path(args.image),
        audio_path=Path(args.audio),
        script=args.script,
        output_path=Path(args.output),
        video_type=args.type,
        persona_name=args.persona,
        variants=args.variants,
    ))
    for o in outputs:
        print(f"OUTPUT: {o}")


if __name__ == "__main__":
    cli()
