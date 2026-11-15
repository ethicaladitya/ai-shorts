"""
Motion Engine — animates still images into short video clips.

Free-first priority order:
  1. LivePortrait local (free, Apple Silicon MPS, best portrait motion)
  2. SVD local via A1111 (free, slower, needs A1111 SVD extension)
  3. Enhanced Ken-Burns FFmpeg (instant, always free, no install needed)
  4. Kling API (paid $0.14/clip — only used if key configured AND provider='kling')

The motion engine is called PER SHOT after image generation.
Each shot gets motion parameters tuned to its ShotType.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.ugc.scene_planner import ShotSpec

from app.config import settings

logger = logging.getLogger(__name__)

CLIP_DURATION_SECS = 7  # seconds — trimmed to actual audio duration in assembler
# 7s gives a generous buffer: most shots are 2–6s, so clips are always long enough.
# Clips are then stream-copied (not re-encoded) to the actual duration per shot.


async def animate_shot(
    image_path: Path,
    shot: "ShotSpec",
    output_dir: Path,
    provider: str = "auto",
) -> Path:
    """
    Animate a still image into a short video clip.

    'auto' = LivePortrait → SVD → Ken-Burns (all free, in quality order)
    'kling' = Kling API (only if key set, else falls to auto)
    'kenburns' = skip motion tools, straight to enhanced Ken-Burns
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"shot_{shot.index:02d}_{uuid.uuid4().hex[:6]}.mp4"

    # Kling: only if explicitly requested AND key configured
    if provider == "kling":
        if _kling_available():
            try:
                return await _kling_animate(image_path, shot, out_path)
            except Exception as exc:
                logger.warning("Kling shot %d failed (%s) — falling to free stack", shot.index, exc)
        else:
            logger.info("Kling requested but no API key — using free stack")

    # Free tier: LivePortrait → SVD → Ken-Burns
    if provider in ("auto", "liveportrait") and _liveportrait_available():
        try:
            return await _liveportrait_animate(image_path, shot, out_path)
        except Exception as exc:
            logger.warning("LivePortrait shot %d failed (%s) — trying SVD", shot.index, exc)

    if provider in ("auto", "svd") and _svd_available():
        try:
            return await _svd_animate(image_path, shot, out_path)
        except Exception as exc:
            logger.warning("SVD shot %d failed (%s) — Ken-Burns fallback", shot.index, exc)

    return await _kenburns_animate(image_path, shot, out_path)


async def animate_all_shots(
    image_paths: list[Path],
    shots: list["ShotSpec"],
    output_dir: Path,
    provider: str = "auto",
    concurrency: int = 2,
) -> list[Path]:
    """Animate all shots with bounded concurrency. Returns clips in shot order."""
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(img: Path, shot: "ShotSpec") -> Path:
        async with semaphore:
            return await animate_shot(img, shot, output_dir, provider)

    results = await asyncio.gather(
        *[_one(img, shot) for img, shot in zip(image_paths, shots)],
        return_exceptions=True,
    )

    clips: list[Path] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("Shot %d animation failed: %s — Ken-Burns fallback", i, result)
            clips.append(await _kenburns_animate(image_paths[i], shots[i], output_dir))
        else:
            clips.append(result)  # type: ignore[arg-type]

    return clips


# ── Provider availability checks ──────────────────────────────────────────────

def _kling_available() -> bool:
    return bool(settings.kling_api_key and settings.kling_api_secret)


def _liveportrait_available() -> bool:
    cli = getattr(settings, "liveportrait_cli_path", "")
    return bool(cli and Path(cli).exists())


def _svd_available() -> bool:
    svd_url = getattr(settings, "svd_base_url", "")
    return bool(svd_url)


# ── Kling provider (paid) ──────────────────────────────────────────────────────

async def _kling_animate(image_path: Path, shot: "ShotSpec", output_path: Path) -> Path:
    from persona_system.video_engine_adapter.animated_clip import generate_activity_clip
    clip = await generate_activity_clip(
        image_path=image_path,
        motion_prompt=shot.motion_prompt,
        output_path=output_path,
    )
    logger.info("Kling clip shot %d → %s", shot.index, output_path.name)
    return clip


# ── LivePortrait provider (free local) ────────────────────────────────────────

async def _liveportrait_animate(image_path: Path, shot: "ShotSpec", output_path: Path) -> Path:
    """
    Run LivePortrait to animate a portrait image.

    LivePortrait uses a driving video or motion template to animate a source portrait.
    We select a driving template based on the shot's motion type.

    Install:
        git clone https://github.com/KwaiVGI/LivePortrait
        cd LivePortrait && pip install -r requirements.txt
    Then set: LIVEPORTRAIT_CLI_PATH=/path/to/LivePortrait/inference.py
    """
    cli = settings.liveportrait_cli_path
    device = getattr(settings, "liveportrait_device", "mps")
    driving_template = _liveportrait_driving_template(shot)

    cmd = [
        "python3", cli,
        "--source", str(image_path),
        "--driving", driving_template,
        "--output", str(output_path),
        "--device", device,
        "--no_flag_pasteback",  # crop to face region for tighter UGC frame
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"LivePortrait failed: {stderr.decode()[:300]}")

    logger.info("LivePortrait clip shot %d → %s", shot.index, output_path.name)
    return output_path


