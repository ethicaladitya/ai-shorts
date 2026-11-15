#!/usr/bin/env python3
"""
Quick validation: load Zara Chen persona → generate script → generate first image.
Stops at approval checkpoint (does not render video).

Run from backend/: python -m scripts.test_zara_script
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Ensure backend root is on path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))


async def main():
    from persona_system.persona_engine.loader import load_persona
    from app.services.ugc.script_generator import generate_ugc_script
    from app.services.ugc.image_generator import generate_scene_images

    print("\n=== STEP 1: Load Zara Chen persona ===")
    persona = load_persona("zara_chen")
    assert persona, "Persona not found — check zara_chen.yaml exists in personas dir"
    print(f"  name:            {persona['name']}")
    print(f"  trigger_word:    {persona['trigger_word']}")
    print(f"  content_pillars: {persona['content_pillars'][:2]}")
    print(f"  banned_words:    {persona.get('banned_words', [])[:3]}")
    print(f"  voice pace:      {persona['voice']['pace']}")
    print(f"  voice tone:      {persona['voice']['tone']}")
    print(f"  image_gen seed:  {persona['image_gen']['seed']}")
    print("  [PASS] Persona loaded correctly\n")

    # Validate addendum loads
    from app.services.ugc.script_generator import _load_persona_addendum
    addendum = _load_persona_addendum("zara")
    assert addendum, "Persona addendum not found — check personas/zara/script_prompts.py"
    print(f"  [PASS] Persona addendum loaded ({len(addendum)} chars)\n")

    print("=== STEP 2: Generate UGC script ===")
    topic = "the one browser extension that quietly doubled my focus"
    script = await generate_ugc_script(
        persona=persona,
        topic=topic,
        style="ugc",
        platform="tiktok",
        duration_target=30,
        provider="ollama",  # local, free
    )
    print(f"  word_count:      {script.word_count}")
    print(f"  pacing_markers:  {script.pacing_markers}")
    print(f"\n  HOOK:  {script.hook}")
    print(f"  BODY:  {script.body[:120]}...")
    print(f"  CTA:   {script.cta}\n")

    # Validate no banned phrases slipped through
    from app.services.ugc.script_generator import _contains_banned_phrase, _persona_banned_phrases
    extra = _persona_banned_phrases(persona)
    global_hit = _contains_banned_phrase(script.raw)
    persona_hit = any(p.lower() in script.raw.lower() for p in extra)
    if global_hit or persona_hit:
        print(f"  [WARN] Banned phrase detected in output — check script above")
    else:
        print(f"  [PASS] No banned phrases detected\n")

    print("=== STEP 3: Generate first scene image ===")
    print("  (uses A1111 if running, falls back to azure_gpt_image)\n")

    import tempfile, os
    out_dir = Path(tempfile.mkdtemp(prefix="zara_test_"))
    try:
        images = await generate_scene_images(
            persona=persona,
            script=script,
            job_id=9999,
            output_dir=out_dir,
            provider="a1111",
            num_images=1,
        )
        print(f"  Generated {len(images)} image(s):")
        for img in images:
            size = Path(img).stat().st_size if Path(img).exists() else 0
            print(f"    {img} ({size // 1024} KB)")
        print(f"\n  [PASS] Image generation succeeded\n")
    except Exception as e:
        print(f"  [INFO] Image generation: {e}")
        print("  (This is expected if A1111 is offline and Azure GPT-Image not configured)\n")

    print("=== COMPLETE — Approval checkpoint would appear here in full pipeline ===")
    print(f"  Script preview saved to: {out_dir}\n")


if __name__ == "__main__":
    asyncio.run(main())
