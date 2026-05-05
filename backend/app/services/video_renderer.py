"""Video rendering with FFmpeg — stock footage, dark overlay, TikTok captions."""
import asyncio
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Prefer ffmpeg-full (includes libass) over the standard Homebrew bottle.
_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
_FFPROBE_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
FFMPEG = _FFMPEG_FULL if Path(_FFMPEG_FULL).exists() else (shutil.which("ffmpeg") or "ffmpeg")
FFPROBE = _FFPROBE_FULL if Path(_FFPROBE_FULL).exists() else (shutil.which("ffprobe") or "ffprobe")

TEMPLATES = {
    "vertical_9_16": {"width": 1080, "height": 1920, "fps": 30},
    "square_1_1":    {"width": 1080, "height": 1080, "fps": 30},
    "horizontal_16_9": {"width": 1920, "height": 1080, "fps": 30},
}


async def get_audio_duration(media_path: Path) -> float:
    """Return duration in seconds of any audio or video file."""
    cmd = [
        FFPROBE, "-v", "quiet", "-print_format", "json",
        "-show_format", str(media_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    data = json.loads(stdout)
    return float(data["format"]["duration"])


async def _concat_clips(
    clips: list[Path],
    duration: float,
    width: int,
    height: int,
    out_path: Path,
) -> bool:
    """
    Stitch clips (looping) to fill `duration` seconds, scaled/cropped to w×h.
    Returns True on success.
    """
    clip_durations = []
    for c in clips:
        try:
            clip_durations.append(await get_audio_duration(c))
        except Exception:
            clip_durations.append(8.0)

    total = sum(clip_durations) or 1.0
    repeat = max(1, int(duration / total) + 2)

    concat_file = out_path.parent / f"_concat_{out_path.stem}.txt"
    with open(concat_file, "w") as f:
        for _ in range(repeat):
            for clip in clips:
                f.write(f"file '{clip.resolve()}'\n")

    try:
        cmd = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-t", str(duration),
            "-vf", (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps=30"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "24",
            "-an",
            str(out_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"Clip concat failed: {stderr.decode()[:300]}")
            return False
        return True
    finally:
        concat_file.unlink(missing_ok=True)


def _subtitle_filter(subtitle_path: Path) -> str:
    """Build the FFmpeg subtitle/ass filter expression.

    Paths must be escaped for FFmpeg's filter-graph parser:
    colons become \\: and backslashes become \\\\.
    """
    p = str(subtitle_path.resolve())
    # Escape characters special to FFmpeg filter option strings
    p_esc = p.replace("\\", "\\\\").replace(":", "\\:")
    if p.endswith(".ass"):
        return f"ass=filename={p_esc}"
    style = (
        "FontName=Arial,FontSize=80,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,Outline=4,Bold=1,Alignment=2,MarginV=220"
    )
    return f"subtitles=filename={p_esc}:force_style='{style}'"


async def render_video(
    audio_path: Path,
    subtitle_path: Path | None,
    output_path: Path,
    template: str = "vertical_9_16",
    background_clips: list[Path] | None = None,
    progress_callback=None,
) -> Path:
    """
    Render a short-form video:
    - Stock footage background (looped/cropped) OR deep dark gradient fallback
    - 50% dark overlay so captions are always readable
    - TikTok-style ASS captions (large, bold, ALL CAPS, centred)
    - High-quality audio track
    """
    tmpl = TEMPLATES.get(template, TEMPLATES["vertical_9_16"])
    w, h, fps = tmpl["width"], tmpl["height"], tmpl["fps"]

    duration = await get_audio_duration(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    has_subs = subtitle_path is not None and subtitle_path.exists()

    # ── Build stock footage background ──────────────────────────────────────
    bg_video: Path | None = None
    if background_clips:
        bg_temp = output_path.parent / f"_bg_{output_path.stem}.mp4"
        success = await _concat_clips(background_clips, duration, w, h, bg_temp)
        if success and bg_temp.exists():
            bg_video = bg_temp

    # ── Compose final video ──────────────────────────────────────────────────
    if bg_video:
        vf_parts = ["drawbox=x=0:y=0:w=iw:h=ih:color=black@0.50:t=fill"]
        if has_subs:
            vf_parts.append(_subtitle_filter(subtitle_path))  # type: ignore[arg-type]
        cmd = [
            FFMPEG, "-y",
            "-i", str(bg_video),
            "-i", str(audio_path),
            "-vf", ",".join(vf_parts),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        # Deep dark navy fallback — looks clean on any social feed
        vf_parts = ["vignette=PI/3"]
        if has_subs:
            vf_parts.append(_subtitle_filter(subtitle_path))  # type: ignore[arg-type]
        cmd = [
            FFMPEG, "-y",
            "-f", "lavfi",
            "-i", f"color=c=0x0d0d1a:s={w}x{h}:d={duration}:r={fps}",
            "-i", str(audio_path),
            "-vf", ",".join(vf_parts),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(output_path),
        ]

    async def _run(cmd_to_run: list) -> tuple[int, str]:
        p = await asyncio.create_subprocess_exec(
            *cmd_to_run,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await p.communicate()
        return p.returncode, err.decode()

    returncode, stderr = await _run(cmd)

    # If subtitle filter failed (libass not compiled in), retry without subs
    _sub_filter_errors = (
        "No such filter: 'ass'",
        "No such filter: 'subtitles'",
        "No option name near",
        "libass",
    )
    if returncode != 0 and has_subs and any(e in stderr for e in _sub_filter_errors):
        logger.warning("Subtitle filter unavailable (libass not compiled into FFmpeg); retrying without subtitles.")
        # Rebuild cmd without the subtitle filter part
        if bg_video:
            cmd_nosubs = [
                FFMPEG, "-y",
                "-i", str(bg_video if bg_video else ""),
                "-i", str(audio_path),
                "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.50:t=fill",
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart",
                str(output_path),
            ]
        else:
            cmd_nosubs = [
                FFMPEG, "-y",
                "-f", "lavfi",
                "-i", f"color=c=0x0d0d1a:s={w}x{h}:d={duration}:r={fps}",
                "-i", str(audio_path),
                "-vf", "vignette=PI/3",
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart",
                str(output_path),
            ]
        returncode, stderr = await _run(cmd_nosubs)

    if bg_video and bg_video.exists():
        bg_video.unlink(missing_ok=True)

    if returncode != 0:
        logger.error(f"FFmpeg error:\n{stderr}")
        # Real FFmpeg errors are at the END of stderr; skip the verbose version banner
        raise RuntimeError(f"FFmpeg rendering failed: {stderr[-2000:]}")

    logger.info(f"Video rendered: {output_path} ({duration:.1f}s, stock={bg_video is not None})")
    return output_path


async def generate_thumbnail(video_path: Path, output_path: Path, time_offset: float = 1.0) -> Path:
    """Extract a thumbnail from the video."""
    cmd = [
        FFMPEG, "-y", "-i", str(video_path),
        "-ss", str(time_offset), "-vframes", "1",
        "-vf", "scale=540:960",
        str(output_path),
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return output_path

