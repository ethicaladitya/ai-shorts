"""
persona_system/automation/publisher.py
Unified publisher: reads the queue, dispatches to IG or OF, handles errors.
Designed to run as a cron job every 5 minutes.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from persona_system.shared.database import (
    ContentPiece, ContentStatus, Platform, ScheduledPost, SessionLocal
)
from persona_system.scheduler.scheduler import get_due_posts

logger = logging.getLogger(__name__)


async def publish_due_posts() -> list[dict]:
    """Check the queue and publish all due posts."""
    due = get_due_posts()
    if not due:
        logger.info("No posts due")
        return []

    results = []
    for post in due:
        db = SessionLocal()
        try:
            # Load content
            content = db.query(ContentPiece).filter(ContentPiece.id == post.content_id).first()
            if not content:
                logger.error("Content %d not found for post %d", post.content_id, post.id)
                continue

            video_path = Path(content.video_file) if content.video_file else None
            caption = content.caption or ""

            if not video_path or not video_path.exists():
                logger.error("Video file missing for post %d: %s", post.id, video_path)
                post.status = ContentStatus.FAILED
                post.error = "Video file not found"
                db.commit()
                continue

            # Dispatch to correct platform
            try:
                if post.platform == Platform.INSTAGRAM:
                    from persona_system.automation.instagram import post_to_instagram
                    media_id = await post_to_instagram(video_path, caption, post.id)
                elif post.platform == Platform.ONLYFANS:
                    from persona_system.automation.onlyfans import post_to_onlyfans
                    media_id = await post_to_onlyfans(video_path, caption, post.id)
                else:
                    logger.warning("Unknown platform: %s", post.platform)
                    continue

                results.append({
                    "post_id": post.id,
                    "platform": post.platform.value,
                    "media_id": media_id,
                    "status": "published",
                })

            except Exception as e:
                logger.error("Publish failed for post %d: %s", post.id, e)
                post.status = ContentStatus.FAILED
                post.error = str(e)
                db.commit()
                results.append({
                    "post_id": post.id,
                    "platform": post.platform.value if post.platform else "unknown",
                    "status": "failed",
                    "error": str(e),
                })
        finally:
            db.close()

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(publish_due_posts())
