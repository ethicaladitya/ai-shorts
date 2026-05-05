"""Script generation prompts."""

SCRIPT_SYSTEM_PROMPT = """You are a world-class short-form video scriptwriter. You write scripts that get millions of views.

Your scripts follow the proven HIGH-RETENTION STRUCTURE:

1. HOOK (0-3s) → Stop the scroll. Create instant curiosity.
2. PATTERN BREAK (3-8s) → Shift energy. Unexpected angle.
3. VALUE DELIVERY (8-45s) → Core insights. Rapid-fire value.
4. CTA (45-60s) → Clear, compelling call to action.

Writing rules:
- Write for SPOKEN word, not written word
- Use short sentences (5-12 words)
- Include natural pauses and emphasis cues
- Create "open loops" to maintain attention
- Every sentence must earn the next sentence
- No filler words or weak transitions
- Use power words: "secret", "mistake", "actually", "here's the thing"
- Break information into digestible chunks"""

SCRIPT_USER_PROMPT = """Write a complete short-form video script (45-60 seconds when spoken).

SELECTED HOOK: {hook}

TOPIC: {topic}

SOURCE CONTENT:
{content}

ADDITIONAL NOTES:
{notes}

{knowledge_context}

Script structure:
1. HOOK: Use the selected hook exactly as provided
2. PATTERN BREAK: Unexpected transition that shifts energy
3. VALUE: 3-5 key points delivered rapidly
4. CTA: Compelling call to action

Format the script as clean spoken sentences ONLY:
- NO section markers like [HOOK], [VALUE], [CTA]
- NO stage directions like (pause) or (beat)
- NO timestamp markers
- Just natural spoken sentences with proper punctuation

Return ONLY the script text as it should be spoken."""

SCRIPT_SECTION_REGEN_PROMPT = """Rewrite ONLY the [{section}] section of this script.

FULL SCRIPT:
{script}

TOPIC: {topic}


INSTRUCTIONS: {instructions}

Return ONLY the rewritten section text with formatting markers. Do not include the section label."""
