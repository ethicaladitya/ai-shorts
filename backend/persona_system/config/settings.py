"""
backend/persona_system/config/settings.py
Reads from the SAME .env as the main ai-shorts backend — no duplicate config.
"""
from __future__ import annotations
from pathlib import Path
from pydantic_settings import BaseSettings


class PersonaSystemSettings(BaseSettings):
    # ── Active persona ─────────────────────────────────────────────────────
    active_persona: str = "default"
    personas_dir: Path = Path(__file__).resolve().parents[1] / "config" / "personas"

    # ── Media storage ──────────────────────────────────────────────────────
    project_root: Path = Path(__file__).resolve().parents[3]
    media_root: Path = Path(__file__).resolve().parents[3] / "backend" / "data" / "persona_media"

    # ── AI — reuse existing ai-shorts keys ────────────────────────────────
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_deployment_mini: str = "gpt-4o-mini"
    azure_openai_dalle_deployment: str = "gpt-image-2"
    azure_openai_grok_deployment: str = "grok-3"

    # --- GPT-Image-2 dedicated resource (Sweden Central) ---
    azure_gpt_image_2_endpoint: str = ""
    azure_gpt_image_2_deployment: str = "gpt-image-2"
    azure_gpt_image_2_api_key: str = ""
    azure_openai_gpt_image_2_api_key: str = ""  # alias for user-provided var name

    openai_api_key: str = ""

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    voice_provider: str = "azure_openai"
    voicebox_url: str = "http://localhost:17493"
    voicebox_engine: str = "qwen"
    voicebox_profile_id: str = ""

    # ── Azure TTS fallback ────────────────────────────────────────────────
    azure_openai_tts_endpoint: str = ""
    azure_openai_tts_api_key: str = ""
    azure_openai_tts_api_version: str = "2025-03-01-preview"
    azure_openai_tts_deployment: str = "gpt-4o-mini-tts"
    azure_openai_tts_voice: str = "alloy"

    # ── D-ID / Replicate ──────────────────────────────────────────────────
    did_api_key: str = ""
    avatar_provider: str = "did"
    replicate_api_key: str = ""

    # ── ElevenLabs ────────────────────────────────────────────────────────
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "cgSgspJ2msm6clMCkdW9"

    # ── Stable Diffusion ──────────────────────────────────────────────────
    sd_base_url: str = "http://localhost:7860"
    sd_lora_trigger: str = "persona_v1"
    sd_default_steps: int = 28
    sd_default_cfg: float = 7.0

    # ── ═══════════════════════════════════════════════════════════════════
    #    VIDEO CLIP PROVIDER (animated clip generation)
    #    "svd"  — Stable Video Diffusion (local, free, slow)
    #    "kling" — Kling AI API (cloud, ~$0.14/video, fast)
    # ─────────────────────────────────────────────────────────────────────
    video_clip_provider: str = "svd"       # default: local

    # SVD — via A1111 or ComfyUI
    svd_base_url: str = "http://localhost:7860"   # same A1111 instance
    svd_motion_bucket_id: int = 127               # 1-255, higher = more motion
    svd_augmentation_level: float = 0.02
    svd_num_frames: int = 25
    svd_fps: int = 8

    # Kling AI
    kling_api_key: str = ""
    kling_api_secret: str = ""
    kling_model: str = "kling-v1-5"        # or kling-v1
    kling_clip_duration: int = 5            # 5 or 10 seconds
    kling_cfg_scale: float = 0.5

    # ── ═══════════════════════════════════════════════════════════════════
    #    LIPSYNC PROVIDER
    #    "latentsync" — local (free, needs ~6GB, slow on Mac)
    #    "hedra"      — Hedra API (~$0.05/video, fast)
    #    "sadtalker"  — Replicate SadTalker (legacy)
    # ─────────────────────────────────────────────────────────────────────
    lipsync_provider: str = "latentsync"   # default: local

    # LatentSync
    latentsync_dir: str = ""               # path to cloned LatentSync repo
    latentsync_checkpoint: str = "latentsync_unet.pt"
    latentsync_whisper_ckpt: str = "whisper/tiny.pt"

    # Hedra
    hedra_api_key: str = ""
    hedra_aspect_ratio: str = "9:16"

    # ── Default video mode ─────────────────────────────────────────────────
    # loop | animated_loop | talking | animated_talking
    default_video_mode: str = "animated_talking"

    # ── Platform content settings ──────────────────────────────────────────
    ig_content_enabled: bool = True
    of_content_enabled: bool = True

    # ── Persona reference image ────────────────────────────────────────────
    # Base image used for gpt-image-2 scene edits. Same person in every video.
    persona_reference_image: str = ""

    # ── Instagram ─────────────────────────────────────────────────────────
    instagram_access_token: str = ""
    instagram_user_id: str = ""

    # ── OnlyFans ──────────────────────────────────────────────────────────
    onlyfans_email: str = ""
    onlyfans_password: str = ""
    onlyfans_cookies_file: str = str(Path(__file__).resolve().parents[3] / "backend" / "data" / "of_cookies.json")

    # ── ai-shorts internal API ────────────────────────────────────────────
    ai_shorts_api_url: str = "http://localhost:8000"
    ai_shorts_api_key: str = ""

    # ── Scoring thresholds ────────────────────────────────────────────────
    content_score_min: float = 0.60
    image_score_min: float = 0.55

    # ── Persona DB ────────────────────────────────────────────────────────
    persona_db_url: str = f"sqlite:///{Path(__file__).resolve().parents[3] / 'backend' / 'data' / 'persona.db'}"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = PersonaSystemSettings()

# Ensure dirs exist
for _d in [
    settings.media_root / "images",
    settings.media_root / "videos",
    settings.media_root / "audio",
    settings.media_root / "clips",
]:
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        import sys
        print(f"Warning: Could not create directory {_d}: {e}", file=sys.stderr)
