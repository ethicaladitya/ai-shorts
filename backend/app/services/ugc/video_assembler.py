"""UGC video assembler — fast-cut editor with audio-driven timing.

Two assembly modes:
  1. assemble_from_clips(timed_shots, clips, ...) — PREFERRED
     Takes pre-animated clips (from motion_engine) + TimedShot timing objects.
     Trims each clip to its audio-driven duration, concatenates, muxes audio.
     This is what makes the video feel like a real TikTok edit.

  2. assemble_ugc_video(scene_images, ...) — LEGACY / FALLBACK
     Takes static images → builds Ken-Burns slideshow (old pipeline).
     Kept for backward compatibility and for pipelines without Kling.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import settings
from app.services.ugc.config.defaults import PLATFORM_PRESETS
from app.services.ugc.realism.ffmpeg_filters import (
    full_ugc_video_filter,
    ken_burns_params,
    loudnorm_filter,
)
from app.services.video_renderer import FFMPEG, FFPROBE, get_audio_duration

if TYPE_CHECKING:
    from app.services.ugc.audio_cutter import TimedShot

logger = logging.getLogger(__name__)

# Intermediate clips: CRF=18 veryfast keeps quality high through multiple encode passes.
# Final output: CRF=23 slow → ~4–6 Mbps at 1080×1920, ~15–25 MB for 22–30s (ideal for TikTok/IG).
_LOSSLESS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
_FINAL_CRF = ["-c:v", "libx264", "-preset", "slow", "-crf", "23", "-pix_fmt", "yuv420p"]


# ── MODE 1: Fast-cut from animated clips (preferred) ─────────────────────────

async def assemble_from_clips(
    timed_shots: list["TimedShot"],
    clips: list[Path],          # one clip per timed_shot (from motion_engine)
    audio_path: Path,
    output_path: Path,
    subtitle_file: Path | None,
    platform: str = "tiktok",
    seed: int = 0,
    lipsync_provider: str | None = None,  # "syncso" | "replicate_latentsync" | "wav2lip" | None
) -> Path:
    """
    Fast-cut assembly: trim each animated clip to its audio-driven duration,
    concatenate, apply UGC post-processing, mux audio + subtitles.

    Each clip is trimmed to the exact duration from the audio cutter,
    so cuts happen at natural speech pauses — just like a real editor would.
    """
    preset = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["tiktok"])
    width, height, fps = preset["width"], preset["height"], preset["fps"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.parent

    # Step 1: Trim + scale each clip to its target duration
    trimmed: list[Path] = []
    for ts, clip in zip(timed_shots, clips):
        if not clip.exists():
            logger.warning("Clip %d missing — skipping", ts.shot.index)
            continue
        out = tmp / f"_trim_{ts.shot.index:02d}_{uuid.uuid4().hex[:4]}.mp4"
        await _trim_clip(clip, out, ts.duration, width, height, fps)
        trimmed.append(out)

    if not trimmed:
        raise RuntimeError("No clips survived trimming — cannot assemble")

    # Step 2: Concatenate trimmed clips (lossless)
    concat_video = tmp / f"_concat_{uuid.uuid4().hex[:6]}.mp4"
    await _concat_clips(trimmed, concat_video)
    for t in trimmed:
        t.unlink(missing_ok=True)

    # Step 3: UGC post-processing (grain, shake, CA, vignette)
    processed = tmp / f"_processed_{uuid.uuid4().hex[:6]}.mp4"
    await _apply_ugc_filters(concat_video, processed, width, height, seed)
    concat_video.unlink(missing_ok=True)

    # Step 3.5: Lipsync (optional) — applied before subtitle burn so text doesn't block mouth
    if lipsync_provider:
        lipsync_vid = tmp / f"_lipsync_{uuid.uuid4().hex[:6]}.mp4"
        try:
            from persona_system.video_engine_adapter.lipsync import apply_lipsync
            logger.info("Applying lipsync via %s on assembled video...", lipsync_provider)
            await apply_lipsync(processed, audio_path, lipsync_vid, provider=lipsync_provider)
            if lipsync_vid.exists() and lipsync_vid.stat().st_size > 0:
                processed.unlink(missing_ok=True)
                processed = lipsync_vid
                logger.info("Lipsync done → %s", lipsync_vid.name)
            else:
                logger.warning("Lipsync output empty — using processed video without lipsync")
                lipsync_vid.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Lipsync (%s) failed — continuing without: %s", lipsync_provider, exc)
            lipsync_vid.unlink(missing_ok=True)

    # Step 4: Normalise audio + final mux
    norm_audio = tmp / f"_audnorm_{uuid.uuid4().hex[:6]}.mp3"
    await _normalise_audio(audio_path, norm_audio)

    total_duration = sum(ts.duration for ts in timed_shots)
    await _mux_final(processed, norm_audio, subtitle_file, output_path, total_duration, width, height)
    processed.unlink(missing_ok=True)
    norm_audio.unlink(missing_ok=True)

    logger.info("Fast-cut UGC video assembled → %s (%d shots)", output_path.name, len(timed_shots))
    return output_path


async def _trim_clip(src: Path, dst: Path, duration: float, width: int, height: int, fps: int) -> None:
    """Trim clip to duration and scale/crop to target resolution.

    If the requested duration exceeds the clip's actual length, the last frame is
    freeze-extended via tpad so audio is never cut short (e.g. when a shot covers
    a long audio segment and the Ken-Burns clip is shorter).
    """
    from app.services.video_renderer import FFPROBE
    import asyncio as _asyncio

    # Probe actual clip duration
    probe_cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(src),
    ]
    proc = await _asyncio.create_subprocess_exec(
        *probe_cmd, stdout=_asyncio.subprocess.PIPE, stderr=_asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    try:
        clip_duration = float(out.decode().strip())
    except (ValueError, AttributeError):
        clip_duration = duration  # assume exact if probe fails

    if duration > clip_duration:
        # Freeze-extend the clip's last frame to fill the required duration
        freeze_extra = duration - clip_duration
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps},"
            f"tpad=stop=-1:stop_mode=clone"
        )
    else:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}"
        )

    cmd = [
        FFMPEG, "-y",
        "-i", str(src),
        "-t", f"{duration:.3f}",
        "-vf", vf,
    ] + _LOSSLESS + ["-an", str(dst)]
    await _run_cmd(cmd, f"trim clip {src.name}")


async def _concat_clips(clips: list[Path], output: Path) -> None:
    """Concatenate clips using FFmpeg concat demuxer (stream copy — no re-encode)."""
    if len(clips) == 1:
        import shutil
        shutil.copy2(str(clips[0]), str(output))
        return

    # Write a temporary concat list file
    list_file = output.parent / f"_concat_list_{uuid.uuid4().hex[:6]}.txt"
    try:
        list_file.write_text("\n".join(f"file '{c.resolve()}'" for c in clips))
        cmd = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            "-an", str(output),
        ]
        await _run_cmd(cmd, "concat clips (stream copy)")
    finally:
        list_file.unlink(missing_ok=True)


# ── MODE 2: Legacy slideshow assembler (kept for fallback) ────────────────────

async def assemble_ugc_video(
    scene_images: list[Path],
    audio_path: Path,
    output_path: Path,
    subtitle_file: Path | None,
    talking_head_clip: Path | None,
    platform: str = "tiktok",
    seed: int = 0,
) -> Path:
    """Legacy assembler: static images → Ken-Burns slideshow → post-process → mux."""
    preset = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["tiktok"])
    width, height, fps = preset["width"], preset["height"], preset["fps"]
    audio_duration = await get_audio_duration(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.parent

    base_video = tmp / f"_base_{uuid.uuid4().hex[:8]}.mp4"
    if talking_head_clip and talking_head_clip.exists():
        await _scale_video(talking_head_clip, base_video, width, height, fps)
    else:
        await _build_slideshow(scene_images, audio_duration, base_video, width, height, fps, seed)

    processed = tmp / f"_processed_{uuid.uuid4().hex[:8]}.mp4"
    await _apply_ugc_filters(base_video, processed, width, height, seed)
    base_video.unlink(missing_ok=True)

    norm_audio = tmp / f"_audnorm_{uuid.uuid4().hex[:6]}.mp3"
    await _normalise_audio(audio_path, norm_audio)

    await _mux_final(processed, norm_audio, subtitle_file, output_path, audio_duration, width, height)
    processed.unlink(missing_ok=True)
    norm_audio.unlink(missing_ok=True)

    logger.info("UGC video (slideshow) assembled → %s", output_path.name)
    return output_path


async def _scale_video(src: Path, dst: Path, width: int, height: int, fps: int) -> None:
    cmd = [
        FFMPEG, "-y", "-i", str(src),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}"
        ),
    ] + _LOSSLESS + ["-an", str(dst)]
    await _run_cmd(cmd, "scale video")


async def _build_slideshow(
    images: list[Path],
    duration: float,
    output: Path,
    width: int,
    height: int,
    fps: int,
    seed: int = 0,
) -> None:
    if not images:
        raise ValueError("No scene images for slideshow")

    per_image = duration / len(images)
    inputs: list[str] = []
    for img in images:
        inputs += ["-loop", "1", "-t", f"{per_image:.3f}", "-i", str(img)]

    filter_parts = []
    for i in range(len(images)):
        kb = ken_burns_params(i, seed)
        frames = int(per_image * fps)
        # force_original_aspect_ratio=increase + crop ensures 3:4 images fill 9:16 without distortion
        filter_parts.append(
            f"[{i}:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan="
            f"z='{kb['zoom_expr']}':"
            f"d={frames}:"
            f"x='{kb['x_expr']}':"
            f"y='{kb['y_expr']}':"
            f"s={width}x{height}:fps={fps},"
            f"setsar=1[v{i}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(len(images)))
    filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={len(images)}:v=1:a=0[out]"

    cmd = [FFMPEG, "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(duration),
    ] + _LOSSLESS + ["-an", str(output)]
    await _run_cmd(cmd, "build slideshow")


# ── Shared post-processing steps ──────────────────────────────────────────────

async def _apply_ugc_filters(src: Path, dst: Path, width: int, height: int, seed: int = 0) -> None:
    vf = full_ugc_video_filter(width, height, seed)
    cmd = [FFMPEG, "-y", "-i", str(src), "-vf", vf] + _LOSSLESS + ["-an", str(dst)]
    rc, stderr = await _run_cmd_capture(cmd)
    if rc != 0:
        logger.warning("UGC filter chain failed — retrying simplified: %s", stderr[:200])
        simple = "eq=saturation=1.06,noise=alls=8:allf=t+u,vignette=angle=0.62:mode=backward"
        await _run_cmd([FFMPEG, "-y", "-i", str(src), "-vf", simple] + _LOSSLESS + ["-an", str(dst)],
                       "simplified UGC filters")


async def _normalise_audio(src: Path, dst: Path) -> None:
    cmd = [
        FFMPEG, "-y", "-i", str(src),
        "-af", loudnorm_filter(),
        "-c:a", "libmp3lame", "-q:a", "2", str(dst),
    ]
    rc, stderr = await _run_cmd_capture(cmd)
    if rc != 0:
        import shutil
        logger.warning("loudnorm failed — using raw audio: %s", stderr[:200])
        shutil.copy2(str(src), str(dst))


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
        vf_parts.append(f"ass={escaped}" if subtitle_file.suffix.lower() == ".ass" else f"subtitles={escaped}")
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += [
        "-map", "0:v", "-map", "1:a",
    ] + _FINAL_CRF + [
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration),
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        str(output),
    ]
    rc, stderr = await _run_cmd_capture(cmd)
    if rc != 0 and subtitle_file:
        logger.warning("Subtitle mux failed — retrying without subs")
        cmd_ns = [FFMPEG, "-y", "-i", str(video), "-i", str(audio),
                  "-map", "0:v", "-map", "1:a",
                  ] + _FINAL_CRF + [
                  "-c:a", "aac", "-b:a", "192k",
                  "-t", str(duration),
                  "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(output)]
        await _run_cmd(cmd_ns, "mux (no subs)")
    elif rc != 0:
        raise RuntimeError(f"Final mux failed (rc={rc}): {stderr[:400]}")


async def _run_cmd(cmd: list[str], step: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg {step} failed (rc={proc.returncode}): {stderr.decode()[:500]}")


async def _run_cmd_capture(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    return proc.returncode, stderr.decode()
