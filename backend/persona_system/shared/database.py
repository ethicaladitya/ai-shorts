"""
persona_system/shared/database.py
SQLAlchemy setup + all ORM models for the persona system.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, Integer, JSON, String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from persona_system.config.settings import settings

engine = create_engine(
    settings.persona_db_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.persona_db_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────
class ContentStatus(str, enum.Enum):
    DRAFT      = "draft"
    SCORED     = "scored"
    APPROVED   = "approved"
    SCHEDULED  = "scheduled"
    PUBLISHED  = "published"
    REJECTED   = "rejected"
    FAILED     = "failed"
    PROCESSING = "processing"


class Platform(str, enum.Enum):
    INSTAGRAM = "instagram"
    ONLYFANS  = "onlyfans"


class ContentTier(str, enum.Enum):
    TEASER   = "teaser"    # Safe for IG, short/clean
    STANDARD = "standard"  # Default full content
    PREMIUM  = "premium"   # OF-only extended / intimate


class VideoMode(str, enum.Enum):
    LOOP             = "loop"              # Static image + audio (cheapest)
    ANIMATED_LOOP    = "animated_loop"     # SVD/Kling clip, no lipsync
    TALKING          = "talking"           # Static image + lipsync
    ANIMATED_TALKING = "animated_talking"  # Animated clip + lipsync (best)


class DMCategory(str, enum.Enum):
    COLD       = "cold"
    WARM       = "warm"
    HIGH_VALUE = "high_value"
    AUTO_REPLY = "auto_reply"


class JobStatus(str, enum.Enum):
    QUEUED  = "queued"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Content
# ─────────────────────────────────────────────────────────────────────────────
class GeneratedImage(Base):
    __tablename__ = "generated_images"

    id           = Column(Integer, primary_key=True, index=True)
    persona      = Column(String(100), default="default")
    file_path    = Column(String(500))
    prompt       = Column(Text)
    negative     = Column(Text)
    preset       = Column(String(100))      # lighting preset name
    platform     = Column(String(50))       # "instagram" | "onlyfans" | "both"
    content_tier = Column(String(50), default="standard")
    gallery_id   = Column(String(64))       # UUID hex — groups a coherent set
    activity     = Column(String(200))      # activity scene description
    width        = Column(Integer, default=768)
    height       = Column(Integer, default=1024)
    score        = Column(Float)            # computed quality score 0-1
    tags         = Column(JSON, default=list)
    used_in      = Column(JSON, default=list)   # list of content_piece ids
    created_at   = Column(DateTime, default=utcnow)


class ContentPiece(Base):
    __tablename__ = "content_pieces"

    id             = Column(Integer, primary_key=True, index=True)
    persona        = Column(String(100), default="default")
    caption        = Column(Text)
    script         = Column(Text)
    pillar         = Column(String(100))
    script_score   = Column(Float)
    image_score    = Column(Float)
    combined_score = Column(Float)
    image_id       = Column(Integer)
    voice_file     = Column(String(500))
    video_file     = Column(String(500))
    thumbnail_file = Column(String(500))
    video_type     = Column(String(50))         # legacy: loop | talking
    video_mode     = Column(String(50), default="animated_talking")
    platform       = Column(Enum(Platform))
    content_tier   = Column(String(50), default="standard")
    gallery_id     = Column(String(64))
    status         = Column(Enum(ContentStatus), default=ContentStatus.DRAFT)
    scheduled_at   = Column(DateTime)
    published_at   = Column(DateTime)
    error          = Column(Text)
    logs           = Column(Text, default="")
    created_at     = Column(DateTime, default=utcnow)
    updated_at     = Column(DateTime, default=utcnow, onupdate=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling & posting
# ─────────────────────────────────────────────────────────────────────────────
class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id               = Column(Integer, primary_key=True, index=True)
    content_id       = Column(Integer, index=True)
    platform         = Column(Enum(Platform))
    persona          = Column(String(100))
    scheduled_at     = Column(DateTime, index=True)
    published_at     = Column(DateTime)
    platform_post_id = Column(String(200))
    status           = Column(Enum(ContentStatus), default=ContentStatus.SCHEDULED)
    error            = Column(Text)
    created_at       = Column(DateTime, default=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────────────────────────
class PostAnalytics(Base):
    __tablename__ = "post_analytics"

    id                = Column(Integer, primary_key=True, index=True)
    scheduled_post_id = Column(Integer, index=True)
    platform          = Column(Enum(Platform))
    persona           = Column(String(100))
    views             = Column(Integer, default=0)
    likes             = Column(Integer, default=0)
    comments          = Column(Integer, default=0)
    shares            = Column(Integer, default=0)
    saves             = Column(Integer, default=0)
    reach             = Column(Integer, default=0)
    engagement_rate   = Column(Float, default=0.0)
    performance_score = Column(Float)
    fetched_at        = Column(DateTime, default=utcnow)
    raw_data          = Column(JSON)


# ─────────────────────────────────────────────────────────────────────────────
# DM management
# ─────────────────────────────────────────────────────────────────────────────
class DMThread(Base):
    __tablename__ = "dm_threads"

    id                 = Column(Integer, primary_key=True, index=True)
    platform           = Column(Enum(Platform))
    platform_thread_id = Column(String(200), unique=True)
    persona            = Column(String(100))
    category           = Column(Enum(DMCategory))
    fan_username       = Column(String(200))
    last_message       = Column(Text)
    last_message_at    = Column(DateTime)
    reply_sent         = Column(Boolean, default=False)
    reply_text         = Column(Text)
    auto_replied       = Column(Boolean, default=False)
    approved_by_human  = Column(Boolean, default=False)
    created_at         = Column(DateTime, default=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Background jobs
# ─────────────────────────────────────────────────────────────────────────────
class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id          = Column(Integer, primary_key=True, index=True)
    job_type    = Column(String(100))
    payload     = Column(JSON)
    status      = Column(Enum(JobStatus), default=JobStatus.QUEUED)
    result      = Column(JSON)
    error       = Column(Text)
    created_at  = Column(DateTime, default=utcnow)
    started_at  = Column(DateTime)
    finished_at = Column(DateTime)


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_columns()


def _migrate_columns():
    """Safe ALTER TABLE for columns added in later versions (SQLite-compatible)."""
    from sqlalchemy import text, inspect as sa_inspect
    insp = sa_inspect(engine)
    with engine.connect() as conn:
        # generated_images migrations
        gi_cols = {c["name"] for c in insp.get_columns("generated_images")}
        for col_def in [
            "platform VARCHAR(50)",
            "content_tier VARCHAR(50) DEFAULT 'standard'",
            "gallery_id VARCHAR(64)",
            "activity VARCHAR(200)",
            "width INTEGER DEFAULT 768",
            "height INTEGER DEFAULT 1024",
        ]:
            col_name = col_def.split()[0]
            if col_name not in gi_cols:
                conn.execute(text(f"ALTER TABLE generated_images ADD COLUMN {col_def}"))

        # content_pieces migrations
        cp_cols = {c["name"] for c in insp.get_columns("content_pieces")}
        for col_def in [
            "thumbnail_file VARCHAR(500)",
            "video_mode VARCHAR(50) DEFAULT 'animated_talking'",
            "content_tier VARCHAR(50) DEFAULT 'standard'",
            "gallery_id VARCHAR(64)",
        ]:
            col_name = col_def.split()[0]
            if col_name not in cp_cols:
                conn.execute(text(f"ALTER TABLE content_pieces ADD COLUMN {col_def}"))

        conn.commit()


if __name__ == "__main__":
    init_db()
    print("Database initialised.")
