import asyncio
import os
import sys
from pathlib import Path

# Add backend to sys.path
backend_path = Path("/Users/adityashah/dev/Personal/ai-shorts/backend")
sys.path.append(str(backend_path))

# Set env for local relative paths
os.environ["DATA_DIR"] = str(backend_path / "data")

async def test_video_generation():
    print("--- Video Generation QA ---")

    try:
        from persona_system.video_engine_adapter.adapter import create_video
        from persona_system.voice_engine.generator import PersonaVoiceGenerator
        from persona_system.config.settings import settings

        print(f"Using Voicebox at: {settings.voicebox_url}")

        test_script = "This is a detailed QA test of the video generation pipeline. We are testing audio synthesis, image processing, and final video rendering."
        test_persona = "default"

        # 1. Generate Voice
        print("Step 1: Generating audio via Voicebox...")
        voice_gen = PersonaVoiceGenerator(test_persona)
        audio_path = backend_path / "data" / "qa_test_voice.mp3"
        await voice_gen.generate(test_script, audio_path)

        if not audio_path.exists():
            print("FAILURE: Audio generation failed")
            return False
        print(f"Audio generated: {audio_path}")

        # 2. Use Downloaded Image
        image_path = backend_path / "data" / "test_face.jpg"
        if not image_path.exists():
             print("FAILURE: Test image not found")
             return False

        # 3. Combine into Video
        print("Step 2: Combining into video (loop type)...")
        output_video = backend_path / "data" / "qa_test_video.mp4"

        outputs = await create_video(
            image_path=image_path,
            audio_path=audio_path,
            script=test_script,
            output_path=output_video,
            video_type="loop",
            persona_name=test_persona,
            variants=1
        )

        if outputs and outputs[0].exists():
            print(f"SUCCESS: Video generated at {outputs[0]}")
            print(f"Size: {os.path.getsize(outputs[0])} bytes")
            return True
        else:
            print("FAILURE: Video file not created")
            return False

    except Exception as e:
        print(f"CRITICAL ERROR during QA: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    asyncio.run(test_video_generation())
