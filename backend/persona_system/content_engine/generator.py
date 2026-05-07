"""
persona_system/content_engine/generator.py
Batch caption + script generation, persona-aware scoring, and content scoring.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from persona_system.persona_engine.loader import load_persona, apply_caption_style, get_content_pillars
from persona_system.shared.llm import generate

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# System prompts
# ─────────────────────────────────────────────────────────────────────────────
_CAPTION_SYSTEM = """\
You write social media captions for a digital creator persona.
Output ONLY the caption text — no explanation, no quotes, no preamble.
Style: lowercase, emotionally engaging, slightly chaotic, authentic.
Keep captions under 20 words unless the content demands more.
Never use generic phrases like "check this out" or "swipe up".
"""

_SCRIPT_SYSTEM = """\
You write short-form video scripts (30-60 seconds) for a digital creator persona.
Output ONLY the spoken words — no stage directions, no hashtags, no formatting.
Style: first-person, conversational, one strong hook in the first 5 words.
End with something that makes the viewer feel seen or curious.
"""

_SCORING_SYSTEM = """\
You score social media content on a 0.0–1.0 scale.
Return ONLY valid JSON: {"score": 0.75, "reason": "one sentence"}
Criteria: emotional hook, authenticity, retention potential, uniqueness.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Caption generation
# ─────────────────────────────────────────────────────────────────────────────
async def generate_captions(
    persona_name: str = "default",
    count: int = 20,
    pillar: str | None = None,
) -> list[str]:
    """Generate `count` captions in one LLM call (batched for efficiency)."""
    persona = load_persona(persona_name)
    pillars = get_content_pillars(persona)
    chosen_pillar = pillar or (pillars[0] if pillars else "lifestyle")

    style_notes = "\n".join([
        f"- Persona bio: {persona.get('bio', '').strip()[:200]}",
        f"- Content pillar: {chosen_pillar}",
        f"- Emotional register: {persona.get('caption_style', {}).get('emotional_register', 'chaotic soft')}",
        f"- Slang to use occasionally: {', '.join(persona.get('caption_style', {}).get('slang', []))}",
        f"- Contradictions that make her feel real: {', '.join(persona.get('contradictions', [])[:2])}",
    ])

    prompt = (
        f"Generate {count} different Instagram captions. Each on its own line, numbered 1–{count}.\n\n"
        f"Style context:\n{style_notes}\n\n"
        f"Make each one feel distinct. Vary length from 6 to 22 words. Some are introspective, "
        f"some are provocative, some are soft, some are funny."
    )

    raw = await generate(prompt, system=_CAPTION_SYSTEM, temperature=0.95, max_tokens=2048)

    # Parse numbered list
    captions = []
    for line in raw.splitlines():
        line = re.sub(r"^\d+[.)]\s*", "", line).strip()
        if line:
            styled = apply_caption_style(line, persona)
            captions.append(styled)

    return captions[:count]


# ─────────────────────────────────────────────────────────────────────────────
# Script generation
# ─────────────────────────────────────────────────────────────────────────────
async def generate_script(
    persona_name: str = "default",
    topic: str = "",
    caption: str = "",
    target_seconds: int = 45,
) -> str:
    """Generate a single video script matching the persona and caption."""
    persona = load_persona(persona_name)

    prompt = (
        f"Write a {target_seconds}-second talking-head video script.\n\n"
        f"Topic: {topic or caption or 'everyday life moment'}\n"
        f"Caption (for context): {caption}\n\n"
        f"Character notes:\n"
        f"- Bio: {persona.get('bio', '').strip()[:200]}\n"
        f"- Voice: {persona.get('voice', {}).get('tone', 'warm')}\n"
        f"- Contradictions: {', '.join(persona.get('contradictions', [])[:2])}\n\n"
        f"Script requirements:\n"
        f"- First 5 words must stop the scroll\n"
        f"- 1 clear emotional beat in the middle\n"
        f"- End on something open, not a hard CTA\n"
        f"- Approx {target_seconds * 2} words"
    )

    return await generate(prompt, system=_SCRIPT_SYSTEM, temperature=0.9, max_tokens=512)


