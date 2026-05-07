"""
persona_system/automation/instagram.py
Instagram posting via Graph API with cookie/token persistence.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from persona_system.config.settings import settings
from persona_system.shared.database import (
    ContentPiece, ContentStatus, ScheduledPost, SessionLocal
)

logger = logging.getLogger(__name__)

IG_BASE = "https://graph.instagram.com/v19.0"


async def _upload_reel_to_container(
    video_path: Path,
    caption: str,
) -> str:
    """
    Step 1 of Instagram Reels upload:
    Create a media container and return container_id.
    Requires Instagram Basic Display API / Creator Studio access.
    """
    user_id = settings.instagram_user_id
    token = settings.instagram_access_token

    # For Reels: upload to FB servers first (resumable upload or CDN URL required)
    # Simple approach: POST to /reels endpoint with hosted video URL.
    # For local files, use Facebook Resumable Upload API.
    # Construct public URL via Cloudflare tunnel
    # video_path is local path, we need to convert it to a URL relative to BASE_URL
    # Assuming video_path is inside settings.media_root
    try:
        rel_path = video_path.relative_to(Path(settings.data_dir))
        public_url = f"{settings.base_url}/{rel_path}"
    except ValueError:
        # Fallback if not inside data_dir
        public_url = f"{settings.base_url}/persona_media/{video_path.name}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{IG_BASE}/{user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": public_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": token,
            }
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def _publish_container(container_id: str) -> str:
    """Step 2: Publish the media container and return media_id."""
    user_id = settings.instagram_user_id
    token = settings.instagram_access_token
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{IG_BASE}/{user_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def post_to_instagram(
    video_path: Path,
    caption: str,
    scheduled_post_id: int,
) -> str:
    """Full Instagram posting flow. Returns platform media_id."""
    logger.info("Posting to Instagram: %s", video_path.name)
    container_id = await _upload_reel_to_container(video_path, caption)

    import asyncio
    # Wait for container to be ready (IG processing)
    for _ in range(30):
        await asyncio.sleep(10)
        async with httpx.AsyncClient(timeout=15.0) as client:
            status_resp = await client.get(
                f"{IG_BASE}/{container_id}",
                params={"fields": "status_code", "access_token": settings.instagram_access_token}
            )
            status = status_resp.json().get("status_code", "")
            if status == "FINISHED":
                break
            if status == "ERROR":
                raise RuntimeError("Instagram container processing failed")

    media_id = await _publish_container(container_id)
    logger.info("Published to Instagram: media_id=%s", media_id)

    # Update DB
    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).filter(ScheduledPost.id == scheduled_post_id).first()
        if post:
            import datetime
            post.platform_post_id = media_id
            post.status = ContentStatus.PUBLISHED
            post.published_at = datetime.datetime.now(datetime.timezone.utc)
            db.commit()
    finally:
        db.close()

    return media_id
