"""UGC image prompt templates — shot-aware, social-native."""
from __future__ import annotations


# ── Core UGC photography suffixes ─────────────────────────────────────────────

UGC_POSITIVE_SUFFIX = (
    "shot on iPhone 15 Pro, natural available light, slightly overexposed, "
    "handheld camera feel, f/1.8 shallow depth of field, imperfect framing, "
    "candid moment, authentic, raw unedited look, real person not model"
)

UGC_NEGATIVE_SUFFIX = (
    "studio lighting, professional photography, stock photo, perfect symmetry, "
    "airbrushed skin, color graded, branded overlay, lower third, watermark, "
    "advertisement, corporate, plastic skin, hyperrealistic CGI render, "
    "uniform studio background, beauty dish, softbox, beauty retouching"
)

# ── Framing-specific positive prompt additions ─────────────────────────────────

FRAMING_PROMPTS = {
    "extreme_close_up":  "extreme close-up selfie, face fills frame, chin to forehead, intense eye contact",
    "close_up":          "close-up portrait, head and shoulders, face prominent, soft background bokeh",
    "medium_shot":       "medium shot, waist up, arms visible, natural gesture",
    "three_quarter":     "three-quarter shot, knees up, full outfit visible, casual pose",
    "full_body":         "full body shot, head to toe, casual natural posture",
}

# ── Shot-type specific prompt additions ───────────────────────────────────────

SHOT_TYPE_PROMPTS = {
    "hook_selfie":      "direct camera eye contact, intense or conspiratorial expression, face filling frame",
    "reaction_close":   "genuine surprised or realizing expression, candid reaction shot",
    "mirror_selfie":    "bathroom or bedroom mirror selfie, phone visible at edge, full or three-quarter body",
    "casual_env":       "person naturally in their environment, relaxed, unposed candid moment",
    "lean_in":          "person leaning toward camera, close and personal, lowered voice energy",
    "side_glance":      "three-quarter or side view, caught in thought, glancing sideways",
    "walking_outdoor":  "person in motion outdoors, natural movement implied, urban or park setting",
    "product_moment":   "person holding or gesturing toward something, natural show-and-tell energy",
    "reframe_cut":      "slightly different angle than previous, handheld reframe feel",
    "outro_cta":        "direct warm eye contact, slight genuine smile, end-of-video relaxed energy",
}

# ── Legacy: style-based scene contexts (kept for backward compat) ──────────────

STYLE_SCENE_CONTEXTS = {
    "ugc": [
        "sitting on couch at home, casual living room, warm ambient light",
        "standing in kitchen, morning light through window, coffee in hand",
        "at minimal desk, laptop glow, focused home office vibe",
        "lying on bed, phone above face, casual bedroom light",
        "outdoor café table, golden hour light, latte on table",
        "car front seat parked, natural daylight through windshield",
        "mirror selfie in bedroom, casual outfit visible",
        "couch, feet up, cosy evening, warm lamp",
    ],
    "selfie": [
        "holding phone slightly above eye level, arm edge just visible",
        "mirror selfie, bathroom, casual morning",
        "outdoor selfie, city background bokeh",
        "in car, parked, natural daylight windshield light",
        "lying on side, phone above face, bedroom",
    ],
    "casual-vlog": [
        "wide shot, person in room, camera on desk or shelf",
        "outdoor, person walking toward camera, natural city background",
        "coffee shop window, bokeh warm lights behind",
        "park bench, greenery blurred background",
        "home office desk, books and plants behind, daylight",
    ],
}


def build_ugc_positive(
    persona: dict,
    scene_context: str,
    shot_type: str = "",
    framing: str = "",
    extra: str = "",
) -> str:
    """Assemble the full positive prompt for a UGC scene image."""
    parts: list[str] = []

    # Persona LoRA trigger word
    trigger = persona.get("trigger_word", "")
    if trigger:
        parts.append(trigger)

    # Persona appearance
    appearance = persona.get("appearance", {})
    for key in ("hair", "eyes", "skin", "style"):
        val = appearance.get(key, "")
        if val:
            parts.append(val)

    # Persona image base (first 3 meaningful terms, skip trigger duplication)
    base_pos = persona.get("image_gen", {}).get("base_positive", "")
    if base_pos:
        base_parts = [p.strip() for p in base_pos.split(",") if p.strip() != trigger]
        parts.extend(base_parts[:3])

    # Shot-type specific modifier
    if shot_type and shot_type in SHOT_TYPE_PROMPTS:
        parts.append(SHOT_TYPE_PROMPTS[shot_type])

    # Framing
    if framing and framing in FRAMING_PROMPTS:
        parts.append(FRAMING_PROMPTS[framing])

    # Scene context (environment, emotion, camera note)
    parts.append(scene_context)

    if extra:
        parts.append(extra)

    parts.append(UGC_POSITIVE_SUFFIX)

    return ", ".join(p.strip(" ,") for p in parts if p.strip())


def build_ugc_positive_from_spec(persona: dict, shot_spec) -> str:
    """Build positive prompt directly from a ShotSpec object.

    Uses the full rich spec (environment, emotion, camera_note) for more
    contextually accurate images vs. just the scene_context string.
    """
    shot_type_str = shot_spec.shot_type.value if hasattr(shot_spec.shot_type, "value") else str(shot_spec.shot_type)

    # Build a richer scene context from all spec fields
    context_parts = []
    if shot_spec.environment:
        context_parts.append(shot_spec.environment)
    if shot_spec.emotion:
        context_parts.append(shot_spec.emotion)
    if shot_spec.camera_note:
        context_parts.append(shot_spec.camera_note)
    if not context_parts and shot_spec.scene_context:
        context_parts.append(shot_spec.scene_context)
    rich_context = ", ".join(context_parts)

    # Framing: try enum value first, fallback to the raw string as extra detail
    framing_key = shot_spec.framing.value if hasattr(shot_spec.framing, "value") else str(shot_spec.framing)
    framing_lookup = framing_key if framing_key in FRAMING_PROMPTS else ""
    extra = framing_key if framing_key not in FRAMING_PROMPTS else ""

    return build_ugc_positive(
        persona=persona,
        scene_context=rich_context,
        shot_type=shot_type_str,
        framing=framing_lookup,
        extra=extra,
    )


def build_ugc_negative(persona: dict) -> str:
    """Assemble the full negative prompt."""
    base_neg = persona.get("image_gen", {}).get("base_negative", "")
    parts = [base_neg, UGC_NEGATIVE_SUFFIX]
    return ", ".join(p.strip(" ,") for p in parts if p.strip())


def get_scene_contexts(style: str, count: int = 4) -> list[str]:
    """Return scene context strings (legacy API — scene_planner preferred)."""
    import random
    pool = STYLE_SCENE_CONTEXTS.get(style, STYLE_SCENE_CONTEXTS["ugc"])
    if count >= len(pool):
        result = []
        while len(result) < count:
            result.extend(pool)
        return result[:count]
    return random.sample(pool, count)
