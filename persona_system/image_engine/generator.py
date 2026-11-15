"""
persona_system/image_engine/generator.py
Stable Diffusion image generation with LoRA consistency, prompt templating,
batch generation, and pre-processing for video input.
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
# Prompt templates
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


def build_prompt(
    persona: dict,
    preset_name: str | None = None,
    angle: str | None = None,
    expression: str | None = None,
    background: str | None = None,
    extra: str = "",
) -> dict[str, str]:
    """Build a full SD prompt tuple from persona + variation choices."""
    import random

    preset = get_image_preset(persona, preset_name)
    trigger = preset["trigger_word"] or persona.get("trigger_word", "")
    appearance = persona.get("appearance", {})

    chosen_angle      = angle      or random.choice(ANGLE_VARIANTS)
    chosen_expression = expression or random.choice(EXPRESSION_VARIANTS)
    chosen_background = background or random.choice(BACKGROUND_VARIANTS)

    positive = (
        f"{trigger}, {chosen_angle}, {chosen_expression}, {chosen_background}, "
        f"{appearance.get('hair', '')}, {appearance.get('eyes', '')}, "
        f"{appearance.get('skin', '')}, {preset['positive']}"
    )
    if extra:
        positive += f", {extra}"

    return {
        "positive": positive.strip(", "),
        "negative": preset["negative"],
        "preset": preset["preset_name"],
        "angle": chosen_angle,
        "expression": chosen_expression,
        "background": chosen_background,
    }


# ─────────────────────────────────────────────────────────────────────────────
# A1111 API wrapper (local Stable Diffusion)
# ─────────────────────────────────────────────────────────────────────────────
async def _txt2img(
    positive: str,
    negative: str,
    steps: int = None,
    cfg: float = None,
    width: int = 768,
    height: int = 1024,
) -> bytes:
    """Call A1111 /sdapi/v1/txt2img and return raw PNG bytes."""
    payload = {
        "prompt": positive,
        "negative_prompt": negative,
        "steps": steps or settings.sd_default_steps,
        "cfg_scale": cfg or settings.sd_default_cfg,
        "width": width,
        "height": height,
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
) -> bytes:
    payload = {
        "init_images": [init_image_b64],
        "prompt": positive,
        "negative_prompt": negative,
        "denoising_strength": denoising_strength,
        "steps": steps,
        "cfg_scale": settings.sd_default_cfg,
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


# ─────────────────────────────────────────────────────────────────────────────
# Face crop / pre-processing helpers
# ─────────────────────────────────────────────────────────────────────────────
def preprocess_for_video(image_path: Path, output_path: Path) -> Path:
    """
    Crop face correctly, enhance lighting, and prepare image for lipsync input.
    Uses OpenCV + mediapipe (graceful fallback to centre-crop if unavailable).
    """
    try:
        import cv2
        import numpy as np

        img = cv2.imread(str(image_path))
        h, w = img.shape[:2]

        # Try mediapipe face detection
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
            # Centre-crop fallback
            side = min(h, w)
            x0 = (w - side) // 2
            y0 = (h - side) // 2
            img = img[y0:y0+side, x0:x0+side]

        # Slight CLAHE lighting enhancement
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        # Resize to 512x512 for lipsync models
        img = cv2.resize(img, (512, 512), interpolation=cv2.INTER_LANCZOS4)
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
# Auto-tagger (for LoRA dataset)
# ─────────────────────────────────────────────────────────────────────────────
async def auto_tag_image(image_path: Path) -> list[str]:
    """
    Auto-tag an image using WD14 tagger via A1111 Tagger extension,
    or fall back to a basic keyword list.
    """
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.sd_base_url}/tagger/v1/interrogate",
                json={"image": img_b64, "model": "wd14-convnextv2-v2", "threshold": 0.35},
            )
            resp.raise_for_status()
            caption = resp.json().get("caption", {})
            return list(caption.keys())
    except Exception as e:
        logger.warning("Auto-tagging failed: %s", e)
        return ["1girl", "solo", "looking_at_viewer", "realistic"]


# ─────────────────────────────────────────────────────────────────────────────
# Batch generation
# ─────────────────────────────────────────────────────────────────────────────
async def generate_image_batch(
    persona_name: str = "default",
    count: int = 20,
    output_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Generate `count` images with maximum variation (angle, expression, lighting, background).
    Returns list of {"file_path", "prompt", "negative", "preset", "tags"}.
    """
    persona = load_persona(persona_name)
    out_dir = output_dir or (
        settings.media_root / "images" / persona_name / datetime.now(timezone.utc).strftime("%Y%m%d")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i in range(count):
        prompt_data = build_prompt(persona)
        try:
            png_bytes = await _txt2img(prompt_data["positive"], prompt_data["negative"])
            file_path = out_dir / f"{uuid.uuid4().hex[:8]}.jpg"
            file_path.write_bytes(png_bytes)
            tags = await auto_tag_image(file_path)
            results.append({
                "file_path": str(file_path),
                "prompt": prompt_data["positive"],
                "negative": prompt_data["negative"],
                "preset": prompt_data["preset"],
                "angle": prompt_data["angle"],
                "expression": prompt_data["expression"],
                "background": prompt_data["background"],
                "tags": tags,
            })
            logger.info("Generated image %d/%d → %s", i + 1, count, file_path.name)
        except Exception as e:
            logger.error("Image generation failed (%d/%d): %s", i + 1, count, e)

        # Small delay between requests to avoid overloading local SD
        await asyncio.sleep(0.5)

    return results
