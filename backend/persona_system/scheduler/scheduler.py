"""
persona_system/scheduler/scheduler.py
Content calendar, randomized post timing, and queue management.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from persona_system.persona_engine.loader import load_persona
from persona_system.shared.database import (
    ContentPiece, ContentStatus, Platform, ScheduledPost, SessionLocal
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Time slot generator
# ─────────────────────────────────────────────────────────────────────────────
def generate_posting_slots(
    persona_name: str,
    platform: Platform,
    days_ahead: int = 7,
    start_from: datetime | None = None,
) -> list[datetime]:
    """
    Generate a week of randomized posting slots based on persona schedule config.
    Returns sorted list of UTC datetimes.
    """
    persona = load_persona(persona_name)
    schedule = persona.get("schedule", {})
    platform_sched = schedule.get(platform.value, {})

    posts_per_week = platform_sched.get("posts_per_week", 7)
    preferred_hours = platform_sched.get("preferred_hours", [9, 12, 18, 21])
    jitter_minutes = platform_sched.get("randomize_minutes", 30)

    start = start_from or datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)

    # Distribute posts across days
    all_slots: list[datetime] = []
    for day_offset in range(days_ahead):
        date = start + timedelta(days=day_offset)
        # Pick random hours from preferred list (without duplicates per day)
        hours_today = random.sample(
            preferred_hours,
            k=min(len(preferred_hours), max(1, posts_per_week // days_ahead))
        )
        for hour in hours_today:
            jitter = random.randint(-jitter_minutes, jitter_minutes)
            slot = date.replace(hour=hour) + timedelta(minutes=jitter)
            all_slots.append(slot)

    # Trim to posts_per_week total
    all_slots.sort()
    return all_slots[:posts_per_week]


# ─────────────────────────────────────────────────────────────────────────────
# Schedule content pieces
# ─────────────────────────────────────────────────────────────────────────────
def schedule_content_batch(
    persona_name: str,
    platform: Platform,
    content_ids: list[int],
    days_ahead: int = 7,
) -> list[dict[str, Any]]:
    """
    Assign available content pieces to posting slots.
    Returns list of created ScheduledPost records.
    """
    slots = generate_posting_slots(persona_name, platform, days_ahead)

    if len(content_ids) > len(slots):
        logger.warning(
            "More content (%d) than slots (%d) — truncating",
            len(content_ids), len(slots)
        )
        content_ids = content_ids[:len(slots)]
    elif len(content_ids) < len(slots):
        slots = slots[:len(content_ids)]

    db = SessionLocal()
    scheduled = []
    try:
        for content_id, slot in zip(content_ids, slots):
            # Update content status
            piece = db.query(ContentPiece).filter(ContentPiece.id == content_id).first()
            if piece:
                piece.status = ContentStatus.SCHEDULED
                piece.scheduled_at = slot

            post = ScheduledPost(
                content_id=content_id,
                platform=platform,
                persona=persona_name,
                scheduled_at=slot,
                status=ContentStatus.SCHEDULED,
            )
            db.add(post)
            scheduled.append({
                "content_id": content_id,
                "platform": platform.value,
                "scheduled_at": slot.isoformat(),
            })

        db.commit()
        logger.info(
            "Scheduled %d posts for %s on %s",
            len(scheduled), persona_name, platform.value
        )
        return scheduled
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Queue fetcher — what's due to post right now
# ─────────────────────────────────────────────────────────────────────────────
def get_due_posts(platform: Platform | None = None) -> list[ScheduledPost]:
    """Return all posts that are due (scheduled_at ≤ now) and not yet published."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        query = db.query(ScheduledPost).filter(
            ScheduledPost.status == ContentStatus.SCHEDULED,
            ScheduledPost.scheduled_at <= now,
        )
        if platform:
            query = query.filter(ScheduledPost.platform == platform)
        return query.order_by(ScheduledPost.scheduled_at).all()
    finally:
        db.close()


def get_upcoming_posts(days: int = 7, platform: Platform | None = None) -> list[dict]:
    """Return scheduled posts for the next N days (for dashboard display)."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        query = db.query(ScheduledPost).filter(
            ScheduledPost.scheduled_at.between(now, end)
        )
        if platform:
            query = query.filter(ScheduledPost.platform == platform)
        posts = query.order_by(ScheduledPost.scheduled_at).all()
        return [
            {
                "id": p.id,
                "content_id": p.content_id,
                "platform": p.platform.value if p.platform else None,
                "persona": p.persona,
                "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
                "status": p.status.value if p.status else None,
            }
            for p in posts
        ]
    finally:
        db.close()
