# backend/persona_system/voice.py

import asyncio
import argparse
from pathlib import Path
from persona_system.voice_engine.generator import PersonaVoiceGenerator

async def main():
    parser = argparse.ArgumentParser(description="Persona Voice Engine CLI")
    parser.add_argument("--text", type=str, required=True, help="Text to synthesize")
    parser.add_argument("--profile", type=str, default="soft_feminine_v1", help="Profile name from profiles.yaml")
    parser.add_argument("--emotion", type=str, default="neutral", help="Emotional tone")
    parser.add_argument("--output", type=str, default="output_voice.mp3", help="Output filename")
    parser.add_argument("--variants", type=int, default=1, help="Number of variants to generate and score")
    
    args = parser.parse_args()
    
    generator = PersonaVoiceGenerator()
    output_path = Path(args.output)
    
    print(f"Synthesizing: \"{args.text}\"")
    print(f"Profile: {args.profile} | Emotion: {args.emotion}")
    
    try:
        path = await generator.generate(
            text=args.text,
            output_path=output_path,
            profile_name=args.profile,
            emotion=args.emotion,
            variants=args.variants
        )
        print(f"Success! Saved to: {path.absolute()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
