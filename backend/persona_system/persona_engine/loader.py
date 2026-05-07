"""
persona_system/persona_engine/loader.py
Load and cache persona YAML configs + apply persona to any text.
"""
from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from persona_system.config.settings import settings


@lru_cache(maxsize=16)
def load_persona(name: str | None = None) -> dict[str, Any]:
    """Return a parsed persona dict. Results are cached in-process."""
    persona_name = name or settings.active_persona
    persona_file = Path(settings.personas_dir) / f"{persona_name}.yaml"
    if not persona_file.exists():
        raise FileNotFoundError(f"Persona file not found: {persona_file}")
    with persona_file.open() as f:
        return yaml.safe_load(f)


def get_slang(persona: dict) -> list[str]:
    return persona.get("caption_style", {}).get("slang", [])


def get_content_pillars(persona: dict) -> list[str]:
    return persona.get("content_pillars", [])


def get_image_preset(persona: dict, preset_name: str | None = None) -> dict[str, str]:
    """Return base positive/negative + a randomly chosen (or specified) lighting preset."""
    img = persona.get("image_gen", {})
    presets = img.get("lighting_presets", {})

    # If this persona has no lighting presets, fall back to the default persona's presets
    if not presets:
        try:
            import yaml
            from pathlib import Path
            default_path = Path(__file__).resolve().parents[2] / "config" / "personas" / "default.yaml"
            default_cfg = yaml.safe_load(default_path.read_text())
            presets = default_cfg.get("image_gen", {}).get("lighting_presets", {})
            img = {**default_cfg.get("image_gen", {}), **img}  # merge, persona-specific wins
        except Exception:
            pass

    if not presets:
        # Last resort: return empty but valid dict so pipeline doesn't crash
        return {
            "positive": img.get("base_positive", "photorealistic, 8k, cinematic lighting"),
            "negative": img.get("base_negative", "blurry, watermark, low quality"),
            "preset_name": "natural",
            "trigger_word": persona.get("trigger_word", ""),
        }

    chosen_key = preset_name or random.choice(list(presets.keys()))
    return {
        "positive": f"{img.get('base_positive', '')}, {presets.get(chosen_key, '')}",
        "negative": img.get("base_negative", ""),
        "preset_name": chosen_key,
        "trigger_word": persona.get("trigger_word", ""),
    }


def apply_caption_style(text: str, persona: dict) -> str:
    """
    Transform a raw caption draft into the persona's style:
    - lowercase
    - minimal punctuation
    - inject a slang term occasionally
    """
    style = persona.get("caption_style", {})
    result = text.strip()
    if style.get("case") == "lowercase":
        result = result.lower()

    # Occasionally prepend a slang starter
    slang = get_slang(persona)
    if slang and random.random() < 0.4:
        starter = random.choice(slang)
        result = f"{starter} {result}"

    return result
