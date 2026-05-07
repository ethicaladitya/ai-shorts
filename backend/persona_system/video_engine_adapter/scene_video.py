"""
persona_system/video_engine_adapter/scene_video.py

Generates a dynamic multi-scene video by:
  1. Taking a reference persona image (same person every time)
  2. Using gpt-image-2 /images/edits to place that person in different
     backgrounds/activities (cosy bedroom, cafe, city window, etc.)
  3. Using ffmpeg xfade to crossfade between scenes with Ken Burns zoom
  4. Mixing in the TTS audio track

Result: same model, fresh background + body action in every video.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import uuid
from pathlib import Path
from typing import Optional

import httpx

from persona_system.config.settings import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Scene catalogue — each entry = one edit prompt variation
# Rotated randomly so each video has a unique combination of scenes
# ─────────────────────────────────────────────────────────────────────────────
SCENE_CATALOGUE = [
    # ── Walking / mall / outdoor ──────────────────────────────────────────
    {
        "tag": "mall_shopping",
        "prompt": (
            "same woman, walking through a bright shopping mall, "
            "holding shopping bags in both hands, looking ahead with a slight smile, "
            "other shoppers blurred in background, full body shot, "
            "casual stylish outfit, natural mall lighting"
        ),
    },
    {
        "tag": "wardrobe_picking",
        "prompt": (
            "same woman, standing in front of open wardrobe, "
            "one hand holding a hanger with a dress, looking at it thoughtfully, "
            "the other hand on her hip, bedroom background, full body, "
            "morning light, cosy aesthetic wardrobe"
        ),
    },
    {
        "tag": "park_walking",
        "prompt": (
            "same woman, walking along a tree-lined park path, "
            "looking to the side with a relaxed smile, "
            "dappled sunlight through leaves on her face, "
            "full body shot, casual outfit, golden hour light behind her"
        ),
    },
    {
        "tag": "showing_outfit",
        "prompt": (
            "same woman, standing facing camera in a stylish outfit, "
            "arms slightly out with palms up as if showing the look, "
            "slight confident smile, bedroom or neutral background, "
            "full body visible, fashion-forward pose, soft natural light"
        ),
    },
    {
        "tag": "kitchen_coffee",
        "prompt": (
            "same woman, leaning against kitchen counter, "
            "both hands wrapped around a mug, "
            "looking out the kitchen window in the morning, "
            "soft morning window light, cosy kitchen background, "
            "full body visible, oversized shirt or hoodie"
        ),
    },
    {
        "tag": "outdoor_cafe_walk",
        "prompt": (
            "same woman, walking past outdoor café tables on a sunny street, "
            "one hand holding an iced coffee, glancing toward camera, "
            "sunglasses on or in hand, full body, pedestrians blurred behind, "
            "bright summer lighting"
        ),
    },
    # ── Late night / bedroom ──────────────────────────────────────────────
    {
        "tag": "bedroom_phone",
        "prompt": (
            "same woman, sitting cross-legged on bed at night, "
            "holding phone with both hands, soft fairy lights behind her, "
            "phone screen glow on face, knees visible, full upper body shot, "
            "hands clearly visible, cosy bedroom background"
        ),
    },
    {
        "tag": "bedroom_lying",
        "prompt": (
            "same woman, lying on stomach on bed, feet kicked up behind her, "
            "chin resting on one hand, elbow on pillow, looking at camera, "
            "full body visible, aesthetic bedroom with string lights, warm lamp"
        ),
    },
    # ── Cafe / coffee ─────────────────────────────────────────────────────
    {
        "tag": "cafe_coffee",
        "prompt": (
            "same woman, sitting at cafe table, both hands wrapped around "
            "a tall iced coffee cup, looking up at camera, cafe interior "
            "background with bokeh, golden hour window light, "
            "arms and hands visible, 3/4 body shot"
        ),
    },
    {
        "tag": "cafe_window",
        "prompt": (
            "same woman, sitting at cafe window seat, elbow on table, "
            "chin resting in hand with fingers near face, "
            "gazing toward window, city street visible through glass behind her, "
            "rings on fingers visible, soft daylight, 3/4 body"
        ),
    },
    # ── Mirror / bathroom ────────────────────────────────────────────────
    {
        "tag": "mirror_selfie",
        "prompt": (
            "same woman, standing at bathroom mirror with soft vanity lights, "
            "one arm raised with hand touching mirror glass, "
            "slight head tilt, looking at own reflection, "
            "full body visible in mirror, warm bulb lighting"
        ),
    },
    # ── Floor / casual ───────────────────────────────────────────────────
    {
        "tag": "floor_hugging_knees",
        "prompt": (
            "same woman, sitting on wooden floor with back against white wall, "
            "knees pulled up to chest, arms hugging knees, "
            "looking up slightly at camera, full body shot, "
            "natural side window light, minimalist room"
        ),
    },
    # ── Window / city night ───────────────────────────────────────────────
    {
        "tag": "city_window",
        "prompt": (
            "same woman, standing at large apartment window at night, "
            "one hand pressed lightly on the glass, "
            "looking out at blurred city lights below, profile pose, "
            "city glow illuminating her face and hand, full body visible"
        ),
    },
    # ── Desk / stretch ───────────────────────────────────────────────────
    {
        "tag": "desk_stretch",
        "prompt": (
            "same woman, sitting at aesthetic desk with warm lamp, "
            "both arms stretched overhead in a big stretch, "
            "eyes closed, oversized hoodie, full body visible, "
            "plants and soft decor in background, cosy evening light"
        ),
    },
    # ── Couch / relaxed ──────────────────────────────────────────────────
    {
        "tag": "couch_relaxed",
        "prompt": (
            "same woman, lounging on couch sideways, legs draped over armrest, "
            "one hand behind head, other hand resting on stomach, "
            "looking at camera with slight smirk, "
            "cosy living room, warm lamp, full body visible"
        ),
    },
    # ── Outdoor / rooftop ────────────────────────────────────────────────
    {
        "tag": "rooftop_night",
        "prompt": (
            "same woman, standing on rooftop at night, "
            "arms resting on railing, looking over city skyline, "
            "wind slightly moving her hair, "
            "city lights and dark sky behind her, full body visible, "
            "cool blue purple ambient light"
        ),
    },
]


async def _edit_scene(
    persona_image_path: Path,
    prompt: str,
    output_path: Path,
    client: httpx.AsyncClient,
) -> Path:
    """
    Call gpt-image-2 /images/edits with the persona image as reference.
    The model places the same person in the new scene described by prompt.
    """
    endpoint = settings.azure_gpt_image_2_endpoint.rstrip("/")
    deployment = settings.azure_gpt_image_2_deployment or "gpt-image-2"
    # Prefer dedicated GPT-Image-2 key, fall back to main Azure key
    api_key = (
        settings.azure_gpt_image_2_api_key
        or settings.azure_openai_gpt_image_2_api_key
        or settings.azure_openai_api_key
    )
    if not endpoint or not api_key:
        raise RuntimeError(
            "AZURE_GPT_IMAGE_2_ENDPOINT and AZURE_GPT_IMAGE_2_API_KEY must be set in .env"
        )
    api_version = "2025-04-01-preview"
    url = f"{endpoint}/openai/deployments/{deployment}/images/edits?api-version={api_version}"

    img_bytes = persona_image_path.read_bytes()
    suffix = persona_image_path.suffix.lstrip(".") or "jpeg"

    resp = await client.post(
        url,
        headers={"api-key": api_key},  # Azure uses api-key, not Authorization: Bearer
        data={
            "prompt": prompt,
            "size": "1024x1792",
            "quality": "low",
            "n": "1",
        },
        files={"image": (persona_image_path.name, img_bytes, f"image/{suffix}")},
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()

    b64 = data["data"][0].get("b64_json", "")
    if not b64:
        # Some API versions return a URL instead of b64
        url_out = data["data"][0].get("url", "")
        if url_out:
            dl = await client.get(url_out, timeout=60.0)
            dl.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl.content)
            logger.info("Scene edited (URL) → %s (%s)", output_path.name, prompt[:60])
            return output_path
        raise RuntimeError("gpt-image-2 edits returned no b64_json or url")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(b64))
    logger.info("Scene edited → %s (%s)", output_path.name, prompt[:60])
    return output_path


async def generate_scene_images(
    persona_image_path: Path,
    output_dir: Path,
    n_scenes: int = 4,
    pillar: Optional[str] = None,
) -> list[Path]:
    """
    Generate n_scenes scene variations of the persona image.
    Each scene shows the same person in a different background + body action.
    Scenes are randomly selected from the catalogue so each video is unique.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    pool = SCENE_CATALOGUE.copy()
    random.shuffle(pool)
    selected = pool[:n_scenes]

    paths: list[Path] = []
    async with httpx.AsyncClient() as client:
        for i, scene in enumerate(selected):
            out = output_dir / f"scene_{uuid.uuid4().hex[:6]}.png"
            try:
                path = await _edit_scene(persona_image_path, scene["prompt"], out, client)
                paths.append(path)
                logger.info("Scene %d/%d ready (%s)", i + 1, n_scenes, scene["tag"])
            except Exception as e:
                logger.warning("Scene %d (%s) failed: %s", i + 1, scene["tag"], e)

    if not paths:
        raise RuntimeError("All scene generations failed — check gpt-image-2 edits endpoint")

    return paths


