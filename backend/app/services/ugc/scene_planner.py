"""
Scene Planner — splits a UGC script into a shot list.

This is the most important module in the pipeline. Instead of generating
4 identical-vibe images, this plans 8-15 distinct shots that vary:
  - framing (close-up, medium, OTS, selfie, mirror, wide)
  - environment (bedroom, kitchen, outdoors, cafe, car, street)
  - emotion (neutral, laughing, serious, curious, conspiratorial)
  - camera angle (eye level, slightly high, phone-level)
  - motion type (breathing, walking, reframing, zoom-pull)

The planner thinks like a TikTok editor, not a slideshow renderer.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ShotType(str, Enum):
    HOOK_SELFIE       = "hook_selfie"        # direct camera, face filling frame — hook moment
    REACTION_CLOSE    = "reaction_close"     # tight close-up, emotional reaction
    MIRROR_SELFIE     = "mirror_selfie"      # phone visible, full-body or 3/4
    CASUAL_ENV        = "casual_env"         # environment-anchored, person in context
    LEAN_IN           = "lean_in"            # slightly closer to camera, conspiratorial
    SIDE_GLANCE       = "side_glance"        # profile or 3/4 view, candid look
    WALKING_OUTDOOR   = "walking_outdoor"    # dynamic, movement implied in frame
    PRODUCT_MOMENT    = "product_moment"     # shows something in hand/on screen
    REFRAME_CUT       = "reframe_cut"        # slightly different angle from prior, fast cut
    OUTRO_CTA         = "outro_cta"          # direct gaze, slight smile, end card energy


class FramingStyle(str, Enum):
    EXTREME_CLOSE  = "extreme_close_up"   # eyes to chin
    CLOSE          = "close_up"           # head and shoulders
    MEDIUM         = "medium_shot"        # waist up
    THREE_QUARTER  = "three_quarter"      # knees up
    FULL           = "full_body"


class MotionType(str, Enum):
    BREATHING      = "slow_breathing"         # subtle life, minimal
    LEAN_CLOSER    = "lean_toward_camera"     # creates intimacy
    HEAD_TILT      = "natural_head_tilt"      # curiosity/thinking
    LOOK_UP        = "look_up_then_back"      # authentic thought recall
    LAUGH          = "soft_laugh_expression"  # relatable moment
    WALK_TOWARD    = "walk_toward_camera"     # dynamic, retention hook
    PUSH_ZOOM      = "slow_push_in_zoom"      # Ken-Burns zooming in fast
    PULL_ZOOM      = "slow_pull_out_zoom"     # Ken-Burns pulling out
    STATIC_DRIFT   = "static_with_drift"      # barely moves, life in eyes
    REFRAME        = "camera_reframe"         # slight swipe/reframe cut


@dataclass
class ShotSpec:
    """A single planned shot in the video."""
    index: int
    shot_type: ShotType
    framing: FramingStyle
    environment: str           # e.g. "bedroom morning light", "coffee shop window"
    emotion: str               # e.g. "curious", "laughing", "direct serious"
    camera_note: str           # e.g. "slight high angle, phone held above"
    motion_type: MotionType
    motion_prompt: str         # exact motion description for Kling/SVD
    scene_context: str         # SD image prompt context
    text_hint: str             # what script segment roughly maps here
    duration_hint: float       # target duration in seconds (1.5–3.0)
    is_talking_head: bool      # True = needs lipsync; False = cutaway/b-roll


# ─── Environment pools (varied, social-native) ────────────────────────────────

_INDOOR_ENVS = [
    "bedroom morning light, white sheets slightly rumpled",
    "bedroom late evening, warm desk lamp glow, cosy",
    "kitchen counter, morning light through window",
    "living room couch, casual pillows, warm ambient light",
    "bathroom mirror, morning, casual outfit",
    "home desk setup, laptop glow, dark room",
    "sitting on floor against bed, leaning back",
    "kitchen table, coffee mug in foreground",
]

_OUTDOOR_ENVS = [
    "outdoor urban street, buildings blurred behind",
    "outside coffee shop, warm afternoon light",
    "city park bench, greenery softly bokeh'd",
    "rooftop, city skyline background",
    "parking lot, golden hour light on face",
    "walking on sidewalk, motion-implied framing",
]

_TRANSITIONAL_ENVS = [
    "car, parked, natural daylight through windshield",
    "elevator or hallway, casual",
    "standing in doorway, backlit",
]

_ALL_ENVS = _INDOOR_ENVS + _OUTDOOR_ENVS + _TRANSITIONAL_ENVS

_CAMERA_NOTES = [
    "phone held at eye level, slight handheld sway",
    "phone slightly above eye level, natural selfie angle",
    "phone at chest level, looking down slightly at lens",
    "placed on surface, person close to camera, tripod-like but slightly imperfect",
    "arm extended, wide selfie angle",
    "slight dutch tilt, casual imperfect",
]

_EMOTIONS = {
    ShotType.HOOK_SELFIE:    ["direct and slightly intense", "curious and pulled-in", "conspiratorial half-smile"],
    ShotType.REACTION_CLOSE: ["genuine surprise", "slow realization", "suppressed laugh", "eyes widened"],
    ShotType.MIRROR_SELFIE:  ["casual confidence", "checking herself out, unbothered"],
    ShotType.CASUAL_ENV:     ["relaxed, in her world", "distracted then notices camera"],
    ShotType.LEAN_IN:        ["lowering voice to share something", "slightly playful"],
    ShotType.SIDE_GLANCE:    ["caught in thought", "glances sideways then back"],
    ShotType.WALKING_OUTDOOR:["light energy, positive", "pre-occupied but vibrant"],
    ShotType.PRODUCT_MOMENT: ["showing something with genuine interest", "pointing or gesturing"],
    ShotType.REFRAME_CUT:    ["continuous from prior shot, slightly different angle"],
    ShotType.OUTRO_CTA:      ["direct, warm, slight smile", "self-aware sign-off energy"],
}

_MOTION_PROMPTS = {
    MotionType.BREATHING:    "slow natural breathing, subtle chest rise, soft natural blinking, hair barely moves",
    MotionType.LEAN_CLOSER:  "person leans slightly toward camera, slow movement, hair falls forward",
    MotionType.HEAD_TILT:    "head tilts slightly to one side, thoughtful expression, eyes move",
    MotionType.LOOK_UP:      "eyes glance up briefly as if recalling something, then back to camera",
    MotionType.LAUGH:        "soft exhale laugh, head dips slightly, genuine smile forms",
    MotionType.WALK_TOWARD:  "person walks slowly toward camera, background shifts, handheld feel",
    MotionType.PUSH_ZOOM:    "very slow push-in zoom, camera creeps closer, subject stays still",
    MotionType.PULL_ZOOM:    "very slow pull-out zoom, revealing more of environment",
    MotionType.STATIC_DRIFT: "almost still, very subtle drift, only eyes and breathing visible",
    MotionType.REFRAME:      "quick camera reframe, slight jerk then stabilize, handheld imperfect",
}

# ─── Shot type → framing mapping ──────────────────────────────────────────────

_DEFAULT_FRAMING = {
    ShotType.HOOK_SELFIE:     FramingStyle.CLOSE,
    ShotType.REACTION_CLOSE:  FramingStyle.EXTREME_CLOSE,
    ShotType.MIRROR_SELFIE:   FramingStyle.THREE_QUARTER,
    ShotType.CASUAL_ENV:      FramingStyle.MEDIUM,
    ShotType.LEAN_IN:         FramingStyle.CLOSE,
    ShotType.SIDE_GLANCE:     FramingStyle.MEDIUM,
    ShotType.WALKING_OUTDOOR: FramingStyle.MEDIUM,
    ShotType.PRODUCT_MOMENT:  FramingStyle.MEDIUM,
    ShotType.REFRAME_CUT:     FramingStyle.CLOSE,
    ShotType.OUTRO_CTA:       FramingStyle.CLOSE,
}

_FRAMING_TO_SD_DESC = {
    FramingStyle.EXTREME_CLOSE: "extreme close-up, face filling frame, chin to forehead",
    FramingStyle.CLOSE:         "close-up portrait, head and shoulders, face prominent",
    FramingStyle.MEDIUM:        "medium shot, waist up, arms visible",
    FramingStyle.THREE_QUARTER: "three-quarter shot, knees up, full outfit visible",
    FramingStyle.FULL:          "full body shot, head to toe",
}


def _build_scene_context(shot: ShotType, framing: FramingStyle, env: str, emotion: str, camera: str) -> str:
    """Build a Stable Diffusion scene context string for this shot."""
    framing_desc = _FRAMING_TO_SD_DESC[framing]
    return f"{framing_desc}, {emotion}, {env}, {camera}"


# ─── Shot sequence templates per duration ─────────────────────────────────────

def _shot_sequence_for_duration(duration: int) -> list[ShotType]:
    """Return a shot type sequence appropriate for the target duration."""
    if duration <= 15:
        return [
            ShotType.HOOK_SELFIE,
            ShotType.REACTION_CLOSE,
            ShotType.LEAN_IN,
            ShotType.OUTRO_CTA,
        ]
    elif duration <= 30:
        return [
            ShotType.HOOK_SELFIE,
            ShotType.REACTION_CLOSE,
            ShotType.CASUAL_ENV,
            ShotType.LEAN_IN,
            ShotType.REFRAME_CUT,
            ShotType.SIDE_GLANCE,
            ShotType.OUTRO_CTA,
        ]
    else:  # 60s
        return [
            ShotType.HOOK_SELFIE,
            ShotType.REACTION_CLOSE,
            ShotType.CASUAL_ENV,
            ShotType.LEAN_IN,
            ShotType.MIRROR_SELFIE,
            ShotType.REFRAME_CUT,
            ShotType.SIDE_GLANCE,
            ShotType.CASUAL_ENV,
            ShotType.REACTION_CLOSE,
            ShotType.LEAN_IN,
            ShotType.OUTRO_CTA,
        ]


def _shot_durations(shot_types: list[ShotType], total_duration: float) -> list[float]:
    """
    Assign shot durations to hit total_duration.
    Strategy:
    - Hook shot: 2.0–3.0s (long enough to establish but fast enough to not lose viewer)
    - Reaction/close-up: 1.5–2.0s
    - Environment/wide: 2.0–3.0s
    - Lean-in/conspiratorial: 2.0–2.5s
    - CTA: 2.5–4.0s
    """
    weights = {
        ShotType.HOOK_SELFIE:     2.5,
        ShotType.REACTION_CLOSE:  1.5,
        ShotType.CASUAL_ENV:      2.5,
        ShotType.LEAN_IN:         2.0,
        ShotType.MIRROR_SELFIE:   2.5,
        ShotType.REFRAME_CUT:     1.5,
        ShotType.SIDE_GLANCE:     2.0,
        ShotType.WALKING_OUTDOOR: 2.5,
        ShotType.PRODUCT_MOMENT:  2.0,
        ShotType.OUTRO_CTA:       3.0,
    }
    raw = [weights.get(s, 2.0) for s in shot_types]
    raw_total = sum(raw)
    scale = total_duration / raw_total
    return [round(r * scale, 2) for r in raw]


def plan_shots(
    script_raw: str,
    persona: dict,
    duration_target: int = 30,
    style: str = "ugc",
    seed: int = 0,
) -> list[ShotSpec]:
    """
    Plan a shot list for the given script and persona.

    Returns a list of ShotSpec objects. Each spec drives:
    - Image generation (scene_context → SD prompt)
    - Motion generation (motion_prompt → Kling/SVD)
    - Video assembly (duration_hint → cut timing)
    """
    rng = random.Random(seed)

    shot_types = _shot_sequence_for_duration(duration_target)
    total_secs = float(duration_target)
    durations = _shot_durations(shot_types, total_secs)

    # Environments: avoid repeating the same one twice in a row
    env_pool = _get_persona_envs(persona) or list(_ALL_ENVS)
    rng.shuffle(env_pool)
    envs = []
    last_env = ""
    env_idx = 0
    for _ in shot_types:
        # Pick next env that isn't the same as last
        for attempt in range(len(env_pool)):
            candidate = env_pool[env_idx % len(env_pool)]
            env_idx += 1
            if candidate != last_env:
                envs.append(candidate)
                last_env = candidate
                break
        else:
            envs.append(env_pool[env_idx % len(env_pool)])

    # Script segments: split script roughly into equal-word-count chunks per shot
    words = script_raw.split()
    words_per_shot = max(1, len(words) // len(shot_types))

    shots: list[ShotSpec] = []
    for i, (shot_type, dur, env) in enumerate(zip(shot_types, durations, envs)):
        framing = _DEFAULT_FRAMING[shot_type]
        emotion_pool = _EMOTIONS.get(shot_type, ["natural, looking at camera"])
        emotion = rng.choice(emotion_pool)
        camera = rng.choice(_CAMERA_NOTES)
        motion_type = _pick_motion(shot_type, rng)

        # Text hint: this shot roughly covers these script words
        start_word = i * words_per_shot
        end_word = min(start_word + words_per_shot, len(words))
        text_hint = " ".join(words[start_word:end_word])

        scene_context = _build_scene_context(shot_type, framing, env, emotion, camera)

        shots.append(ShotSpec(
            index=i,
            shot_type=shot_type,
            framing=framing,
            environment=env,
            emotion=emotion,
            camera_note=camera,
            motion_type=motion_type,
            motion_prompt=_MOTION_PROMPTS[motion_type],
            scene_context=scene_context,
            text_hint=text_hint,
            duration_hint=dur,
            is_talking_head=(shot_type not in (ShotType.WALKING_OUTDOOR, ShotType.CASUAL_ENV)),
        ))

    return shots


def _get_persona_envs(persona: dict) -> list[str]:
    """Extract environment descriptions from persona activity_scenes."""
    scenes = persona.get("activity_scenes", {})
    envs: list[str] = []
    for pillar_scenes in scenes.values():
        for scene_entry in pillar_scenes:
            scene_desc = scene_entry.get("scene", "")
            if scene_desc:
                envs.append(scene_desc)
    return envs


def _pick_motion(shot_type: ShotType, rng: random.Random) -> MotionType:
    """Pick the most appropriate motion type for this shot type."""
    motion_map: dict[ShotType, list[MotionType]] = {
        ShotType.HOOK_SELFIE:     [MotionType.LEAN_CLOSER, MotionType.STATIC_DRIFT, MotionType.PUSH_ZOOM],
        ShotType.REACTION_CLOSE:  [MotionType.LOOK_UP, MotionType.LAUGH, MotionType.HEAD_TILT],
        ShotType.MIRROR_SELFIE:   [MotionType.BREATHING, MotionType.STATIC_DRIFT],
        ShotType.CASUAL_ENV:      [MotionType.PULL_ZOOM, MotionType.BREATHING, MotionType.REFRAME],
        ShotType.LEAN_IN:         [MotionType.LEAN_CLOSER, MotionType.PUSH_ZOOM],
        ShotType.SIDE_GLANCE:     [MotionType.HEAD_TILT, MotionType.LOOK_UP],
        ShotType.WALKING_OUTDOOR: [MotionType.WALK_TOWARD],
        ShotType.PRODUCT_MOMENT:  [MotionType.BREATHING, MotionType.STATIC_DRIFT],
        ShotType.REFRAME_CUT:     [MotionType.REFRAME, MotionType.PUSH_ZOOM],
        ShotType.OUTRO_CTA:       [MotionType.LEAN_CLOSER, MotionType.STATIC_DRIFT],
    }
    options = motion_map.get(shot_type, [MotionType.BREATHING])
    return rng.choice(options)
