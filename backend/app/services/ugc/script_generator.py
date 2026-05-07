"""UGC script generator — calls Ollama locally, falls back to Azure OpenAI."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.services.ugc.config.defaults import DURATION_WORD_LIMITS
from app.services.ugc.realism.script_prompts import build_system_prompt, build_user_prompt
from app.services.ugc.utils.retry import async_retry

logger = logging.getLogger(__name__)

# Phrases that indicate the LLM produced polished ad copy instead of UGC
_BANNED_PHRASES = [
    "in conclusion",
    "in summary",
    "to summarize",
    "let me share",
    "I'd like to talk about",
    "1.", "2.", "3.",  # numbered lists
    "- ",              # bullet lists
    "## ",             # markdown headers
]


@dataclass
class UGCScript:
    raw: str
    hook: str = ""
    body: str = ""
    cta: str = ""
    word_count: int = 0
    pacing_markers: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: str) -> "UGCScript":
        text = raw.strip()
        words = len(text.split())
        markers = re.findall(r"\[(PAUSE|EMPHASIS|LAUGH)[^\]]*\]", text)

        # Naive segment split: first sentence = hook, last sentence = cta, rest = body
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        hook = sentences[0] if sentences else text
        cta = sentences[-1] if len(sentences) > 1 else ""
        body = " ".join(sentences[1:-1]) if len(sentences) > 2 else sentences[1] if len(sentences) == 2 else ""

        return cls(raw=text, hook=hook, body=body, cta=cta, word_count=words, pacing_markers=markers)


def _contains_banned_phrase(text: str) -> bool:
    lower = text.lower()
    return any(phrase.lower() in lower for phrase in _BANNED_PHRASES)


async def _generate_once(
    persona: dict,
    topic: str,
    style: str,
    platform: str,
    duration_target: int,
    provider: str,
) -> str:
    system = build_system_prompt(style)
    user = build_user_prompt(persona, topic, style, platform, duration_target)

    if provider == "ollama":
        from persona_system.shared.llm import _call_ollama
        return await _call_ollama(user, system=system, temperature=0.85, max_tokens=600)

    # azure_openai or any cloud provider
    from persona_system.shared.llm import _call_azure
    return await _call_azure(user, system=system, temperature=0.85, max_tokens=600)


@async_retry(max_attempts=3, base_delay=2.0)
async def generate_ugc_script(
    persona: dict,
    topic: str,
    style: str = "ugc",
    platform: str = "tiktok",
    duration_target: int = 30,
    provider: str = "ollama",
) -> UGCScript:
    """Generate a UGC-style script and validate realism. Auto-retries up to 2 extra times
    if banned phrases are detected (via a dedicated inner loop, not the outer retry)."""

    word_limit = DURATION_WORD_LIMITS.get(duration_target, 150)
    result: str | None = None

    for attempt in range(1, 4):  # up to 3 inner attempts for realism check
        raw = await _generate_once(persona, topic, style, platform, duration_target, provider)
        if not _contains_banned_phrase(raw):
            result = raw
            break
        logger.warning(
            "Script realism check failed (attempt %d/3) — banned phrases detected, regenerating",
            attempt,
        )

    if result is None:
        # Use the last attempt even if imperfect — better than failing the job
        logger.warning("Script realism validation still failing after 3 attempts — using last output")
        result = raw  # type: ignore[possibly-undefined]

    script = UGCScript.from_raw(result)

    # Warn if over word limit (don't fail — TTS will just run long)
    if script.word_count > word_limit * 1.2:
        logger.warning(
            "Script word count %d exceeds target %d for %ds video",
            script.word_count, word_limit, duration_target,
        )

    logger.info("Generated UGC script: %d words, %d pacing markers", script.word_count, len(script.pacing_markers))
    return script