def _liveportrait_driving_template(shot: "ShotSpec") -> str:
    """
    Map shot motion type to a LivePortrait driving video template.
    Templates ship with LivePortrait in assets/examples/driving/.
    """
    from app.services.ugc.scene_planner import MotionType
    lp_root = Path(settings.liveportrait_cli_path).parent if settings.liveportrait_cli_path else Path(".")
    templates_dir = lp_root / "assets" / "examples" / "driving"

    motion_to_template = {
        MotionType.BREATHING:    "d0.mp4",
        MotionType.LEAN_CLOSER:  "d5.mp4",
        MotionType.HEAD_TILT:    "d3.mp4",
        MotionType.LOOK_UP:      "d2.mp4",
        MotionType.LAUGH:        "d4.mp4",
        MotionType.STATIC_DRIFT: "d0.mp4",
        MotionType.PUSH_ZOOM:    "d1.mp4",
        MotionType.PULL_ZOOM:    "d1.mp4",
        MotionType.REFRAME:      "d3.mp4",
        MotionType.WALK_TOWARD:  "d5.mp4",
    }
    template_file = motion_to_template.get(shot.motion_type, "d0.mp4")
    full_path = templates_dir / template_file
    # Fall back to any .mp4 in templates dir if specific one doesn't exist
    if not full_path.exists():
        candidates = list(templates_dir.glob("*.mp4"))
        if candidates:
            return str(candidates[0])
    return str(full_path)


# ── SVD provider (free local via A1111) ───────────────────────────────────────

async def _svd_animate(image_path: Path, shot: "ShotSpec", output_path: Path) -> Path:
    from persona_system.video_engine_adapter.animated_clip import generate_activity_clip
    clip = await generate_activity_clip(
        image_path=image_path,
        motion_prompt=shot.motion_prompt,
        output_path=output_path,
    )
    return clip


# ── Ken-Burns FFmpeg fallback (always free, always works) ─────────────────────

async def _kenburns_animate(image_path: Path, shot: "ShotSpec", output_path: Path) -> Path:
    """
    Motion-type-aware Ken-Burns. Different shot types get different zoom behaviours.
    This is the zero-dependency fallback that runs without ANY additional installs.
    """
    from app.services.video_renderer import FFMPEG
    from app.services.ugc.scene_planner import MotionType

    fps = 30
    duration = CLIP_DURATION_SECS
    frames = fps * duration
    width, height = 1080, 1920

    mt = shot.motion_type

    # Motion-type → Ken-Burns parameters
    if mt in (MotionType.PUSH_ZOOM, MotionType.LEAN_CLOSER):
        # Fast push-in — creates intimacy, good for hook/lean-in shots
        zoom_expr  = "min(zoom+0.0007,1.20)"
        x_expr     = "iw/2-(iw/zoom/2)"
        y_expr     = "ih/2-(ih/zoom/2)"
    elif mt in (MotionType.PULL_ZOOM, MotionType.REFRAME):
        # Pull out — reveals environment, good for casual/environment shots
        zoom_expr  = "max(zoom-0.0005,1.0)"
        x_expr     = "iw/2-(iw/zoom/2)"
        y_expr     = "ih/2-(ih/zoom/2)"
    elif mt == MotionType.WALK_TOWARD:
        # Fast zoom with slight downward drift — simulates walking motion
        # zoompan uses 'on' (output frame number) for time — NOT 't' or 'T'
        # on/30 = seconds at 30fps; on*speed_per_frame = linear drift
        drift_per_frame = height * 0.003 / fps
        zoom_expr  = "min(zoom+0.0010,1.30)"
        x_expr     = "iw/2-(iw/zoom/2)"
        y_expr     = f"min(ih*0.52-(ih/zoom/2)+on*{drift_per_frame:.4f},{height})"
    elif mt in (MotionType.HEAD_TILT, MotionType.LOOK_UP):
        # Subtle drift with slight horizontal pan — simulates head movement
        # sin frequency: 0.8 rad/s * (1/30) frames/rad ≈ 0.0267 rad/frame
        pan_amp = width * 0.005
        zoom_expr  = "min(zoom+0.0002,1.06)"
        x_expr     = f"iw/2-(iw/zoom/2)+sin(on*0.0267)*{pan_amp:.1f}"
        y_expr     = "ih/2-(ih/zoom/2)"
    elif mt == MotionType.LAUGH:
        # Quick push + micro bounce — simulates laugh energy
        # sin frequency: 4 rad/s * (1/30) ≈ 0.1333 rad/frame
        bounce_amp = height * 0.002
        zoom_expr  = "min(zoom+0.0008,1.15)"
        x_expr     = "iw/2-(iw/zoom/2)"
        y_expr     = f"ih/2-(ih/zoom/2)+sin(on*0.1333)*{bounce_amp:.1f}"
    else:
        # BREATHING / STATIC_DRIFT: barely perceptible — life in the frame
        # sin frequency: 0.3 rad/s * (1/30) ≈ 0.01 rad/frame
        sway_amp = width * 0.002
        zoom_expr  = "min(zoom+0.0001,1.04)"
        x_expr     = f"iw/2-(iw/zoom/2)+sin(on*0.01)*{sway_amp:.1f}"
        y_expr     = "ih/2-(ih/zoom/2)"

    # Scale to cover the 9:16 output frame regardless of input aspect ratio,
    # then zoompan on the 2x canvas so pan/zoom never hits an image edge.
    # scale with force_original_aspect_ratio=increase fills the canvas; crop centres it.
    filter_complex = (
        f"[0:v]"
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2},"
        f"zoompan="
        f"z='{zoom_expr}':"
        f"d={frames}:"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        f"s={width}x{height}:fps={fps},"
        f"setsar=1[out]"
    )

    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-t", str(duration), "-i", str(image_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        # CRF=18 gives visually near-lossless quality at ~5-10 MB vs 190 MB at CRF=0.
        # The final assembly re-encodes anyway so two-pass quality loss is imperceptible.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-an", str(output_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Ken-Burns FFmpeg failed: {stderr.decode()[:600]}")

    logger.info("Ken-Burns (%s) shot %d → %s", mt.value, shot.index, output_path.name)
    return output_path
