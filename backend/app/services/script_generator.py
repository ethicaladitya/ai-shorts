"""Script generation service - orchestrates the full AI pipeline."""
import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import KnowledgeEntry
from app.services.ai_provider import get_ai_provider, AIProvider
from app.prompts.hooks import HOOK_SYSTEM_PROMPT, HOOK_USER_PROMPT
from app.prompts.scripts import SCRIPT_SYSTEM_PROMPT, SCRIPT_USER_PROMPT, SCRIPT_SECTION_REGEN_PROMPT
from app.prompts.formatter import FORMATTER_SYSTEM_PROMPT, FORMATTER_USER_PROMPT

logger = logging.getLogger(__name__)


def _build_knowledge_context(db: Session) -> str:
    """Gather active knowledge entries for prompt context."""
    entries = db.query(KnowledgeEntry).filter(KnowledgeEntry.is_active == True).all()
    if not entries:
        return ""

    sections = []
    categories = {}
    for e in entries:
        categories.setdefault(e.category, []).append(e)

    for cat, items in categories.items():
        label = cat.replace("_", " ").title()
        texts = [f"- {item.title}: {item.content}" if item.title else f"- {item.content}" for item in items]
        sections.append(f"[{label}]\n" + "\n".join(texts))

    return "KNOWLEDGE BASE:\n" + "\n\n".join(sections)


def _get_provider() -> AIProvider:
    return get_ai_provider(
        provider_name=settings.ai_provider,
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        deployment=settings.azure_openai_deployment,
        api_version=settings.azure_openai_api_version,
        openai_api_key=settings.openai_api_key,
    )


async def generate_hooks(topic: str, content: str, notes: str, db: Session) -> list[dict]:
    """Generate 5 hook options for a topic."""
    provider = _get_provider()
    knowledge = _build_knowledge_context(db)

    prompt = HOOK_USER_PROMPT.format(
        topic=topic,
        content=content[:3000] if content else "No source content provided.",
        notes=notes or "None",
        knowledge_context=knowledge,
    )

    result = await provider.generate(prompt, system_prompt=HOOK_SYSTEM_PROMPT, temperature=0.8, max_tokens=1500)

    # Parse JSON from response
    try:
        # Handle markdown code blocks
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        hooks = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse hooks JSON: {result[:200]}")
        hooks = [{"type": "direct", "text": result.strip(), "why_it_works": "Raw AI output"}]

    return hooks


async def generate_script(hook: str, topic: str, content: str, notes: str, db: Session) -> str:
    """Generate a full script from a selected hook."""
    provider = _get_provider()
    knowledge = _build_knowledge_context(db)

    prompt = SCRIPT_USER_PROMPT.format(
        hook=hook,
        topic=topic,
        content=content[:3000] if content else "No source content provided.",
        notes=notes or "None",
        knowledge_context=knowledge,
    )

    result = await provider.generate(prompt, system_prompt=SCRIPT_SYSTEM_PROMPT, temperature=0.7, max_tokens=2000)
    return result.strip()


async def regenerate_section(script: str, section: str, topic: str, instructions: str = "") -> str:
    """Regenerate a specific section of the script."""
    provider = _get_provider()

    prompt = SCRIPT_SECTION_REGEN_PROMPT.format(
        section=section,
        script=script,
        topic=topic,
        instructions=instructions or "Make it more engaging and attention-grabbing.",
    )

    result = await provider.generate(prompt, system_prompt=SCRIPT_SYSTEM_PROMPT, temperature=0.8, max_tokens=800)
    return result.strip()


async def format_script(script: str) -> str:
    """Add delivery formatting to a script."""
    provider = _get_provider()

    prompt = FORMATTER_USER_PROMPT.format(script=script)
    result = await provider.generate(prompt, system_prompt=FORMATTER_SYSTEM_PROMPT, temperature=0.3, max_tokens=2000)
    return result.strip()


async def test_ai_connection() -> dict:
    """Test the current AI provider connection."""
    try:
        provider = _get_provider()
        return await provider.test_connection()
    except ValueError as e:
        return {"status": "error", "message": str(e)}
