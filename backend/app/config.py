import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_deployment_mini: str = "gpt-4o-mini"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    # Azure TTS (separate resource/endpoint for gpt-4o-mini-tts)
    azure_openai_tts_endpoint: str = ""
    azure_openai_tts_api_key: str = ""  # leave blank to reuse AZURE_OPENAI_API_KEY
    azure_openai_tts_api_version: str = "2025-03-01-preview"
    azure_openai_tts_deployment: str = "gpt-4o-mini-tts"
    azure_openai_tts_voice: str = "alloy"

    # Maya1 (open-source 3B TTS — https://maya1.org)
    maya_voice_description: str = (
        "A young woman in her late 20s with an American accent. "
        "Warm, expressive, conversational tone with clear diction."
    )
    maya_device: str = "auto"   # auto | cuda | mps | cpu
    maya_model_id: str = "maya-research/maya1"
    # Named voice profiles — editable from the Settings UI
    maya_profile_aditya: str = (
        "Indian-American male in his mid-20s, Aditya Shah. "
        "Confident, articulate, natural tech-creator delivery. "
        "Clear diction, moderate-to-fast pace, warm baritone with genuine enthusiasm. "
        "Sounds like a knowledgeable friend talking directly to camera."
    )
    maya_profile_of_model: str = (
        "Young American woman in her early 20s. Warm, intimate, slightly breathy voice "
        "with playful, teasing intonation. Confident and expressive, speaks with a "
        "natural flirty cadence — engaging and suggestive without being over the top."
    )

    # Provider selection
    ai_provider: str = "azure_openai"  # azure_openai | openai
    voice_provider: str = "maya"  # maya | azure_openai | azure_speech | openai | elevenlabs | mai_voice | voicebox | kokoro

    # Voicebox (local TTS — http://github.com/jamiepine/voicebox)
    voicebox_url: str = "http://localhost:17493"  # desktop app default; use http://voicebox:17493 in Docker Compose
    voicebox_profile_id: str = ""  # leave blank to auto-create on first call
    voicebox_engine: str = "qwen"  # qwen | kokoro | chatterbox | chatterbox_turbo | qwen_custom_voice | luxtts | tada

    # Kokoro (direct native TTS via kokoro-onnx — no extra container needed)
    kokoro_model_dir: str = "/app/data/kokoro"  # models downloaded here on first use
    kokoro_voice: str = "af_sky"  # Kokoro voice ID (af_sky, af_nova, am_michael, ...)

    # Azure AI Speech (Cognitive Services Neural TTS — same resource as Azure OpenAI)
    azure_speech_region: str = "eastus"
    azure_speech_api_key: str = ""  # leave blank to reuse AZURE_OPENAI_API_KEY
    azure_speech_voice: str = "en-US-JennyNeural"

    # OpenAI fallback
    openai_api_key: str = ""

    # ElevenLabs
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"

    # MAI-Voice-1 (Azure AI Foundry model)
    mai_voice_endpoint: str = ""
    mai_voice_api_key: str = ""
    mai_voice_name: str = "MAI-Voice-1"

    # Pexels (stock footage)
    pexels_api_key: str = ""

    # D-ID (talking head avatar videos)
    did_api_key: str = ""  # format: username:password from studio.d-id.com

    # Avatar provider: did | replicate  (replicate = SadTalker, free tier, no watermark)
    avatar_provider: str = "did"  # set to "replicate" to switch
    replicate_api_key: str = ""  # r8_xxxx from replicate.com
    replicate_sadtalker_version: str = "a519cc0cfebaaeade068b23899165a11ec76aaa1d2b313d40d214f204ec957a3"

    # App
    app_name: str = "AI Shorts System"
    debug: bool = False
    secret_key: str = "change-me-in-production"

    # Database — absolute path avoids CWD-relative issues when pipeline runs in background tasks
    database_url: str = f"sqlite:///{Path(__file__).parent.parent}/data/ai_shorts.db"

    # Paths — default to /app/data (Docker), but can be overridden via env
    data_dir: Path = Path("/app/data")
    output_dir: Path = Path("/app/data/output")
    temp_dir: Path = Path("/app/data/temp")
    assets_dir: Path = Path("/app/data/assets")

    # n8n
    n8n_webhook_url: str = ""

    # UGC Video Engine
    ugc_mode: str = "free"
    ugc_script_provider: str = ""
    ugc_image_provider: str = ""
    ugc_voice_provider: str = ""
    ugc_head_provider: str = ""
    ugc_assembly_provider: str = "ffmpeg"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    comfyui_base_url: str = "http://localhost:8188"
    comfyui_workflow_path: str = ""
    ugc_sadtalker_cli_path: str = ""
    ugc_approval_mode: str = "webhook"
    ugc_approval_webhook_url: str = ""
    ugc_output_dir: Path = Path("/app/data/output/ugc-videos")
    ugc_temp_dir: Path = Path("/app/data/temp/ugc-jobs")
    sd_base_url: str = "http://localhost:7860"

    # SVD (Stable Video Diffusion — free local via A1111 extension)
    svd_base_url: str = "http://localhost:7860"
    svd_motion_bucket_id: int = 127
    svd_augmentation_level: float = 0.02
    svd_num_frames: int = 14
    svd_fps: int = 6

    # Kling AI (paid image-to-video — leave blank to skip, falls back to free)
    kling_api_key: str = ""
    kling_api_secret: str = ""
    kling_model: str = "kling-v1-5"
    kling_clip_duration: int = 5
    kling_cfg_scale: float = 0.5

    # LivePortrait (free local portrait animation — install separately)
    liveportrait_cli_path: str = ""   # e.g. /path/to/LivePortrait/inference.py
    liveportrait_device: str = "mps"  # mps (Apple Silicon) | cuda | cpu

    # LatentSync (free local lipsync)
    latentsync_dir: str = ""
    latentsync_checkpoint: str = "latentsync_unet.pt"
    latentsync_whisper_ckpt: str = "whisper/tiny.pt"

    # Hedra (paid lipsync — leave blank to skip)
    hedra_api_key: str = ""
    hedra_aspect_ratio: str = "9:16"

    # sync.so lipsync (paid, cheap — ~$0.01/video, free tier available)
    syncso_api_key: str = ""  # sk-xxx from sync.so dashboard

    # Wav2Lip (free local lipsync — needs one-time install)
    wav2lip_dir: str = ""  # /path/to/Wav2Lip repo

    # Lipsync provider selection
    lipsync_provider: str = "latentsync"  # latentsync | syncso | replicate_latentsync | hedra | wav2lip

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    allowed_email: str = "ethicaladitya@gmail.com"   # kept for backward-compat
    super_admin_email: str = "ethicaladitya@gmail.com"  # always has full access + admin panel
    base_url: str = "https://video.theadityashah.com"

    # Credits & Billing
    signup_free_credits: int = 1          # credits granted on first Google OAuth login
    credit_cost_per_video: int = 1        # credits consumed per video generation
    # Instamojo (https://www.instamojo.com/developers/)
    instamojo_api_key: str = ""
    instamojo_auth_token: str = ""
    instamojo_base_url: str = "https://www.instamojo.com/api/1.1"  # use https://test.instamojo.com for sandbox

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

# Ensure directories exist
for d in [settings.data_dir, settings.output_dir, settings.temp_dir, settings.assets_dir,
          settings.ugc_output_dir, settings.ugc_temp_dir]:
    d.mkdir(parents=True, exist_ok=True)
