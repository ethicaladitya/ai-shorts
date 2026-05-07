"""
persona_system/video_engine_adapter/animated_clip.py

Generates a short animated video clip from a still image.
Supports two providers, selectable via settings:
  - "svd"   → Stable Video Diffusion via local A1111 (free, slower)
  - "kling" → Kling AI image-to-video API (paid ~$0.14/video, fast)

Usage:
    from persona_system.video_engine_adapter.animated_clip import generate_activity_clip
    clip = await generate_activity_clip(image_path, motion_prompt, output_path)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
import uuid
from pathlib import Path

import httpx

from persona_system.config.settings import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Motion prompt library (pillar → motion description)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_MOTION_PROMPTS: dict[str, str] = {
    "late night energy":          "slow gentle breathing, subtle hair movement, soft natural blinking, phone glow on face",
    "soft-chaos lifestyle":        "slow exhale, looks down then back up, subtle shoulder movement, natural",
    "intimate eye contact moments":"slow natural blinking, barely perceptible breathing, slight lip part, direct gaze",
    "aesthetic daily routines":    "slow graceful movement, natural hair fall, serene expression, soft breath",
    "thought-provoking short takes":"slight head tilt, thoughtful gaze, exhales slowly, gentle eye movement",
    "_default":                    "slow gentle breathing, soft natural blinking, subtle hair movement, cinematic",
}


def get_motion_prompt(pillar: str | None, persona: dict | None = None) -> str:
    """Get motion prompt from pillar name, falling back to persona YAML, then default."""
    if persona:
        scenes = persona.get("activity_scenes", {}).get(pillar or "", [])
        if scenes:
            import random
            return random.choice(scenes).get("motion", DEFAULT_MOTION_PROMPTS["_default"])
    if pillar and pillar in DEFAULT_MOTION_PROMPTS:
        return DEFAULT_MOTION_PROMPTS[pillar]
    return DEFAULT_MOTION_PROMPTS["_default"]


# ─────────────────────────────────────────────────────────────────────────────
# SVD provider (local Stable Video Diffusion via A1111)
# ─────────────────────────────────────────────────────────────────────────────
async def _svd_generate(
    image_path: Path,
    output_path: Path,
    motion_bucket_id: int | None = None,
    augmentation_level: float | None = None,
    num_frames: int | None = None,
    fps: int | None = None,
) -> Path:
    """
    Generate animated clip via A1111's /sdapi/v1/img2vid endpoint (SVD-XT).
    Falls back to /sdapi/v1/img2img with animate_diff if svd endpoint not present.
    """
    with image_path.open("rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "image": img_b64,
        "motion_bucket_id": motion_bucket_id or settings.svd_motion_bucket_id,
        "augmentation_level": augmentation_level or settings.svd_augmentation_level,
        "frames": num_frames or settings.svd_num_frames,
        "fps": fps or settings.svd_fps,
        "output_format": "mp4",
    }

    base = settings.svd_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=600.0) as client:
        # Try SVD endpoint first
        try:
            resp = await client.post(f"{base}/sdapi/v1/img2vid", json=payload)
            resp.raise_for_status()
            data = resp.json()
            video_b64 = data.get("video") or data.get("videos", [None])[0]
            if video_b64:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(base64.b64decode(video_b64))
                logger.info("SVD clip generated → %s", output_path.name)
                return output_path
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            logger.warning("SVD /img2vid not available, trying AnimateDiff fallback")

        # AnimateDiff fallback (requires A1111 AnimateDiff extension)
        ad_payload = {
            "init_images": [img_b64],
            "prompt": "cinematic, smooth motion, natural movement, high quality",
            "negative_prompt": "static, blurry, distorted, artifacts",
            "steps": 20,
            "cfg_scale": 7.0,
            "denoising_strength": 0.6,
            "alwayson_scripts": {
                "animatediff": {
                    "args": [{
                        "enable": True,
                        "model": "mm_sd_v15_v2.ckpt",
                        "format": ["MP4"],
                        "video_length": num_frames or settings.svd_num_frames,
                        "fps": fps or settings.svd_fps,
                        "loop_number": 0,
                        "closed_loop": "R+P",
                    }]
                }
            }
        }
        resp = await client.post(f"{base}/sdapi/v1/img2img", json=ad_payload)
        resp.raise_for_status()
        data = resp.json()

        # AnimateDiff returns video path in info
        import json
        info = json.loads(data.get("info", "{}"))
        video_path_str = info.get("animatediff", {}).get("output_mp4")
        if video_path_str:
            import shutil
            shutil.copy2(video_path_str, str(output_path))
            return output_path

        raise RuntimeError("SVD and AnimateDiff both failed to produce a video output")


# ─────────────────────────────────────────────────────────────────────────────
# Kling AI provider
# ─────────────────────────────────────────────────────────────────────────────
def _kling_jwt(api_key: str, api_secret: str) -> str:
    """Generate a JWT for Kling API authentication."""
    import base64
    import json

    header  = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": api_key,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5,
    }

    def _b64url(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode()

    header_enc  = _b64url(header)
    payload_enc = _b64url(payload)
    signing_input = f"{header_enc}.{payload_enc}".encode()
    sig = hmac.new(api_secret.encode(), signing_input, hashlib.sha256).digest()
    sig_enc = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{header_enc}.{payload_enc}.{sig_enc}"


async def _kling_generate(
    image_path: Path,
    motion_prompt: str,
    output_path: Path,
    duration: int | None = None,
    cfg_scale: float | None = None,
) -> Path:
    """Submit image-to-video job to Kling AI and poll until complete."""
    if not settings.kling_api_key or not settings.kling_api_secret:
        raise RuntimeError("KLING_API_KEY and KLING_API_SECRET must be set in .env")

    token = _kling_jwt(settings.kling_api_key, settings.kling_api_secret)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    base = "https://api.klingai.com"

    # Upload image as base64
    with image_path.open("rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    job_payload = {
        "model_name": settings.kling_model,
        "image": img_b64,
        "prompt": motion_prompt,
        "negative_prompt": "distorted face, artifacts, blurry, morphing, melting skin",
        "cfg_scale": cfg_scale or settings.kling_cfg_scale,
        "mode": "std",
        "duration": str(duration or settings.kling_clip_duration),
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base}/v1/videos/image2video",
            json=job_payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    task_id = data.get("data", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"Kling did not return task_id: {data}")

    logger.info("Kling task submitted: %s", task_id)

    # Poll for completion (max 10 min)
    for attempt in range(120):
        await asyncio.sleep(5)
        token = _kling_jwt(settings.kling_api_key, settings.kling_api_secret)
        async with httpx.AsyncClient(timeout=30.0) as client:
            status_resp = await client.get(
                f"{base}/v1/videos/image2video/{task_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            status_resp.raise_for_status()
            status_data = status_resp.json().get("data", {})

        task_status = status_data.get("task_status", "")
        if task_status == "succeed":
            video_url = (
                status_data.get("task_result", {})
                .get("videos", [{}])[0]
                .get("url", "")
            )
            if not video_url:
                raise RuntimeError("Kling returned success but no video URL")

            # Download
            async with httpx.AsyncClient(timeout=300.0) as client:
                dl = await client.get(video_url)
                dl.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(dl.content)
            logger.info("Kling clip downloaded → %s", output_path.name)
            return output_path

        elif task_status == "failed":
            err = status_data.get("task_status_msg", "unknown")
            raise RuntimeError(f"Kling task failed: {err}")

        logger.debug("Kling task %s: %s (attempt %d)", task_id, task_status, attempt)

    raise TimeoutError(f"Kling task {task_id} did not complete within 10 minutes")


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────
async def generate_activity_clip(
    image_path: Path,
    output_path: Path,
    motion_prompt: str | None = None,
    pillar: str | None = None,
    persona: dict | None = None,
    provider: str | None = None,
    duration_seconds: int = 5,
) -> Path:
    """
    Generate an animated activity clip from a still image.

    Args:
        image_path:     Source image (JPEG/PNG, ideally 1080x1920).
        output_path:    Where to write the MP4 clip.
        motion_prompt:  Override motion description. Auto-selected from pillar if None.
        pillar:         Content pillar name (used to pick motion prompt).
        persona:        Parsed persona dict (for richer motion prompts).
        provider:       "svd" | "kling" — overrides settings.video_clip_provider.
        duration_seconds: Target clip duration (Kling: 5 or 10s; SVD: fixed by frames).

    Returns:
        Path to the generated MP4 clip.
    """
    prov = (provider or settings.video_clip_provider).lower()
    prompt = motion_prompt or get_motion_prompt(pillar, persona)

    logger.info("Generating activity clip via '%s': %s", prov, prompt[:80])

    if prov == "kling":
        return await _kling_generate(image_path, prompt, output_path, duration=duration_seconds)
    elif prov == "grok-3":
        # Grok-3 video API not yet publicly available.
        # Fall through to SVD (local) as the best available alternative.
        logger.info("Grok-3 video not available — using SVD (local) fallback")
        return await _svd_generate(image_path, output_path)
    else:
        # SVD (default / local) — free, no API keys required
        return await _svd_generate(image_path, output_path)
