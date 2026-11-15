"""Platform presets and UGC mode → provider mappings."""
from __future__ import annotations

# 9:16 vertical for all short-form platforms
PLATFORM_PRESETS: dict[str, dict] = {
    "tiktok": {"width": 1080, "height": 1920, "fps": 30, "max_duration": 60},
    "reels":  {"width": 1080, "height": 1920, "fps": 30, "max_duration": 90},
    "shorts": {"width": 1080, "height": 1920, "fps": 30, "max_duration": 60},
}

DURATION_WORD_LIMITS: dict[int, int] = {15: 75, 30: 150, 60: 300}

# ── UGC_MODE preset → provider defaults ───────────────────────────────────────
#
# free     — 100% local, $0/video. Requires: Ollama + A1111 + Voicebox running.
#            Motion: LivePortrait (if installed) → enhanced Ken-Burns (always works)
#            Lipsync: LatentSync (if installed) → skipped
#
# balanced — Ollama script + GPT-Image-2 images + local voice + Ken-Burns motion.
#            Small image cost (~$0.04/video). Faster than full free stack.
#
# quality  — All cloud. Best output, highest cost.

UGC_MODE_PRESETS: dict[str, dict[str, str]] = {
    "free": {
        "script": "ollama",
        "image":  "a1111",
        "voice":  "maya:aditya",   # Maya local AI — free, expressive
        "head":   "kenburns",
    },
    "balanced": {
        "script": "ollama",
        "image":  "azure_gpt_image",
        "voice":  "maya:aditya",
        "head":   "syncso",
    },
    "quality": {
        "script": "azure_openai",
        "image":  "azure_gpt_image",
        "voice":  "elevenlabs",
        "head":   "kling",
    },
}

# Providers that cost $0 (used for cost display in UI)
LOCAL_PROVIDERS: frozenset[str] = frozenset({
    "ollama", "a1111", "comfyui", "kokoro", "voicebox",
    "maya", "maya:aditya", "maya:of_model",
    "sadtalker", "latentsync", "liveportrait",
    "kenburns", "slideshow", "ffmpeg",
})

# Rough per-video cost estimates for paid providers (USD)
PROVIDER_COST_ESTIMATES: dict[str, float] = {
    "azure_openai":          0.01,
    "azure_gpt_image":       0.04,
    "elevenlabs":            0.03,
    "did":                   0.10,
    "replicate":             0.07,
    "heygen":                0.25,
    "kling":                 0.14,
    "hedra":                 0.05,
    "syncso":                0.01,   # sync.so: ~$0.01/video (cheap!)
    "sync.so":               0.01,
    "replicate_latentsync":  0.01,   # Replicate LatentSync: ~$0.01/video
}


def resolve_providers(settings) -> dict[str, str]:
    """Resolve final provider names. Individual env overrides beat mode preset."""
    preset = UGC_MODE_PRESETS.get(settings.ugc_mode, UGC_MODE_PRESETS["free"])
    return {
        "script": settings.ugc_script_provider or preset["script"],
        "image":  settings.ugc_image_provider  or preset["image"],
        "voice":  settings.ugc_voice_provider  or preset["voice"],
        "head":   settings.ugc_head_provider   or preset["head"],
    }


def estimate_cost(providers: dict[str, str]) -> float:
    return sum(PROVIDER_COST_ESTIMATES.get(p, 0.0) for p in providers.values())
