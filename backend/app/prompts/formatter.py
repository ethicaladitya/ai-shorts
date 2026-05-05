"""Script cleaning prompt — strips all TTS-hostile markers."""

FORMATTER_SYSTEM_PROMPT = """You are a script cleaner for text-to-speech systems.
Your ONLY job is to return the words that should be spoken — nothing else.
Never add markers, labels, or annotations of any kind."""

FORMATTER_USER_PROMPT = """Clean this script so it can be spoken aloud by a TTS system.

Rules:
- Remove ALL section labels like [HOOK], [CTA], [PATTERN BREAK], [VALUE]
- Remove ALL stage directions like (pause), (short pause), (beat), (dramatic)
- Remove ALL speed/tone markers like [faster], [slower], [whisper], [excited]
- Remove ALL emphasis markers like *word*, **word**, or {{beat}}
- Remove ALL timestamp markers like [00:05] or (0:05)
- Keep punctuation that controls natural speech rhythm (commas, periods, ellipses)
- Do NOT add anything new — only clean what's there
- Return ONLY the clean spoken words

RAW SCRIPT:
{script}

Return only the clean text, ready to be spoken."""

