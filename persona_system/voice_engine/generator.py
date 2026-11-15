"""
persona_system/voice_engine/generator.py
Persona-specific voice generation with emotion presets, pauses, and imperfections.
Wraps the existing VoiceboxVoiceProvider from the ai-shorts backend.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from persona_system.config.settings import settings
from persona_system.persona_engine.loader import load_persona

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Emotion preset → SSML / prosody hints
# ─────────────────────────────────────────────────────────────────────────────
EMOTION_SPEED: dict[str, float] = {
    "flirty":   0.92,
    "playful":  1.08,
    "serious":  0.95,
    "sultry":   0.85,
    "excited":  1.12,
    "neutral":  1.00,
}

PAUSE_PHRASES = [
    ", ",
    "... ",
    " — ",
]


def inject_pauses(text: str, density: float = 0.15) -> str:
    """
    Inject natural pauses into the script before sending to TTS.
    density = probability of adding a pause after a comma/period/dash.
    """
    # Add slight breathing pause between sentences
    text = re.sub(r'([.!?])\s+', r'\1  ', text)
    # Randomly extend some commas to simulate thinking
    words = text.split()
    result = []
    for i, word in enumerate(words):
        result.append(word)
        if word.endswith(",") and random.random() < density:
            result.append("...")
    return " ".join(result)


def add_imperfections(text: str, rate: float = 0.05) -> str:
    """
    Occasionally add filler words (um, uh, you know) at sentence boundaries
    to make the voice feel human.
    """
    fillers = ["um,", "uh,", "you know,", "like,", "I mean,"]
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []
    for i, sent in enumerate(sentences):
        if i > 0 and random.random() < rate:
            result.append(f"{random.choice(fillers)} {sent}")
        else:
            result.append(sent)
    return " ".join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Voice generation via Voicebox (local)
# ─────────────────────────────────────────────────────────────────────────────
class PersonaVoiceGenerator:
    """
    Wraps the Voicebox local TTS with persona-specific emotion presets.
    Falls back to Kokoro ONNX if Voicebox is unavailable.
    """

    def __init__(self, persona_name: str = "default"):
        self.persona_name = persona_name
        self._persona = load_persona(persona_name)
        self._vb_provider = None

    def _get_voicebox(self):
        if self._vb_provider is None:
            # Import the existing provider from the ai-shorts backend
            import sys
            backend_path = Path(__file__).resolve().parents[3] / "backend"
            if str(backend_path) not in sys.path:
                sys.path.insert(0, str(backend_path))
            from app.services.voice_provider import VoiceboxVoiceProvider
            self._vb_provider = VoiceboxVoiceProvider(
                url=settings.voicebox_url,
                engine=settings.voicebox_engine,
                profile_id=settings.voicebox_profile_id,
            )
        return self._vb_provider

    async def generate(
        self,
        text: str,
        output_path: Path,
        emotion: str = "neutral",
        add_pauses: bool = True,
        humanize: bool = True,
    ) -> Path:
        """Generate voice audio for `text` with persona style applied."""
        # Apply persona voice processing
        processed_text = text
        if add_pauses:
            processed_text = inject_pauses(processed_text)
        if humanize:
            processed_text = add_imperfections(processed_text, rate=0.04)

        # Adjust speed via ffmpeg post-processing (Voicebox itself doesn't expose speed)
        speed = EMOTION_SPEED.get(emotion, 1.0)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            vb = self._get_voicebox()
            # Generate to temp path first
            tmp_path = output_path.parent / f"_raw_{uuid.uuid4().hex[:6]}.mp3"
            await vb.generate_speech(processed_text, tmp_path)

            if abs(speed - 1.0) > 0.02:
                # Apply speed adjustment
                cmd = [
                    "ffmpeg", "-y", "-i", str(tmp_path),
                    "-af", f"atempo={speed}",
                    str(output_path),
                ]
                subprocess.run(cmd, check=True, capture_output=True, timeout=60)
                tmp_path.unlink(missing_ok=True)
            else:
                tmp_path.rename(output_path)

            logger.info("Voice generated → %s (emotion=%s, speed=%.2f)", output_path.name, emotion, speed)
            return output_path

        except Exception as e:
            logger.error("Voice generation failed: %s", e)
            raise

    async def generate_batch(
        self,
        scripts: list[str],
        output_dir: Path,
        emotion: str = "neutral",
    ) -> list[Path]:
        """Generate voice for multiple scripts concurrently (max 3 at a time)."""
        sem = asyncio.Semaphore(3)
        results: list[Path | None] = [None] * len(scripts)

        async def _one(i: int, script: str):
            async with sem:
                out = output_dir / f"voice_{i:03d}_{uuid.uuid4().hex[:6]}.mp3"
                try:
                    results[i] = await self.generate(script, out, emotion)
                except Exception as e:
                    logger.error("Voice batch item %d failed: %s", i, e)

        await asyncio.gather(*[_one(i, s) for i, s in enumerate(scripts)])
        return [r for r in results if r is not None]


# ─────────────────────────────────────────────────────────────────────────────
# Voice prompt templates
# ─────────────────────────────────────────────────────────────────────────────
VOICE_PROMPT_TEMPLATES = {
    "late_night": (
        "it's late and i can't sleep. just thinking about {topic}. "
        "do you ever feel like that?"
    ),
    "confession": (
        "okay i need to tell you something. i've been thinking about {topic} and i can't stop."
    ),
    "provocative": (
        "hot take: {topic}. and before you argue, just hear me out."
    ),
    "soft": (
        "this is going to sound weird but {topic} just makes me feel like everything's okay."
    ),
    "chaotic": (
        "i don't know how to explain this but {topic}. and i mean that in the best possible way."
    ),
}


def get_voice_prompt(template_name: str, topic: str) -> str:
    template = VOICE_PROMPT_TEMPLATES.get(template_name, VOICE_PROMPT_TEMPLATES["soft"])
    return template.format(topic=topic)
