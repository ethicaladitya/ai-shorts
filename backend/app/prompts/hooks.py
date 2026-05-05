"""Hook generation prompts."""

HOOK_SYSTEM_PROMPT = """You are an expert short-form video scriptwriter specializing in high-retention hooks.

Your hooks must:
- Stop the scroll in the first 1-2 seconds
- Create curiosity gaps
- Use pattern interrupts
- Be conversational and direct
- Never be clickbait - always deliver on the promise

Hook types you can use:
1. QUESTION HOOK: Start with a provocative question
2. STAT HOOK: Lead with a surprising statistic
3. CONTRARIAN HOOK: Challenge a common belief
4. STORY HOOK: Start mid-story for immediate engagement
5. DIRECT HOOK: Bold, direct statement that commands attention"""

HOOK_USER_PROMPT = """Generate 5 unique, high-retention hooks for a short-form video about the following topic.

TOPIC: {topic}

SOURCE CONTENT:
{content}

ADDITIONAL NOTES:
{notes}

{knowledge_context}

Requirements:
- Each hook should be 1-2 sentences max (under 15 words ideal)
- Each hook should use a DIFFERENT hook type
- Hooks must be spoken-word friendly (read aloud naturally)
- Include the hook type label

Return as JSON array:
[
  {{"type": "question", "text": "...", "why_it_works": "..."}},
  {{"type": "stat", "text": "...", "why_it_works": "..."}},
  {{"type": "contrarian", "text": "...", "why_it_works": "..."}},
  {{"type": "story", "text": "...", "why_it_works": "..."}},
  {{"type": "direct", "text": "...", "why_it_works": "..."}}
]

Return ONLY the JSON array, no other text."""
