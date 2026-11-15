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
# Simple string matches — applied case-insensitively to full text
_BANNED_PHRASES = [
    "in conclusion",
    "in summary",
    "to summarize",
    "let me share",
    "I'd like to talk about",
    "## ",             # markdown headers
]

# Line-start patterns — only trigger if the pattern appears at the start of a line
# (prevents mid-sentence dashes or "1." in "step 1." from false-positiving)
_BANNED_LINE_PATTERNS = re.compile(
    r"(^|\n)\s*(\d+\.\s|\-\s|\*\s)",   # numbered list or bullet at line start
)


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


_PREAMBLE_PATTERNS = re.compile(
    r"^(here\s+is\s+(the\s+)?(ugc\s+)?(video\s+)?script[:\-]?\s*|"
    r"script[:\-]\s*|"
    r"here('s|\s+is)\s+[a-z\s]*script[:\-]?\s*|"
    r"okay[\s,!]+here['\s]*s?\s+[a-z\s]*script[:\-]?\s*|"
    r"[a-z\s,!]*\bscript[:\-]\s*)",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_llm_preamble(text: str) -> str:
    """Remove common LLM preamble phrases like 'Here is the script:' from the start of output."""
    text = text.strip()
    # Strip line-by-line prefix on the first non-empty line
    lines = text.splitlines()
    if lines and _PREAMBLE_PATTERNS.match(lines[0].strip()):
        lines = lines[1:]
    text = "\n".join(lines).strip()
    # Also strip inline prefix at start if it runs into the script
    text = _PREAMBLE_PATTERNS.sub("", text).strip()
    return text


def _contains_banned_phrase(text: str) -> bool:
    lower = text.lower()
    if any(phrase.lower() in lower for phrase in _BANNED_PHRASES):
        return True
    if _BANNED_LINE_PATTERNS.search(text):
        return True
    return False


def _load_persona_addendum(persona_name: str) -> str:
    """Try to import a persona-specific system prompt addendum. Returns '' if none exists."""
    try:
        import importlib
        mod = importlib.import_module(f"app.services.ugc.personas.{persona_name}.script_prompts")
        return getattr(mod, "SYSTEM_PROMPT_ADDENDUM", "")
    except ModuleNotFoundError:
        return ""


def _persona_banned_phrases(persona: dict) -> list[str]:
    """Return persona-specific banned words from YAML."""
    return persona.get("banned_words", [])


async def _generate_once(
    persona: dict,
    topic: str,
    style: str,
    platform: str,
    duration_target: int,
    provider: str,
) -> str:
    system = build_system_prompt(style)
    # persona_slug is the YAML filename key (e.g. "zara_chen"), not the display name
    persona_slug = persona.get("_slug", persona.get("name", "")).lower().replace(" ", "_")
    addendum = _load_persona_addendum(persona_slug)
    if addendum:
        system = system + "\n" + addendum
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

    extra_banned = _persona_banned_phrases(persona)

    for attempt in range(1, 4):  # up to 3 inner attempts for realism check
        raw = await _generate_once(persona, topic, style, platform, duration_target, provider)
        raw = _strip_llm_preamble(raw)
        # Check global banned phrases + persona-specific ones
        persona_hit = any(phrase.lower() in raw.lower() for phrase in extra_banned)
        if not _contains_banned_phrase(raw) and not persona_hit:
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

    # Truncate to word limit if significantly over — avoids audio running far past target duration
    if script.word_count > word_limit * 1.15:
        logger.warning(
            "Script word count %d exceeds target %d for %ds video — truncating",
            script.word_count, word_limit, duration_target,
        )
        # Split on whitespace, keep first word_limit words, rejoin
        words = result.split()
        truncated = " ".join(words[:word_limit])
        # Find last sentence-ending punctuation and cut there for a clean end
        last_sentence_end = max(
            truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?")
        )
        if last_sentence_end > len(truncated) * 0.7:  # only cut if last sentence is reasonably late
            truncated = truncated[:last_sentence_end + 1]
        result = truncated
        script = UGCScript.from_raw(result)

    logger.info("Generated UGC script: %d words, %d pacing markers", script.word_count, len(script.pacing_markers))

    # Unload Ollama model to free up memory/VRAM for subsequent stages
    if provider == "ollama":
        try:
            from persona_system.shared.llm import unload_ollama_model
            await unload_ollama_model()
        except Exception as e:
            logger.warning("Failed to unload Ollama model: %s", e)

    return script
