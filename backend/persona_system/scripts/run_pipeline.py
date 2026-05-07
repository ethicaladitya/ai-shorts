"""
persona_system/scripts/run_pipeline.py
Full end-to-end content generation pipeline — Phase 2 upgrade.

Stages:
  1. Generate captions + scripts (content_engine)
  2. Score content — only proceed if above threshold
  3. Generate images (image_engine / SD) — platform-specific
  4. Optionally generate image gallery sets (OF)
  5. Generate voice (voice_engine / Voicebox)
  6. Create video (adapter: loop | animated_loop | talking | animated_talking)
  7. Store to DB
  8. Schedule for posting (per platform)

Run:
  python -m persona_system.scripts.run_pipeline \
    --persona default --pillar "late night energy" \
    --platforms instagram onlyfans \
    --video-mode animated_talking \
    --clip-provider svd --lipsync-provider latentsync
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
import uuid
import yaml
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


def _add_log(db, piece, message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    logger.info("[piece=%s] %s", piece.id, message)
    entry = f"[{timestamp}] {message}"
    piece.logs = (piece.logs or "") + ("\n" if piece.logs else "") + entry
    db.commit()


async def run_full_pipeline(
    persona_name: str = "default",
    pillar: str | None = None,
    captions_count: int = 20,
    scripts_count: int = 3,
    generate_images: bool = True,
    image_count: int = 5,
    generate_gallery: bool = False,
    gallery_size: int = 5,
    video_mode: str | None = None,
    video_clip_provider: str | None = None,
    lipsync_provider: str | None = None,
    image_provider: str = "sd",
    voice_provider: str | None = None,
    platforms: list[str] | None = None,
    content_tier: str = "standard",
    reference_image: str | None = None,  # override persona reference image for this run
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

    # Apply per-run overrides via env vars (picked up by settings singletons)
    import os
    if reference_image:
        os.environ["PERSONA_REFERENCE_IMAGE"] = reference_image
    if voice_provider:
        os.environ["VOICE_PROVIDER"] = voice_provider

    # Determine target platforms
    target_platforms = platforms or []
    if not target_platforms:
        if settings.ig_content_enabled:
            target_platforms.append("instagram")
        if settings.of_content_enabled:
            target_platforms.append("onlyfans")
    if not target_platforms:
        target_platforms = ["instagram"]

    effective_mode = video_mode or settings.default_video_mode

    results = {
        "persona": persona_name,
        "platforms": target_platforms,
        "video_mode": effective_mode,
        "content_pieces": [],
        "images": [],
        "galleries": [],
    }

    # Load persona config
    persona_config_path = settings.personas_dir / f"{persona_name}.yaml"
    persona: dict = {}
    if persona_config_path.exists():
        with open(persona_config_path) as f:
            persona = yaml.safe_load(f)

    # Resolve reference image: explicit arg > persona YAML > .env setting
    effective_reference_image = (
        reference_image
        or persona.get("reference_image", "")
        or settings.persona_reference_image
        or ""
    )
    if effective_reference_image:
        os.environ["PERSONA_REFERENCE_IMAGE"] = effective_reference_image
        logger.info("Using reference image: %s", effective_reference_image.split("/")[-1])

    try:
        # ── 1. Generate content batch ─────────────────────────────────────
        logger.info("Step 1: Generating content batch...")
        batch = await generate_content_batch(persona_name, captions_count, scripts_count, pillar)
        top_scripts = batch["scripts_with_captions"]
        logger.info(
            "Generated %d scored scripts. Top score: %.2f",
            len(top_scripts),
            top_scripts[0]["combined_score"] if top_scripts else 0,
        )

        # ── 2. Filter by score threshold ──────────────────────────────────
        threshold = settings.content_score_min
        qualified = [s for s in top_scripts if s["combined_score"] >= threshold]
        logger.info("%d/%d scripts above threshold (%.2f)", len(qualified), len(top_scripts), threshold)
        if not qualified:
            logger.warning("No scripts passed threshold. Using best available.")
            qualified = top_scripts[:1]

        # ── 3. Generate images ────────────────────────────────────────────
        generated_images: list[GeneratedImage] = []
        if generate_images and not dry_run:
            # Generate images for the primary platform (video format = 9:16)
            primary_platform = target_platforms[0]
            platform_preset = (
                "instagram_reels" if primary_platform == "instagram" else "onlyfans_video"
            )
            logger.info("Step 3: Generating %d images (preset=%s)...", image_count, platform_preset)

            from persona_system.image_engine.generator import generate_image_batch
            img_data = await generate_image_batch(
                persona_name,
                image_count,
                pillar=pillar,
                platform_preset=platform_preset,
                content_tier=content_tier,
                provider=image_provider,
            )
            for img in img_data:
                record = GeneratedImage(
                    persona=persona_name,
                    file_path=img["file_path"],
                    prompt=img["prompt"],
                    negative=img["negative"],
                    preset=img["preset"],
                    platform=img["platform"],
                    content_tier=img["content_tier"],
                    activity=img.get("activity", ""),
                    width=img.get("width", 768),
                    height=img.get("height", 1024),
                    tags=img["tags"],
                )
                db.add(record)
            db.commit()

            generated_images = (
                db.query(GeneratedImage)
                .filter(GeneratedImage.persona == persona_name)
                .order_by(GeneratedImage.created_at.desc())
                .limit(image_count)
                .all()
            )
            results["images"] = [img.file_path for img in generated_images]
            logger.info("Saved %d images to DB", len(generated_images))

        # ── 3b. Generate OF gallery sets (optional) ───────────────────────
        if generate_gallery and not dry_run and "onlyfans" in target_platforms:
            logger.info("Step 3b: Generating OnlyFans image gallery (size=%d)...", gallery_size)
            from persona_system.image_engine.gallery import generate_image_gallery
            gallery_imgs = await generate_image_gallery(
                persona_name=persona_name,
                pillar=pillar,
                gallery_size=gallery_size,
                platform_preset="onlyfans_photo",
                content_tier=content_tier,
            )
            for img in gallery_imgs:
                record = GeneratedImage(
                    persona=persona_name,
                    file_path=img["file_path"],
                    prompt=img["prompt"],
                    negative=img["negative"],
                    preset=img["preset"],
                    platform="onlyfans",
                    content_tier=img["content_tier"],
                    gallery_id=img["gallery_id"],
                    activity=img.get("activity", ""),
                    width=img.get("width", 1080),
                    height=img.get("height", 1350),
                    tags=img["tags"],
                )
                db.add(record)
            db.commit()
            results["galleries"].append({
                "gallery_id": gallery_imgs[0]["gallery_id"] if gallery_imgs else None,
                "count": len(gallery_imgs),
            })
            logger.info("Gallery saved: %d images", len(gallery_imgs))

        # ── 4 + 5. Voice + Video for each qualified script ────────────────
        from persona_system.voice_engine.generator import PersonaVoiceGenerator
        from persona_system.video_engine_adapter.adapter import create_video

        # Allow per-run voice provider override — set env var only
        # (settings.__dict__.pop doesn't work on Pydantic v2 models)
        if voice_provider:
            import os
            os.environ["VOICE_PROVIDER"] = voice_provider

        voice_gen = PersonaVoiceGenerator(persona_name)
        profile_name = persona.get("voice", {}).get("profile_name", "soft_feminine_v1")

        for i, item in enumerate(qualified):
            script   = item["script"]
            caption  = item["caption"]
            cap_score = item["caption_score"]
            scr_score = item["script_score"]
            combined  = item["combined_score"]

            # Create a ContentPiece per target platform
            for platform_str in target_platforms:
                try:
                    platform_enum = Platform(platform_str)
                except ValueError:
                    platform_enum = Platform.INSTAGRAM

                piece = ContentPiece(
                    persona=persona_name,
                    caption=caption,
                    script=script,
                    pillar=pillar,
                    script_score=scr_score,
                    image_score=cap_score,
                    combined_score=combined,
                    status=ContentStatus.PROCESSING,
                    video_mode=effective_mode,
                    platform=platform_enum,
                    content_tier=content_tier,
                )
                db.add(piece)
                db.commit()
                db.refresh(piece)

                _add_log(db, piece, f"Started processing ({i + 1}/{len(qualified)}) for {platform_str}")

                # Pick image
                image_path: Path | None = None
                if generated_images:
                    img = generated_images[i % len(generated_images)]
                    image_path = Path(img.file_path)
                    piece.image_id = img.id
                    _add_log(db, piece, f"Using image: {image_path.name}")
                else:
                    fallback_dir = settings.media_root.parent / "faces" / "fallback"
                    fallbacks = list(fallback_dir.glob("*.png")) + list(fallback_dir.glob("*.jpg"))
                    if fallbacks:
                        image_path = random.choice(fallbacks)
                        _add_log(db, piece, f"SD offline — fallback image: {image_path.name}")
                    else:
                        default_face = settings.media_root.parent / "faces" / "face_655555b9.png"
                        if default_face.exists():
                            image_path = default_face
                            _add_log(db, piece, "SD offline — default face fallback")
                        else:
                            _add_log(db, piece, "ERROR: No images available")

                if not image_path:
                    piece.status = ContentStatus.FAILED
                    piece.error = "No image available"
                    db.commit()
                    continue

                db.commit()

                # ── Voice ─────────────────────────────────────────────────
                run_id = uuid.uuid4().hex[:8]
                audio_dir = (
                    settings.media_root / "audio" / persona_name
                    / datetime.now(timezone.utc).strftime("%Y%m%d")
                )
                audio_dir.mkdir(parents=True, exist_ok=True)
                voice_path = audio_dir / f"voice_{run_id}.mp3"

                voice_path_result: Path | None = None
                if not dry_run:
                    emotion = random.choice(["neutral", "shy", "playful", "teasing", "whisper"])
                    _add_log(db, piece, f"Generating voice (profile={profile_name}, emotion={emotion})...")
                    try:
                        await asyncio.wait_for(
                            voice_gen.generate(
                                text=script,
                                output_path=voice_path,
                                profile_name=profile_name,
                                emotion=emotion,
                                variants=2,
                            ),
                            timeout=300,
                        )
                        voice_path_result = voice_path
                        piece.voice_file = str(voice_path)
                        _add_log(db, piece, "Voice generated successfully")
                    except asyncio.TimeoutError:
                        _add_log(db, piece, "Voice timed out (300s) — TTS endpoint may be slow or unreachable")
                    except Exception as e:
                        _add_log(db, piece, f"Voice failed: {e}")
                    db.commit()

                # ── Subtitles ─────────────────────────────────────────────
                subtitle_path: Path | None = None
                if voice_path_result:
                    try:
                        from app.services.subtitle_generator import generate_ass_subtitles
                        sub_path = audio_dir / f"subs_{run_id}.ass"
                        await generate_ass_subtitles(voice_path_result, sub_path)
                        subtitle_path = sub_path
                        _add_log(db, piece, "Subtitles generated")
                    except Exception as e:
                        _add_log(db, piece, f"Subtitle warning (non-fatal): {e}")

                # ── Video ─────────────────────────────────────────────────
                video_path_result: Path | None = None
                if not dry_run and image_path and voice_path_result:
                    out_dir = (
                        settings.media_root / "videos" / persona_name
                        / datetime.now(timezone.utc).strftime("%Y%m%d")
                    )
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_video = out_dir / f"video_{run_id}.mp4"

                    # Platform-specific sizes
                    if platform_str == "instagram":
                        vid_w, vid_h = 1080, 1920
                    else:
                        vid_w, vid_h = 1080, 1920

                    _add_log(db, piece, f"Creating video (mode={effective_mode})...")
                    try:
                        video_path_result = await asyncio.wait_for(
                            create_video(
                                image_path=image_path,
                                audio_path=voice_path_result,
                                script=script,
                                output_path=out_video,
                                video_mode=effective_mode,
                                persona_name=persona_name,
                                pillar=pillar,
                                persona=persona,
                                subtitle_path=subtitle_path,
                                video_clip_provider=video_clip_provider,
                                lipsync_provider=lipsync_provider,
                                width=vid_w,
                                height=vid_h,
                            ),
                            timeout=600,  # 10 min max
                        )
                        piece.video_file = str(video_path_result)
                        _add_log(db, piece, "Video rendered successfully")
                    except asyncio.TimeoutError:
                        _add_log(db, piece, "Video timed out (10 min)")
                        piece.error = "Video generation timed out"
                    except Exception as e:
                        _add_log(db, piece, f"Video failed: {e}")
                        piece.error = str(e)
                    db.commit()

                # ── Finalize ──────────────────────────────────────────────
                if video_path_result:
                    piece.status = ContentStatus.APPROVED
                    _add_log(db, piece, "Pipeline complete — piece approved")
                else:
                    piece.status = ContentStatus.FAILED
                    _add_log(db, piece, "Pipeline failed to produce video")
                db.commit()

                results["content_pieces"].append({
                    "id":            piece.id,
                    "platform":      platform_str,
                    "caption":       caption[:60] + "..." if len(caption) > 60 else caption,
                    "combined_score": combined,
                    "video_mode":    effective_mode,
                    "video":         str(video_path_result) if video_path_result else None,
                })

        # ── 7. Auto-schedule ──────────────────────────────────────────────
        if not dry_run and results["content_pieces"]:
            from persona_system.scheduler.scheduler import schedule_content_batch
            for platform_str in target_platforms:
                try:
                    platform_enum = Platform(platform_str)
                except ValueError:
                    continue
                piece_ids = [
                    p["id"] for p in results["content_pieces"]
                    if p["platform"] == platform_str and p["video"]
                ]
                if piece_ids:
                    scheduled = schedule_content_batch(persona_name, platform_enum, piece_ids)
                    results.setdefault("scheduled", {})[platform_str] = scheduled
                    logger.info("Scheduled %d posts to %s", len(scheduled), platform_str)

    finally:
        db.close()

    return results


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Persona content pipeline")
    parser.add_argument("--persona",         default="default")
    parser.add_argument("--pillar",          default=None)
    parser.add_argument("--captions",        type=int, default=20)
    parser.add_argument("--scripts",         type=int, default=3)
    parser.add_argument("--images",          type=int, default=5)
    parser.add_argument("--no-images",       action="store_true")
    parser.add_argument("--gallery",         action="store_true", help="Generate OF gallery sets")
    parser.add_argument("--gallery-size",    type=int, default=5)
    parser.add_argument("--video-mode",      choices=["loop", "animated_loop", "talking", "animated_talking"],
                        default=None)
    parser.add_argument("--clip-provider",   choices=["svd", "kling"], default=None)
    parser.add_argument("--lipsync-provider",choices=["latentsync", "hedra", "sadtalker"], default=None)
    parser.add_argument("--image-provider",  choices=["sd", "azure_dalle", "grok-3"], default="sd")
    parser.add_argument("--platforms",       nargs="+", choices=["instagram", "onlyfans"],
                        default=None)
    parser.add_argument("--content-tier",    choices=["teaser", "standard", "premium"],
                        default="standard")
    parser.add_argument("--dry-run",         action="store_true")
    args = parser.parse_args()

    result = asyncio.run(run_full_pipeline(
        persona_name=args.persona,
        pillar=args.pillar,
        captions_count=args.captions,
        scripts_count=args.scripts,
        generate_images=not args.no_images,
        image_count=args.images,
        generate_gallery=args.gallery,
        gallery_size=args.gallery_size,
        video_mode=args.video_mode,
        video_clip_provider=args.clip_provider,
        lipsync_provider=args.lipsync_provider,
        image_provider=args.image_provider,
        platforms=args.platforms,
        content_tier=args.content_tier,
        dry_run=args.dry_run,
    ))

    import json
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