# ─────────────────────────────────────────────────────────────────────────────
# Script optimization for video
# ─────────────────────────────────────────────────────────────────────────────
async def optimize_script_for_video(
    script: str,
    target_seconds: int = 45,
    variants: int = 2,
) -> list[str]:
    """
    Rewrite a script optimized for short-form retention:
    - pacing beats
    - emotional arc
    - natural pauses
    Returns `variants` versions.
    """
    prompt = (
        f"Rewrite the following script {variants} times, each as a standalone version.\n"
        f"Target duration: {target_seconds} seconds when spoken at natural pace.\n"
        f"Separate versions with '---'.\n\n"
        f"For each version:\n"
        f"- Keep the core message\n"
        f"- Vary the pacing (one faster, one slower)\n"
        f"- Optimize word choice for emotional retention\n"
        f"- No stage directions or brackets\n\n"
        f"Original script:\n{script}"
    )
    raw = await generate(prompt, system=_SCRIPT_SYSTEM, temperature=0.85, max_tokens=1024)
    versions = [v.strip() for v in raw.split("---") if v.strip()]
    return versions[:variants] if len(versions) >= variants else [script]


# ─────────────────────────────────────────────────────────────────────────────
# Content scoring
# ─────────────────────────────────────────────────────────────────────────────
async def score_content(text: str, content_type: str = "caption") -> dict[str, Any]:
    """
    Score a caption or script on a 0.0–1.0 scale.
    Returns {"score": float, "reason": str}
    """
    # Trim to avoid token-limit 400 errors on long scripts
    trimmed = text[:500].strip()
    prompt = (
        f"Score this {content_type}:\n"
        f'"{trimmed}"\n'
        f"Return only JSON."
    )
    try:
        raw = await generate(prompt, system=_SCORING_SYSTEM, temperature=0.1, max_tokens=60)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.warning("Scoring failed: %s", e)
    return {"score": 0.5, "reason": "scoring unavailable"}


# ─────────────────────────────────────────────────────────────────────────────
# Batch generation pipeline
# ─────────────────────────────────────────────────────────────────────────────
async def generate_content_batch(
    persona_name: str = "default",
    captions_count: int = 30,
    scripts_count: int = 5,
    pillar: str | None = None,
) -> dict[str, Any]:
    """
    Generate a full batch of content:
    1. Generate captions in bulk
    2. Score each caption
    3. Generate scripts for top-scoring captions
    4. Return structured batch
    """
    logger.info("Generating caption batch (%d)...", captions_count)
    captions = await generate_captions(persona_name, captions_count, pillar)

    # Score captions in small batches to avoid 400 rate-limit errors
    import asyncio
    batch_size = 5
    all_scores: list[dict] = []
    for i in range(0, len(captions), batch_size):
        batch = captions[i:i + batch_size]
        batch_scores = await asyncio.gather(*[score_content(c, "caption") for c in batch])
        all_scores.extend(batch_scores)
        if i + batch_size < len(captions):
            await asyncio.sleep(0.3)  # brief pause between batches
    scores = all_scores

    scored_captions = sorted(
        [{"caption": c, **s} for c, s in zip(captions, scores)],
        key=lambda x: x.get("score", 0),
        reverse=True,
    )

    # Generate scripts for top captions
    top_captions = scored_captions[:scripts_count]
    scripts = []
    for item in top_captions:
        script = await generate_script(persona_name, caption=item["caption"])
        script_score = await score_content(script, "script")
        scripts.append({
            "caption": item["caption"],
            "caption_score": item.get("score", 0),
            "script": script,
            "script_score": script_score.get("score", 0),
            "combined_score": (item.get("score", 0) + script_score.get("score", 0)) / 2,
        })

    scripts.sort(key=lambda x: x["combined_score"], reverse=True)

    return {
        "persona": persona_name,
        "pillar": pillar,
        "all_captions": scored_captions,
        "scripts_with_captions": scripts,
        "top_script": scripts[0] if scripts else None,
    }
