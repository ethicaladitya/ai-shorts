"""
persona_system/image_engine/gallery.py

Generate coherent image gallery sets — a group of related images that share
outfit/scene but vary in expression/pose. Used for OnlyFans carousel posts.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from persona_system.config.settings import settings
from persona_system.persona_engine.loader import load_persona
from persona_system.image_engine.generator import (
    build_prompt,
    _txt2img,
    _img2img,
    auto_tag_image,
    _get_platform_size,
    EXPRESSION_VARIANTS,
    ANGLE_VARIANTS,
)

logger = logging.getLogger(__name__)


async def generate_image_gallery(
    persona_name: str = "default",
    pillar: str | None = None,
    scene_index: int = 0,
    gallery_size: int = 5,
    output_dir: Path | None = None,
    platform_preset: str = "onlyfans_photo",
    content_tier: str = "standard",
) -> list[dict[str, Any]]:
    """
    Generate a coherent gallery set:
    - Image 0: the hero shot (full prompt, randomised everything)
    - Images 1-N: variations via img2img (same outfit/scene, different expressions/angles)

    All images share a gallery_id UUID for grouping in the DB.
    """
    import random

    persona = load_persona(persona_name)
    size = _get_platform_size(platform_preset)
    w, h = size["width"], size["height"]

    gallery_id = uuid.uuid4().hex
    out_dir = output_dir or (
        settings.media_root / "images" / persona_name
        / datetime.now(timezone.utc).strftime("%Y%m%d") / f"gallery_{gallery_id[:8]}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pick a fixed scene for the whole gallery
    scenes = persona.get("activity_scenes", {}).get(pillar or "", [])
    if scenes:
        scene = scenes[scene_index % len(scenes)]
        activity_scene = scene["scene"]
    else:
        activity_scene = None

    # Pick fixed lighting preset for consistency
    presets = list(persona.get("image_gen", {}).get("lighting_presets", {}).keys())
    fixed_preset = random.choice(presets) if presets else None

    results = []

    # ── Image 0: hero shot ────────────────────────────────────────────────
    prompt_data = build_prompt(
        persona,
        preset_name=fixed_preset,
        activity_scene=activity_scene,
        content_tier=content_tier,
    )
    try:
        png_bytes = await _txt2img(prompt_data["positive"], prompt_data["negative"], width=w, height=h)
        hero_path = out_dir / f"{uuid.uuid4().hex[:8]}_hero.jpg"
        hero_path.write_bytes(png_bytes)
        hero_b64 = base64.b64encode(png_bytes).decode()

        tags = await auto_tag_image(hero_path)
        results.append({
            "file_path":    str(hero_path),
            "gallery_id":   gallery_id,
            "gallery_index": 0,
            "is_hero":      True,
            "prompt":       prompt_data["positive"],
            "negative":     prompt_data["negative"],
            "preset":       prompt_data["preset"],
            "activity":     activity_scene or "",
            "platform":     platform_preset,
            "content_tier": content_tier,
            "width": w, "height": h,
            "tags": tags,
        })
        logger.info("Gallery hero generated → %s", hero_path.name)
    except Exception as e:
        logger.error("Gallery hero failed: %s", e)
        return results

    # ── Images 1-N: variations via img2img ────────────────────────────────
    used_expressions = {prompt_data["expression"]}
    remaining_expressions = [e for e in EXPRESSION_VARIANTS if e not in used_expressions]

    for i in range(1, gallery_size):
        expr = remaining_expressions[(i - 1) % len(remaining_expressions)]
        var_prompt_data = build_prompt(
            persona,
            preset_name=fixed_preset,    # keep same lighting
            expression=expr,             # vary expression
            activity_scene=activity_scene,  # keep same scene
            content_tier=content_tier,
        )
        try:
            # Use img2img from hero to maintain visual consistency
            var_bytes = await _img2img(
                hero_b64,
                var_prompt_data["positive"],
                var_prompt_data["negative"],
                denoising_strength=0.4,  # low = more consistent with hero
                steps=25,
                width=w,
                height=h,
            )
            var_path = out_dir / f"{uuid.uuid4().hex[:8]}_var{i}.jpg"
            var_path.write_bytes(var_bytes)
            tags = await auto_tag_image(var_path)
            results.append({
                "file_path":    str(var_path),
                "gallery_id":   gallery_id,
                "gallery_index": i,
                "is_hero":      False,
                "prompt":       var_prompt_data["positive"],
                "negative":     var_prompt_data["negative"],
                "preset":       var_prompt_data["preset"],
                "activity":     activity_scene or "",
                "platform":     platform_preset,
                "content_tier": content_tier,
                "width": w, "height": h,
                "tags": tags,
            })
            logger.info("Gallery variant %d/%d → %s", i, gallery_size - 1, var_path.name)
        except Exception as e:
            logger.error("Gallery variant %d failed: %s", i, e)

        await asyncio.sleep(0.5)

    logger.info("Gallery %s complete: %d/%d images", gallery_id[:8], len(results), gallery_size)
    return results
