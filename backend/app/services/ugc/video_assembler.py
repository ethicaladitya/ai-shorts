"""UGC video assembler — FFmpeg composition with UGC post-processing pass."""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from app.config import settings
from app.services.ugc.config.defaults import PLATFORM_PRESETS
from app.services.ugc.realism.ffmpeg_filters import full_ugc_video_filter, speed_filter
from app.services.video_renderer import FFMPEG, FFPROBE, get_audio_duration

logger = logging.getLogger(__name__)


async def assemble_ugc_video(
    scene_images: list[Path],
    audio_path: Path,
    output_path: Path,
    subtitle_file: Path | None,
    talking_head_clip: Path | None,
    platform: str = "tiktok",
) -> Path:
    """Compose the final UGC video.

    Pipeline:
    1. If talking_head_clip: use it as the video source
    2. Otherwise: create slideshow from scene_images timed to audio duration
    3. Apply UGC post-processing (grain, shake, vignette, saturation)
    4. Mux with audio and burn subtitles
    """
    preset = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["tiktok"])
    width, height, fps = preset["width"], preset["height"], preset["fps"]

    audio_duration = await get_audio_duration(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Base video (talking head OR slideshow)
    base_video = output_path.parent / f"_base_{uuid.uuid4().hex[:8]}.mp4"

    if talking_head_clip and talking_head_clip.exists():
        await _scale_video(talking_head_clip, base_video, width, height, fps)
    else:
        await _build_slideshow(scene_images, audio_duration, base_video, width, height, fps)

    # Step 2: UGC post-processing pass
    processed_video = output_path.parent / f"_processed_{uuid.uuid4().hex[:8]}.mp4"
    await _apply_ugc_filters(base_video, processed_video, width, height)
    base_video.unlink(missing_ok=True)

    # Step 3: Mux audio + optional subtitles → final output
    await _mux_final(processed_video, audio_path, subtitle_file, output_path, audio_duration, width, height)
    processed_video.unlink(missing_ok=True)

    logger.info("UGC video assembled → %s", output_path.name)
    return output_path


async def _scale_video(src: Path, dst: Path, width: int, height: int, fps: int) -> None:
    cmd = [
        FFMPEG, "-y", "-i", str(src),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}"
        ),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-an", str(dst),
    ]
    await _run_cmd(cmd, "scale video")


async def _build_slideshow(
    images: list[Path],
    duration: float,
    output: Path,
    width: int,
    height: int,
    fps: int,
) -> None:
    """Ken-Burns style slideshow — each image held for equal duration."""
    if not images:
        raise ValueError("No scene images for slideshow")

    per_image = duration / len(images)
    # Build concat input using image loop
    inputs: list[str] = []
    for img in images:
        inputs += ["-loop", "1", "-t", f"{per_image:.3f}", "-i", str(img)]

    filter_parts = []
    for i in range(len(images)):
        # Ken-Burns: slight zoom in over the duration
        zoom_speed = 0.0003
        filter_parts.append(
            f"[{i}:v]scale={width * 2}:{height * 2},"
            f"zoompan=z='min(zoom+{zoom_speed},1.08)':d={int(per_image * fps)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={width}x{height}:fps={fps},"
            f"setsar=1[v{i}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(len(images)))
    filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={len(images)}:v=1:a=0[out]"

    cmd = [FFMPEG, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-an", str(output),
    ]
    await _run_cmd(cmd, "build slideshow")


async def _apply_ugc_filters(src: Path, dst: Path, width: int, height: int) -> None:
    """Apply grain, vignette, saturation, and subtle shake."""
    vf = full_ugc_video_filter(width, height)
    cmd = [
        FFMPEG, "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-an", str(dst),
    ]
    rc, stderr = await _run_cmd_capture(cmd)
    if rc != 0:
        # Grain/geq can fail on some FFmpeg builds — fall back to just saturation + vignette
        logger.warning("UGC filter chain failed (%s) — retrying with simplified filters", stderr[:200])
        simple_vf = "eq=saturation=1.08,vignette=angle=PI/5:mode=backward"
        cmd[-4] = simple_vf
        await _run_cmd(cmd, "apply simplified UGC filters")


async def _mux_final(
    video: Path,
    audio: Path,
    subtitle_file: Path | None,
    output: Path,
    duration: float,
    width: int,
    height: int,
) -> None:
    cmd = [FFMPEG, "-y", "-i", str(video), "-i", str(audio)]

    vf_parts: list[str] = []
    if subtitle_file and subtitle_file.exists():
        escaped = str(subtitle_file).replace("\\", "\\\\").replace(":", "\\:")
        ext = subtitle_file.suffix.lower()
        if ext == ".ass":
            vf_parts.append(f"ass={escaped}")
        else:
            vf_parts.append(f"subtitles={escaped}")

    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]

    cmd += [
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration),
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        str(output),
    ]
    rc, stderr = await _run_cmd_capture(cmd)
    if rc != 0 and subtitle_file:
        # Subtitle filter failed — retry without subs
        logger.warning("Subtitle mux failed — retrying without subtitles")
        cmd_nosubs = [FFMPEG, "-y", "-i", str(video), "-i", str(audio),
                      "-map", "0:v", "-map", "1:a",
                      "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                      "-c:a", "aac", "-b:a", "192k",
                      "-t", str(duration),
                      "-movflags", "+faststart",
                      "-pix_fmt", "yuv420p",
                      str(output)]
        await _run_cmd(cmd_nosubs, "mux final (no subs)")


async def _run_cmd(cmd: list[str], step: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg {step} failed (rc={proc.returncode}): {stderr.decode()[:500]}")


async def _run_cmd_capture(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode, stderr.decode()
