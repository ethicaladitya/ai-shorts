"""UGC-style image prompt templates for Stable Diffusion / ComfyUI."""
from __future__ import annotations

# Always appended to every UGC scene positive prompt
UGC_POSITIVE_SUFFIX = (
    "shot on iPhone, natural window light, slightly overexposed, handheld camera, "
    "f/1.8 shallow depth of field, imperfect framing, candid moment, authentic, "
    "casual everyday setting, raw unedited look, real person"
)

# Always appended to every UGC scene negative prompt
UGC_NEGATIVE_SUFFIX = (
    "studio lighting, professional photography, stock photo, perfect symmetry, "
    "airbrushed skin, color grading, branded overlay, lower third, watermark, "
    "advertisement, corporate, plastic skin, hyperrealistic render, CGI, "
    "uniform background, beauty dish, softbox"
)

# Scene context templates per style
STYLE_SCENE_CONTEXTS = {
    "ugc": [
        "sitting on couch at home, casual living room background",
        "standing in kitchen, morning light through window",
        "walking outside, urban background slightly blurred",
        "at a desk with laptop visible in background",
        "in bedroom, natural light from side window",
    ],
    "selfie": [
        "holding phone slightly above eye level, arm visible at edge",
        "mirror selfie in bathroom, casual outfit",
        "selfie outside with sky and buildings behind",
        "seated in car, parked, natural daylight through windshield",
    ],
    "casual-vlog": [
        "wide shot of person in room, camera on desk",
        "outdoor setting, person walking toward camera",
        "coffee shop background, bokeh lights",
        "park bench, natural greenery behind",
    ],
}


def build_ugc_positive(
    persona: dict,
    scene_context: str,
    extra: str = "",
) -> str:
    """Assemble the full positive prompt for a UGC scene image."""
    parts: list[str] = []

    # Persona trigger word (for LoRA consistency)
    trigger = persona.get("trigger_word", "")
    if trigger:
        parts.append(trigger)

    # Appearance description
    appearance = persona.get("appearance", {})
    for key in ("hair", "eyes", "skin", "style"):
        val = appearance.get(key, "")
        if val:
            parts.append(val)

    parts.append(scene_context)

    if extra:
        parts.append(extra)

    parts.append(UGC_POSITIVE_SUFFIX)

    return ", ".join(p.strip(" ,") for p in parts if p.strip())


def build_ugc_negative(persona: dict) -> str:
    """Assemble the full negative prompt for a UGC scene image."""
    base_neg = persona.get("image_gen", {}).get("base_negative", "")
    parts = [base_neg, UGC_NEGATIVE_SUFFIX]
    return ", ".join(p.strip(" ,") for p in parts if p.strip())


def get_scene_contexts(style: str, count: int = 4) -> list[str]:
    """Return scene context strings for the given style."""
    import random
    pool = STYLE_SCENE_CONTEXTS.get(style, STYLE_SCENE_CONTEXTS["ugc"])
    if count >= len(pool):
        return pool[:]
    return random.sample(pool, count)
