"""
persona_system/dm_engine/handler.py
DM classification, response generation (3 variants), and human approval layer.
NOT fully autonomous — auto-replies only for cold/low-value messages.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from persona_system.persona_engine.loader import load_persona, apply_caption_style
from persona_system.shared.database import (
    DMThread, DMCategory, Platform, SessionLocal
)
from persona_system.shared.llm import generate

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────────────
_CLASSIFY_SYSTEM = """\
You are a DM classifier for a digital creator. Classify messages into:
- "cold": generic greetings, lurkers, no intent
- "warm": fans who seem genuinely interested or emotional
- "high_value": potential paying fans, people asking about subscriptions, PPV
- "auto_reply": bots, spam, or simple automated messages

Return ONLY valid JSON: {"category": "warm", "confidence": 0.85, "signals": ["..."]}
"""


async def classify_dm(message: str) -> dict[str, Any]:
    """Classify a DM into cold/warm/high_value/auto_reply."""
    prompt = f"Classify this DM:\n\n\"{message}\""
    try:
        raw = await generate(prompt, system=_CLASSIFY_SYSTEM, temperature=0.2, max_tokens=100)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.warning("DM classification failed: %s", e)
    return {"category": "cold", "confidence": 0.5, "signals": []}


# ─────────────────────────────────────────────────────────────────────────────
# Response generator
# ─────────────────────────────────────────────────────────────────────────────
_REPLY_SYSTEM_TEMPLATE = """\
You write DM replies for a digital creator persona.
Persona bio: {bio}
DM style: {dm_style_desc}
Hard limits: {hard_limits}

Rules:
- Write exactly 3 reply variants separated by '---'
- Each variant must be distinct in tone/approach
- Lowercase, no emojis unless one max
- Sound like a real person, not a brand
- NEVER violate hard limits
"""

CATEGORY_TONE_HINTS = {
    DMCategory.COLD: "Keep it brief and slightly mysterious. Leave them wanting more.",
    DMCategory.WARM: "Warm up to them gradually. Ask one question back to keep it going.",
    DMCategory.HIGH_VALUE: "Playfully elusive. Create tension. Hint at exclusive content without being explicit.",
    DMCategory.AUTO_REPLY: "Ultra-brief acknowledgement, nothing more.",
}


async def generate_dm_replies(
    message: str,
    category: DMCategory,
    persona_name: str = "default",
) -> list[str]:
    """
    Generate 3 reply variants for a given DM and category.
    Returns list of 3 reply strings.
    """
    persona = load_persona(persona_name)
    dm_config = persona.get("dm_style", {})
    hard_limits = dm_config.get("hard_limits", [])
    tone_hint = CATEGORY_TONE_HINTS.get(category, "")

    system = _REPLY_SYSTEM_TEMPLATE.format(
        bio=persona.get("bio", "").strip()[:200],
        dm_style_desc=f"{dm_config.get('opening', 'casual')}. {tone_hint}",
        hard_limits=", ".join(hard_limits),
    )

    prompt = (
        f"Message received: \"{message}\"\n\n"
        f"Category: {category.value}\n"
        f"Tone hint: {tone_hint}\n\n"
        "Write 3 reply variants, separated by ---"
    )

    raw = await generate(prompt, system=system, temperature=0.9, max_tokens=400)
    variants = [v.strip() for v in raw.split("---") if v.strip()]

    # Apply persona caption style (lowercase, slang)
    styled = [apply_caption_style(v, persona) for v in variants]
    return styled[:3] if len(styled) >= 3 else styled + ["..."] * (3 - len(styled))


# ─────────────────────────────────────────────────────────────────────────────
# Auto-reply logic
# ─────────────────────────────────────────────────────────────────────────────
AUTO_REPLY_TEMPLATES = [
    "hey 👋",
    "hi, thanks for reaching out 🤍",
    "hey, i'll get back to you soon",
    "thanks for the message 🫶",
]


def should_auto_reply(category: DMCategory, confidence: float) -> bool:
    """Only auto-reply to cold messages with high confidence classification."""
    return category == DMCategory.COLD and confidence >= 0.80


def get_auto_reply(persona_name: str = "default") -> str:
    import random
    persona = load_persona(persona_name)
    # Use persona style for even the auto-reply
    base = random.choice(AUTO_REPLY_TEMPLATES)
    return apply_caption_style(base, persona)


# ─────────────────────────────────────────────────────────────────────────────
# Process incoming DM — full pipeline
# ─────────────────────────────────────────────────────────────────────────────
async def process_incoming_dm(
    platform_thread_id: str,
    fan_username: str,
    message: str,
    platform: Platform,
    persona_name: str = "default",
) -> dict[str, Any]:
    """
    Full DM processing:
    1. Classify
    2. If auto-reply eligible → send immediately
    3. Otherwise → generate 3 options for human review
    Returns status dict.
    """
    db = SessionLocal()
    try:
        # Upsert DM thread
        thread = db.query(DMThread).filter(
            DMThread.platform_thread_id == platform_thread_id
        ).first()
        if not thread:
            thread = DMThread(
                platform=platform,
                platform_thread_id=platform_thread_id,
                persona=persona_name,
                fan_username=fan_username,
            )
            db.add(thread)

        thread.last_message = message
        thread.last_message_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )

        # Classify
        classification = await classify_dm(message)
        category_str = classification.get("category", "cold")
        confidence = classification.get("confidence", 0.5)
        category = DMCategory(category_str) if category_str in [e.value for e in DMCategory] else DMCategory.COLD
        thread.category = category
        db.commit()

        # Auto-reply for cold/low-value
        if should_auto_reply(category, confidence):
            reply = get_auto_reply(persona_name)
            thread.reply_text = reply
            thread.auto_replied = True
            thread.reply_sent = False  # actual send happens in automation layer
            db.commit()
            logger.info("Auto-reply queued for %s (%s)", fan_username, category.value)
            return {
                "action": "auto_reply",
                "category": category.value,
                "reply": reply,
                "thread_id": thread.id,
            }

        # Generate 3 options for human approval
        replies = await generate_dm_replies(message, category, persona_name)
        thread.reply_text = json.dumps(replies)  # store all 3 variants as JSON
        thread.reply_sent = False
        thread.approved_by_human = False
        db.commit()

        logger.info("DM from %s (%s) — awaiting human approval", fan_username, category.value)
        return {
            "action": "needs_approval",
            "category": category.value,
            "variants": replies,
            "thread_id": thread.id,
        }
    finally:
        db.close()


async def approve_and_queue_reply(thread_id: int, chosen_variant_index: int = 0) -> str:
    """Human approves a reply variant. Marks for sending."""
    db = SessionLocal()
    try:
        thread = db.query(DMThread).filter(DMThread.id == thread_id).first()
        if not thread:
            raise ValueError(f"Thread {thread_id} not found")

        variants = json.loads(thread.reply_text or "[]")
        chosen = variants[chosen_variant_index] if variants else ""
        thread.reply_text = chosen
        thread.approved_by_human = True
        thread.reply_sent = False  # will be sent by automation layer
        db.commit()
        return chosen
    finally:
        db.close()
