"""
persona_system/scripts/run_pipeline.py
Full end-to-end content generation pipeline:
  1. Generate captions + scripts (content_engine)
  2. Score content — only proceed if above threshold
  3. Generate images (image_engine / SD)
  4. Generate voice (voice_engine / Voicebox)
  5. Create video (video_engine_adapter → ai-shorts)
  6. Store to DB
  7. Schedule for posting

Run: python -m persona_system.scripts.run_pipeline --persona default --pillar "late night energy"
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


async def run_full_pipeline(
    persona_name: str = "default",
    pillar: str | None = None,
    captions_count: int = 20,
    scripts_count: int = 3,
    generate_images: bool = True,
    image_count: int = 5,
    video_type: str = "loop",
    dry_run: bool = False,
) -> dict:
    """Full content creation pipeline."""
    from persona_system.config.settings import settings
    from persona_system.content_engine.generator import generate_content_batch
    from persona_system.shared.database import (
        ContentPiece, ContentStatus, GeneratedImage, Platform, SessionLocal, init_db
    )

    init_db()
    db = SessionLocal()
    results = {"persona": persona_name, "content_pieces": [], "images": []}

    try:
        # ── 1. Generate content batch ─────────────────────────────────────
        logger.info("Step 1: Generating content batch...")
        batch = await generate_content_batch(persona_name, captions_count, scripts_count, pillar)
        top_scripts = batch["scripts_with_captions"]
        logger.info("Generated %d scored scripts. Top score: %.2f",
                    len(top_scripts), top_scripts[0]["combined_score"] if top_scripts else 0)

        # ── 2. Filter by score threshold ──────────────────────────────────
        threshold = settings.content_score_min
        qualified = [s for s in top_scripts if s["combined_score"] >= threshold]
        logger.info("%d/%d scripts above threshold (%.2f)", len(qualified), len(top_scripts), threshold)

        if not qualified:
            logger.warning("No scripts passed the score threshold. Lowering to best available.")
            qualified = top_scripts[:1]

        # ── 3. Generate images ────────────────────────────────────────────
        generated_images: list[GeneratedImage] = []
        if generate_images and not dry_run:
            logger.info("Step 3: Generating %d images via Stable Diffusion...", image_count)
            from persona_system.image_engine.generator import generate_image_batch
            img_data = await generate_image_batch(persona_name, image_count)
            for img in img_data:
                record = GeneratedImage(
                    persona=persona_name,
                    file_path=img["file_path"],
                    prompt=img["prompt"],
                    negative=img["negative"],
                    preset=img["preset"],
                    tags=img["tags"],
                )
                db.add(record)
            db.commit()
            generated_images = db.query(GeneratedImage).filter(
                GeneratedImage.persona == persona_name
            ).order_by(GeneratedImage.created_at.desc()).limit(image_count).all()
            logger.info("Saved %d images to DB", len(generated_images))
            results["images"] = [img.file_path for img in generated_images]

        # ── 4 + 5. Voice + Video for each qualified script ────────────────
        from persona_system.voice_engine.generator import PersonaVoiceGenerator
        from persona_system.video_engine_adapter.adapter import create_video

        voice_gen = PersonaVoiceGenerator(persona_name)
        import random

        for i, item in enumerate(qualified):
            script  = item["script"]
            caption = item["caption"]
            cap_score = item["caption_score"]
            scr_score = item["script_score"]
            combined  = item["combined_score"]

            # Pick an image (round-robin through available images)
            if generated_images:
                img = generated_images[i % len(generated_images)]
                image_path = Path(img.file_path)
            else:
                logger.warning("No images available — skipping video generation")
                image_path = None

            run_id = uuid.uuid4().hex[:8]
            out_dir = settings.media_root / "videos" / persona_name / datetime.now(timezone.utc).strftime("%Y%m%d")
            out_dir.mkdir(parents=True, exist_ok=True)
            audio_dir = settings.media_root / "audio" / persona_name / datetime.now(timezone.utc).strftime("%Y%m%d")
            audio_dir.mkdir(parents=True, exist_ok=True)

            # Generate voice
            voice_path = audio_dir / f"voice_{run_id}.mp3"
            if not dry_run:
                logger.info("Step 4: Generating voice for script %d/%d...", i+1, len(qualified))
                try:
                    await voice_gen.generate(script, voice_path, emotion="neutral")
                except Exception as e:
                    logger.error("Voice generation failed: %s", e)
                    voice_path = None

            # Generate video
            video_path = None
            if not dry_run and image_path and voice_path and voice_path.exists():
                logger.info("Step 5: Creating video %d/%d (type=%s)...", i+1, len(qualified), video_type)
                out_video = out_dir / f"video_{run_id}.mp4"
                try:
                    outputs = await create_video(
                        image_path=image_path,
                        audio_path=voice_path,
                        script=script,
                        output_path=out_video,
                        video_type=video_type,
                        persona_name=persona_name,
                        variants=1,
                    )
                    video_path = outputs[0] if outputs else None
                except Exception as e:
                    logger.error("Video creation failed: %s", e)

            # ── 6. Store ContentPiece to DB ───────────────────────────────
            piece = ContentPiece(
                persona=persona_name,
                caption=caption,
                script=script,
                pillar=pillar,
                script_score=scr_score,
                image_score=cap_score,
                combined_score=combined,
                image_id=generated_images[i % len(generated_images)].id if generated_images else None,
                voice_file=str(voice_path) if voice_path else None,
                video_file=str(video_path) if video_path else None,
                video_type=video_type,
                status=ContentStatus.APPROVED if video_path else ContentStatus.DRAFT,
            )
            db.add(piece)
            db.commit()
            db.refresh(piece)

            results["content_pieces"].append({
                "id": piece.id,
                "caption": caption[:60] + "..." if len(caption) > 60 else caption,
                "combined_score": combined,
                "video": str(video_path) if video_path else None,
            })
            logger.info("Content piece %d saved (score=%.2f)", piece.id, combined)

        # ── 7. Auto-schedule if not dry_run ───────────────────────────────
        if not dry_run and results["content_pieces"]:
            from persona_system.scheduler.scheduler import schedule_content_batch
            piece_ids = [p["id"] for p in results["content_pieces"]]
            scheduled = schedule_content_batch(persona_name, Platform.INSTAGRAM, piece_ids)
            results["scheduled"] = scheduled
            logger.info("Scheduled %d posts to Instagram", len(scheduled))

    finally:
        db.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="Persona content pipeline")
    parser.add_argument("--persona",      default="default")
    parser.add_argument("--pillar",       default=None)
    parser.add_argument("--captions",     type=int, default=20)
    parser.add_argument("--scripts",      type=int, default=3)
    parser.add_argument("--images",       type=int, default=5)
    parser.add_argument("--no-images",    action="store_true")
    parser.add_argument("--video-type",   choices=["talking", "loop"], default="loop")
    parser.add_argument("--dry-run",      action="store_true", help="Skip actual generation")
    args = parser.parse_args()

    result = asyncio.run(run_full_pipeline(
        persona_name=args.persona,
        pillar=args.pillar,
        captions_count=args.captions,
        scripts_count=args.scripts,
        generate_images=not args.no_images,
        image_count=args.images,
        video_type=args.video_type,
        dry_run=args.dry_run,
    ))

    import json
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
