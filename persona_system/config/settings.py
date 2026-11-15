"""
persona_system/config/settings.py
Central configuration loader — reads .env at the project root.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class PersonaSystemSettings(BaseSettings):
    # ── Persona ────────────────────────────────────────────────────────────
    active_persona: str = "default"
    personas_dir: Path = Path(__file__).resolve().parents[2] / "persona_system" / "config" / "personas"

    # ── Media storage ──────────────────────────────────────────────────────
    media_root: Path = Path(__file__).resolve().parents[1] / "media"

    # ── AI providers ───────────────────────────────────────────────────────
    openai_api_key: str = ""
    # Local Ollama / Qwen endpoint
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # ── Voicebox (local TTS — reuses existing config) ──────────────────────
    voicebox_url: str = "http://localhost:17493"
    voicebox_engine: str = "kokoro"
    voicebox_profile_id: str = ""

    # ── Stable Diffusion (local ComfyUI / AUTOMATIC1111) ───────────────────
    sd_base_url: str = "http://localhost:7860"   # A1111 API
    sd_lora_trigger: str = "mypersona"
    sd_default_steps: int = 30
    sd_default_cfg: float = 7.0

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── Database ───────────────────────────────────────────────────────────
    persona_db_url: str = "sqlite:///./persona_system/data/persona.db"

    # ── Instagram ──────────────────────────────────────────────────────────
    instagram_access_token: str = ""
    instagram_user_id: str = ""

    # ── OnlyFans (browser automation) ─────────────────────────────────────
    onlyfans_email: str = ""
    onlyfans_password: str = ""
    onlyfans_cookies_file: str = "./persona_system/data/of_cookies.json"

    # ── Scoring thresholds ─────────────────────────────────────────────────
    content_score_min: float = 0.65   # only generate video for scripts ≥ this
    image_score_min: float = 0.60

    # ── Existing ai-shorts system ──────────────────────────────────────────
    ai_shorts_api_url: str = "http://localhost:8000"
    ai_shorts_api_key: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = PersonaSystemSettings()

# Ensure media dirs exist
for _sub in ["images", "videos", "audio"]:
    (settings.media_root / _sub).mkdir(parents=True, exist_ok=True)

Path("./persona_system/data").mkdir(parents=True, exist_ok=True)
