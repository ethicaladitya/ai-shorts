"""
persona_system/scripts/fetch_analytics.py
Cron script: fetch analytics for all recently published posts.
"""
import asyncio
import logging
from persona_system.shared.database import ScheduledPost, ContentStatus, SessionLocal
from persona_system.analytics_engine.tracker import track_post_analytics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fetch_analytics")


async def main():
    db = SessionLocal()
    try:
        posts = db.query(ScheduledPost).filter(
            ScheduledPost.status == ContentStatus.PUBLISHED,
            ScheduledPost.platform_post_id.isnot(None),
        ).order_by(ScheduledPost.published_at.desc()).limit(50).all()
        post_ids = [p.id for p in posts]
    finally:
        db.close()

    logger.info("Fetching analytics for %d posts...", len(post_ids))
    for pid in post_ids:
        try:
            await track_post_analytics(pid)
        except Exception as e:
            logger.warning("Analytics fetch failed for post %d: %s", pid, e)

    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
