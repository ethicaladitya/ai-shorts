"""UGC script system prompts — enforces creator authenticity over ad-copy polish."""
from __future__ import annotations

from app.services.ugc.config.defaults import DURATION_WORD_LIMITS


def build_system_prompt(style: str = "ugc") -> str:
    """Return the LLM system prompt that enforces UGC realism."""
    return """You are a real person creating a short video for your personal social media account.
You are NOT a marketer, copywriter, or AI. You speak casually, exactly the way people talk in real life.

STRICT RULES — violating these will result in a bad video:
- NEVER use: "in conclusion", "in summary", "to summarize", "let me share", "I'd like to talk about"
- NEVER use bullet points, numbered lists, or markdown formatting of any kind
- NEVER start with a greeting like "Hey guys" or "What's up everyone"
- NEVER use hashtags in the script
- NEVER use perfect grammar throughout — real speech has imperfections
- NEVER sound like an advertisement or brand content
- NEVER use "seamlessly", "dive into", "game-changer", "transformative", "revolutionary"

REQUIRED elements:
- Use filler words naturally: "honestly", "like", "okay so", "I mean", "you know", "literally", "actually"
- Include at least one self-correction or thought restart (e.g. "wait, no — I mean...")
- Write in first person only
- Short sentences. Fragment sentences are fine.
- Vary sentence length — mix punchy one-liners with slightly longer thoughts
- The hook must be a genuine, specific moment or observation — not a generic question

PACING MARKERS (use exactly as written, they control voice delivery):
- [PAUSE] — half-second natural pause
- [EMPHASIS] word [/EMPHASIS] — slight emphasis on a word
- [LAUGH] — casual laugh/chuckle sound

READ YOUR OUTPUT ALOUD IN YOUR HEAD. If it sounds like an ad or a presentation, rewrite it."""


def build_user_prompt(
    persona: dict,
    topic: str,
    style: str,
    platform: str,
    duration_target: int,
) -> str:
    """Build the user-turn prompt for script generation."""
    word_limit = DURATION_WORD_LIMITS.get(duration_target, 150)
    platform_note = {
        "tiktok": "TikTok audience — direct, fast-paced, hook in first 2 seconds",
        "reels":  "Instagram Reels — slightly warmer tone, still fast",
        "shorts": "YouTube Shorts — slightly more informational but still casual",
    }.get(platform, "short-form video")

    persona_voice = ""
    if persona.get("voice"):
        v = persona["voice"]
        pace = v.get("pace", "")
        tone = v.get("tone", "")
        quirks = v.get("quirks", [])
        if pace or tone:
            persona_voice = f"\nPersona voice: {pace} pace, {tone} tone."
        if quirks:
            persona_voice += f" Quirks: {', '.join(quirks[:3])}."

    pillars = persona.get("content_pillars", [])
    pillar_note = f"\nContent pillars: {', '.join(pillars[:4])}." if pillars else ""

    return f"""Write a {duration_target}-second UGC-style video script for {platform_note}.

Topic: {topic}
Style: {style}
Target: ~{word_limit} words maximum (will be spoken aloud at natural pace){persona_voice}{pillar_note}

Structure (no headers, just flow naturally):
1. HOOK — a specific, surprising, or relatable observation that makes someone stop scrolling (first 3 seconds)
2. BODY — the actual content/story/tip. Keep it real. One main point only.
3. CTA — casual, not pushy. One sentence. Something a real person would say.

Return ONLY the script text with pacing markers. No titles, no section labels, no formatting."""
