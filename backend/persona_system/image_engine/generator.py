"""
persona_system/image_engine/generator.py
Stable Diffusion image generation with LoRA consistency, prompt templating,
batch generation, platform presets, activity scene prompts, and gallery sets.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from persona_system.config.settings import settings
from persona_system.persona_engine.loader import load_persona, get_image_preset

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Prompt variation pools
# ─────────────────────────────────────────────────────────────────────────────
ANGLE_VARIANTS = [
    "close-up portrait",
    "medium shot",
    "three-quarter angle",
    "slight low angle",
    "slight high angle",
    "over-the-shoulder view",
    "side profile",
    "looking away candidly",
]

EXPRESSION_VARIANTS = [
    "soft natural smile",
    "serious expression",
    "mid-laugh genuine",
    "thoughtful gaze",
    "playful smirk",
    "vulnerable soft look",
    "direct eye contact",
    "looking slightly off camera",
]

BACKGROUND_VARIANTS = [
    "minimal white bedroom",
    "cosy cafe interior bokeh",
    "golden hour outdoor park",
    "moody urban street night",
    "clean aesthetic kitchen",
    "abstract gradient studio",
    "window light apartment",
    "rooftop at dusk",
]

# Platform-specific size configs
PLATFORM_SIZES = {
    "instagram_feed":   {"width": 1080, "height": 1350},   # 4:5
    "instagram_reels":  {"width": 1080, "height": 1920},   # 9:16
    "onlyfans_photo":   {"width": 1080, "height": 1350},   # 4:5
    "onlyfans_video":   {"width": 1080, "height": 1920},   # 9:16
    "default":          {"width": 768,  "height": 1024},
}


def _get_platform_size(platform_preset: str | None) -> dict[str, int]:
    return PLATFORM_SIZES.get(platform_preset or "default", PLATFORM_SIZES["default"])


def _get_activity_scenes(persona: dict, pillar: str | None) -> list[dict]:
    """Return activity scenes for a pillar from persona YAML."""
    if not pillar:
        return []
    return persona.get("activity_scenes", {}).get(pillar, [])


def build_prompt(
    persona: dict,
    preset_name: str | None = None,
    angle: str | None = None,
    expression: str | None = None,
    background: str | None = None,
    activity_scene: str | None = None,
    extra: str = "",
    content_tier: str = "standard",
) -> dict[str, str]:
    """Build a full SD prompt tuple from persona + variation choices."""
    import random

    preset = get_image_preset(persona, preset_name)
    trigger = preset["trigger_word"] or persona.get("trigger_word", "")
    appearance = persona.get("appearance", {})

    chosen_angle      = angle      or random.choice(ANGLE_VARIANTS)
    chosen_expression = expression or random.choice(EXPRESSION_VARIANTS)
    chosen_background = background or random.choice(BACKGROUND_VARIANTS)

    positive_parts = [trigger, chosen_angle, chosen_expression, chosen_background]

    # Appearance details
    if appearance.get("hair"):
        positive_parts.append(appearance["hair"])
    if appearance.get("eyes"):
        positive_parts.append(appearance["eyes"])
    if appearance.get("skin"):
        positive_parts.append(appearance["skin"])

    # Activity scene (richer than just background)
    if activity_scene:
        positive_parts.append(activity_scene)

    # Style hints from content tier
    if content_tier == "premium":
        positive_parts.append("intimate direct gaze, soft cinematic lighting, highly detailed skin texture, raw photo, 8k uhd, authentic, natural pose, messy hair, depth of field")
    elif content_tier == "teaser":
        positive_parts.append("clean aesthetic, fashion, lifestyle")

    positive_parts.append(preset["positive"])
    if extra:
        positive_parts.append(extra)

    positive = ", ".join(p.strip(" ,") for p in positive_parts if p.strip())

    return {
        "positive": positive,
        "negative": preset["negative"],
        "preset": preset["preset_name"],
        "angle": chosen_angle,
        "expression": chosen_expression,
        "background": chosen_background,
        "activity": activity_scene or "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# A1111 API wrappers
# ─────────────────────────────────────────────────────────────────────────────
async def _txt2img(
    positive: str,
    negative: str,
    steps: int = None,
    cfg: float = None,
    width: int = 768,
    height: int = 1024,
    seed: int = -1,
) -> bytes:
    """Call A1111 /sdapi/v1/txt2img and return raw PNG bytes."""
    payload = {
        "prompt": positive,
        "negative_prompt": negative,
        "steps": steps or settings.sd_default_steps,
        "cfg_scale": cfg or settings.sd_default_cfg,
        "width": width,
        "height": height,
        "seed": seed,
        "sampler_name": "DPM++ 2M Karras",
        "restore_faces": True,
        "send_images": True,
        "save_images": False,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{settings.sd_base_url}/sdapi/v1/txt2img", json=payload)
        resp.raise_for_status()
        data = resp.json()
        img_b64 = data["images"][0]
        return base64.b64decode(img_b64)


async def _img2img(
    init_image_b64: str,
    positive: str,
    negative: str,
    denoising_strength: float = 0.35,
    steps: int = 25,
    width: int = 768,
    height: int = 1024,
) -> bytes:
    """Call A1111 /sdapi/v1/img2img (for gallery variant generation)."""
    payload = {
        "init_images": [init_image_b64],
        "prompt": positive,
        "negative_prompt": negative,
        "denoising_strength": denoising_strength,
        "steps": steps,
        "cfg_scale": settings.sd_default_cfg,
        "width": width,
        "height": height,
        "sampler_name": "DPM++ 2M Karras",
        "restore_faces": True,
        "send_images": True,
        "save_images": False,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{settings.sd_base_url}/sdapi/v1/img2img", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return base64.b64decode(data["images"][0])


async def _azure_dalle(
    positive: str,
    width: int = 1024,
    height: int = 1024,
    deployment: str | None = None,
) -> bytes:
    """Generate image via Azure OpenAI DALL-E 3 or GPT-Image-2.

    - DALL-E 3 (default): api-key header, response contains a URL to download
    - GPT-Image-2 (Sweden Central): Bearer token, response contains b64_json
    """
    import base64

    use_deployment = deployment or settings.azure_openai_dalle_deployment

    if use_deployment == "gpt-image-2":
        # --- GPT-Image-2: dedicated Sweden Central resource ---
        api_key = (
            settings.azure_gpt_image_2_api_key
            or settings.azure_openai_gpt_image_2_api_key
            or settings.azure_openai_api_key
        )
        endpoint = (settings.azure_gpt_image_2_endpoint or settings.azure_openai_endpoint).rstrip("/")
        url = f"{endpoint}/openai/deployments/{use_deployment}/images/generations?api-version=2024-02-01"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": positive,
            "n": 1,
            "size": "1024x1024",
            "quality": "low",
            "output_format": "png",
            "output_compression": 100,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            b64_data = data["data"][0].get("b64_json")
            if b64_data:
                return base64.b64decode(b64_data)
            # Fallback: URL response
            img_url = data["data"][0]["url"]
            img_resp = await client.get(img_url)
            img_resp.raise_for_status()
            return img_resp.content
    else:
        # --- Standard DALL-E 3 on primary Azure resource ---
        if not settings.azure_openai_api_key or not settings.azure_openai_endpoint:
            raise RuntimeError("Azure OpenAI credentials not set in .env")
        url = (
            f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
            f"{use_deployment}/images/generations?api-version=2024-02-01"
        )
        headers = {
            "api-key": settings.azure_openai_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": positive,
            "n": 1,
            "size": "1024x1024",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            image_url = data["data"][0]["url"]
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            return img_resp.content


# ─────────────────────────────────────────────────────────────────────────────
# Face / video pre-processing
# ─────────────────────────────────────────────────────────────────────────────
def preprocess_for_video(
    image_path: Path,
    output_path: Path,
    width: int = 512,
    height: int = 512,
) -> Path:
    """
    Crop face, enhance lighting, resize for lipsync input.
    Uses OpenCV + mediapipe where available.
    """
    try:
        import cv2
        import numpy as np

        img = cv2.imread(str(image_path))
        h, w = img.shape[:2]

        try:
            import mediapipe as mp
            mp_face = mp.solutions.face_detection
            with mp_face.FaceDetection(model_selection=1, min_detection_confidence=0.5) as det:
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                result = det.process(rgb)
                if result.detections:
                    bb = result.detections[0].location_data.relative_bounding_box
                    x1 = max(0, int((bb.xmin - 0.1) * w))
                    y1 = max(0, int((bb.ymin - 0.15) * h))
                    x2 = min(w, int((bb.xmin + bb.width + 0.1) * w))
                    y2 = min(h, int((bb.ymin + bb.height + 0.25) * h))
                    img = img[y1:y2, x1:x2]
        except Exception:
            side = min(h, w)
            x0, y0 = (w - side) // 2, (h - side) // 2
            img = img[y0:y0 + side, x0:x0 + side]

        # CLAHE lighting enhancement
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        img = cv2.resize(img, (width, height), interpolation=cv2.INTER_LANCZOS4)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return output_path

    except ImportError:
        logger.warning("OpenCV/mediapipe not available — copying image without preprocessing")
        import shutil
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(image_path), str(output_path))
        return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Auto-tagger
# ─────────────────────────────────────────────────────────────────────────────
async def auto_tag_image(image_path: Path) -> list[str]:
    """Return sensible default tags. SD tagger skipped (requires local A1111)."""
    return ["1girl", "solo", "looking_at_viewer", "realistic", "soft_lighting", "portrait"]


# ─────────────────────────────────────────────────────────────────────────────
# Batch generation (Phase 1 core)
# ─────────────────────────────────────────────────────────────────────────────
async def generate_image_batch(
    persona_name: str = "default",
    count: int = 5,
    output_dir: Path | None = None,
    pillar: str | None = None,
    platform_preset: str | None = None,   # "instagram_reels" | "onlyfans_video" | etc.
    content_tier: str = "standard",
    provider: str = "sd",  # "sd" | "azure_dalle"
) -> list[dict[str, Any]]:
    """
    Generate `count` images with maximum variation.
    Returns list of dicts with file_path, prompt, preset, tags, dimensions.
    """
    import random

    persona = load_persona(persona_name)
    size = _get_platform_size(platform_preset)
    w, h = size["width"], size["height"]

    out_dir = output_dir or (
        settings.media_root / "images" / persona_name / datetime.now(timezone.utc).strftime("%Y%m%d")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # Collect activity scenes for the pillar
    scenes = _get_activity_scenes(persona, pillar)

    results = []
    for i in range(count):
        # Pick activity scene if available
        activity_scene = None
        if scenes:
            activity_scene = scenes[i % len(scenes)]["scene"]

        prompt_data = build_prompt(
            persona,
            activity_scene=activity_scene,
            content_tier=content_tier,
        )

        try:
            if provider == "gpt-image-2":
                # GPT-Image-2 on Sweden Central — Bearer auth + b64_json
                png_bytes = await _azure_dalle(prompt_data["positive"], deployment="gpt-image-2")
                gen_w, gen_h = 1024, 1024
            elif provider in ("azure_dalle", "grok-3"):
                # DALL-E 3 or Grok-3 on primary Azure resource
                dept = settings.azure_openai_grok_deployment if provider == "grok-3" else settings.azure_openai_dalle_deployment
                png_bytes = await _azure_dalle(prompt_data["positive"], deployment=dept)
                gen_w, gen_h = 1024, 1024
            else:
                # Local Stable Diffusion
                png_bytes = await _txt2img(prompt_data["positive"], prompt_data["negative"], width=w, height=h)
                gen_w, gen_h = w, h

            file_path = out_dir / f"{uuid.uuid4().hex[:8]}.jpg"
            file_path.write_bytes(png_bytes)
            tags = await auto_tag_image(file_path)
            results.append({
                "file_path":    str(file_path),
                "prompt":       prompt_data["positive"],
                "negative":     prompt_data["negative"],
                "preset":       prompt_data["preset"],
                "angle":        prompt_data["angle"],
                "expression":   prompt_data["expression"],
                "background":   prompt_data["background"],
                "activity":     prompt_data["activity"],
                "platform":     platform_preset or "both",
                "content_tier": content_tier,
                "width":        gen_w,
                "height":       gen_h,
                "tags":         tags,
            })
            logger.info("Generated image %d/%d via %s → %s", i + 1, count, provider, file_path.name)
        except Exception as e:
            logger.error("Image generation failed (%d/%d): %s", i + 1, count, e)

        await asyncio.sleep(0.5)

    return results