async def create_scene_slideshow(
    scene_images: list[Path],
    audio_path: Path,
    output_path: Path,
    width: int = 1080,
    height: int = 1920,
    crossfade_duration: float = 0.8,
) -> Path:
    """
    Crossfade slideshow: each image gets equal screen time, smooth xfade
    transitions, Ken Burns zoom variation per clip, audio mixed in.
    """
    import subprocess

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    audio_dur = float(result.stdout.strip() or "30")

    n = len(scene_images)
    per_scene = max(audio_dur / n, 3.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_path.parent
    uid = uuid.uuid4().hex[:6]

    # ── Step 1: Render each scene image as a short Ken Burns clip ────────
    clips: list[Path] = []
    zoom_effects = [
        ("zoom+0.0004", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),   # centre zoom in
        ("zoom+0.0002", "0",                "ih/2-(ih/zoom/2)"),    # left zoom in slow
        ("zoom+0.0003", "iw-(iw/zoom)",     "ih/2-(ih/zoom/2)"),    # right zoom in
        ("zoom+0.0004", "iw/2-(iw/zoom/2)", "ih-(ih/zoom)"),        # bottom zoom in
    ]

    for i, img in enumerate(scene_images):
        clip_path = tmp_dir / f"sc_{uid}_{i}.mp4"
        z, x, y = zoom_effects[i % len(zoom_effects)]
        frames = int(per_scene * 30)

        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"zoompan=z='min({z},1.5)':x='{x}':y='{y}'"
            f":d={frames}:s={width}x{height}:fps=30"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(per_scene),
            "-i", str(img),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            str(clip_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Scene clip {i} render failed: {stderr.decode()[-400:]}")
        clips.append(clip_path)

    logger.info("Rendered %d scene clips", len(clips))

    # ── Step 2: xfade all clips together ─────────────────────────────────
    if len(clips) == 1:
        combined = clips[0]
    else:
        inputs_args = []
        for c in clips:
            inputs_args += ["-i", str(c)]

        filter_parts = []
        offset = per_scene - crossfade_duration
        prev = "[0:v]"
        for i in range(1, len(clips)):
            label = "[vout]" if i == len(clips) - 1 else f"[v{i}]"
            filter_parts.append(
                f"{prev}[{i}:v]xfade=transition=fade"
                f":duration={crossfade_duration}:offset={offset:.3f}{label}"
            )
            offset += per_scene - crossfade_duration
            prev = label

        combined = tmp_dir / f"combined_{uid}.mp4"
        cmd = ["ffmpeg", "-y"] + inputs_args + [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[vout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            str(combined),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"xfade combine failed: {stderr.decode()[-400:]}")
        logger.info("Scenes crossfaded")

    # ── Step 3: Mix audio ─────────────────────────────────────────────────
    cmd = [
        "ffmpeg", "-y",
        "-i", str(combined),
        "-i", str(audio_path),
        "-c:v", "copy",
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
        raise RuntimeError(f"Audio mix failed: {stderr.decode()[-400:]}")

    # Cleanup temp clips
    for c in clips:
        c.unlink(missing_ok=True)
    if len(clips) > 1:
        combined.unlink(missing_ok=True)

    logger.info("Scene slideshow complete → %s", output_path.name)
    return output_path
