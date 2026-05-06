"""Platform presets and UGC mode → provider mappings."""
from __future__ import annotations

# 9:16 vertical for all short-form platforms
PLATFORM_PRESETS: dict[str, dict] = {
    "tiktok": {"width": 1080, "height": 1920, "fps": 30, "max_duration": 60},
    "reels":  {"width": 1080, "height": 1920, "fps": 30, "max_duration": 90},
    "shorts": {"width": 1080, "height": 1920, "fps": 30, "max_duration": 60},
}

# Maximum word count per duration target
DURATION_WORD_LIMITS: dict[int, int] = {15: 75, 30: 150, 60: 300}

# UGC_MODE preset → provider name mappings
UGC_MODE_PRESETS: dict[str, dict[str, str]] = {
    "free": {
        "script": "ollama",
        "image":  "a1111",
        "voice":  "kokoro",
        "head":   "sadtalker",
    },
    "balanced": {
        "script": "ollama",
        "image":  "azure_gpt_image",
        "voice":  "kokoro",
        "head":   "sadtalker",
    },
    "quality": {
        "script": "azure_openai",
        "image":  "azure_gpt_image",
        "voice":  "elevenlabs",
        "head":   "did",
    },
}

# Providers that cost $0
LOCAL_PROVIDERS: frozenset[str] = frozenset(
    {"ollama", "a1111", "comfyui", "kokoro", "sadtalker", "slideshow", "ffmpeg"}
)

# Rough per-video cost estimates for paid providers (USD)
PROVIDER_COST_ESTIMATES: dict[str, float] = {
    "azure_openai":    0.01,
    "azure_gpt_image": 0.04,
    "elevenlabs":      0.03,
    "did":             0.10,
    "replicate":       0.07,
    "heygen":          0.25,
}


def resolve_providers(settings) -> dict[str, str]:
    """Resolve final provider names from UGC_MODE + individual overrides."""
    preset = UGC_MODE_PRESETS.get(settings.ugc_mode, UGC_MODE_PRESETS["free"])
    return {
        "script": settings.ugc_script_provider or preset["script"],
        "image":  settings.ugc_image_provider  or preset["image"],
        "voice":  settings.ugc_voice_provider  or preset["voice"],
        "head":   settings.ugc_head_provider   or preset["head"],
    }


def estimate_cost(providers: dict[str, str]) -> float:
    """Return total estimated cost in USD for a single video job."""
    return sum(PROVIDER_COST_ESTIMATES.get(p, 0.0) for p in providers.values())
