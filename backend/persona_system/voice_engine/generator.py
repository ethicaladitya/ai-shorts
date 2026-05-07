# backend/persona_system/voice_engine/generator.py

import asyncio
import logging
import random
import yaml
import uuid
from pathlib import Path
from .preprocessor import VoicePreprocessor
from .postprocessor import AudioPostProcessor
from .scorer import AudioScorer
from persona_system.config.settings import settings

logger = logging.getLogger(__name__)

class PersonaVoiceGenerator:
    def __init__(self, persona_name: str = "default"):
        self.persona_name = persona_name
        self.config_path = Path(__file__).parent / "profiles.yaml"
        with open(self.config_path, "r") as f:
            self.config = yaml.safe_load(f)
        
        self.preprocessor = VoicePreprocessor()
        self.postprocessor = AudioPostProcessor()
        self.scorer = AudioScorer()
        self._provider = None

    def _get_provider(self):
        import os
        # Re-read every call so per-run overrides via os.environ work
        provider_name = os.environ.get("VOICE_PROVIDER", settings.voice_provider)

        # Only reuse the cached provider if the name hasn't changed
        if self._provider is not None and getattr(self._provider, '_provider_name', None) == provider_name:
            return self._provider

        from app.services.voice_provider import get_voice_provider
        self._provider = get_voice_provider(
            provider_name=provider_name,
            # Voicebox params
            voicebox_url=settings.voicebox_url,
            voicebox_engine=settings.voicebox_engine,
            voicebox_profile_id=settings.voicebox_profile_id,
            # Azure TTS params
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            tts_endpoint=settings.azure_openai_tts_endpoint,
            tts_api_key=settings.azure_openai_tts_api_key,
            tts_api_version=settings.azure_openai_tts_api_version,
            tts_deployment=settings.azure_openai_tts_deployment,
            tts_voice=settings.azure_openai_tts_voice,
            # Others
            elevenlabs_api_key=settings.elevenlabs_api_key,
            elevenlabs_voice_id=settings.elevenlabs_voice_id,
            openai_api_key=settings.openai_api_key,
        )
        self._provider._provider_name = provider_name
        return self._provider

    async def generate(
        self,
        text: str,
        output_path: Path,
        profile_name: str = "soft_feminine_v1",
        emotion: str = "neutral",
        variants: int = 1,   # kept for API compat but we only do 1 call
    ) -> Path:
        """
        Full expressive generation pipeline.
        """
        profile = self.config["profiles"].get(profile_name, self.config["profiles"]["soft_feminine_v1"])

        # 1. Pre-process Text
        processed_text = self.preprocessor.process(text, profile, emotion)
        logger.info(f"Generating voice with processed text: {processed_text[:120]}...")

        provider = self._get_provider()

        # TTS returns MP3 — save with correct extension so ffmpeg can decode it
        tmp_raw = output_path.parent / f"raw_{uuid.uuid4().hex[:6]}.mp3"
        tmp_raw.parent.mkdir(parents=True, exist_ok=True)

        await provider.generate_speech(processed_text, tmp_raw)

        if not tmp_raw.exists() or tmp_raw.stat().st_size < 1000:
            raise RuntimeError(f"TTS returned empty/missing audio: {tmp_raw}")

        # Post-process (EQ + compression). If ffmpeg fails, use raw audio.
        tmp_post = output_path.parent / f"post_{uuid.uuid4().hex[:6]}.mp3"
        try:
            self.postprocessor.process(tmp_raw, tmp_post, profile)
        except Exception as e:
            logger.warning(f"Post-processing failed ({e}) — using raw TTS audio")
            import shutil
            shutil.copy2(str(tmp_raw), str(tmp_post))

        tmp_raw.unlink(missing_ok=True)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_post.rename(output_path)
        logger.info(f"Voice ready: {output_path.name}")
        return output_path


    async def generate_batch(self, scripts: list[str], output_dir: Path, profile: str = "soft_feminine_v1") -> list[Path]:
        # Implementation for parallel batching
        tasks = []
        for i, script in enumerate(scripts):
            out = output_dir / f"voice_{i}.mp3"
            tasks.append(self.generate(script, out, profile_name=profile))
        return await asyncio.gather(*tasks)
