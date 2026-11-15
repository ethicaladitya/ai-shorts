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
    DRAFT = "draft"
    SCORED = "scored"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


class Platform(str, enum.Enum):
    INSTAGRAM = "instagram"
    ONLYFANS = "onlyfans"


class DMCategory(str, enum.Enum):
    COLD = "cold"
    WARM = "warm"
    HIGH_VALUE = "high_value"
    AUTO_REPLY = "auto_reply"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Content
# ─────────────────────────────────────────────────────────────────────────────
class GeneratedImage(Base):
    __tablename__ = "generated_images"

    id          = Column(Integer, primary_key=True, index=True)
    persona     = Column(String(100), default="default")
    file_path   = Column(String(500))
    prompt      = Column(Text)
    negative    = Column(Text)
    preset      = Column(String(100))   # lighting preset name
    score       = Column(Float)         # computed quality score 0-1
    tags        = Column(JSON, default=list)
    used_in     = Column(JSON, default=list)  # list of content_piece ids
    created_at  = Column(DateTime, default=utcnow)


class ContentPiece(Base):
    __tablename__ = "content_pieces"

    id              = Column(Integer, primary_key=True, index=True)
    persona         = Column(String(100), default="default")
    caption         = Column(Text)
    script          = Column(Text)
    pillar          = Column(String(100))   # content pillar
    script_score    = Column(Float)
    image_score     = Column(Float)
    combined_score  = Column(Float)
    image_id        = Column(Integer)
    voice_file      = Column(String(500))
    video_file      = Column(String(500))
    video_type      = Column(String(50))    # loop | talking
    status          = Column(Enum(ContentStatus), default=ContentStatus.DRAFT)
    platform        = Column(Enum(Platform))
    scheduled_at    = Column(DateTime)
    published_at    = Column(DateTime)
    error           = Column(Text)
    created_at      = Column(DateTime, default=utcnow)
    updated_at      = Column(DateTime, default=utcnow, onupdate=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling & posting
# ─────────────────────────────────────────────────────────────────────────────
class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id              = Column(Integer, primary_key=True, index=True)
    content_id      = Column(Integer, index=True)
    platform        = Column(Enum(Platform))
    persona         = Column(String(100))
    scheduled_at    = Column(DateTime, index=True)
    published_at    = Column(DateTime)
    platform_post_id= Column(String(200))  # IG / OF post ID returned after publish
    status          = Column(Enum(ContentStatus), default=ContentStatus.SCHEDULED)
    error           = Column(Text)
    created_at      = Column(DateTime, default=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────────────────────────
class PostAnalytics(Base):
    __tablename__ = "post_analytics"

    id              = Column(Integer, primary_key=True, index=True)
    scheduled_post_id = Column(Integer, index=True)
    platform        = Column(Enum(Platform))
    persona         = Column(String(100))
    views           = Column(Integer, default=0)
    likes           = Column(Integer, default=0)
    comments        = Column(Integer, default=0)
    shares          = Column(Integer, default=0)
    saves           = Column(Integer, default=0)
    reach           = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)
    performance_score = Column(Float)   # normalized 0-1
    fetched_at      = Column(DateTime, default=utcnow)
    raw_data        = Column(JSON)


# ─────────────────────────────────────────────────────────────────────────────
# DM management
# ─────────────────────────────────────────────────────────────────────────────
class DMThread(Base):
    __tablename__ = "dm_threads"

    id              = Column(Integer, primary_key=True, index=True)
    platform        = Column(Enum(Platform))
    platform_thread_id = Column(String(200), unique=True)
    persona         = Column(String(100))
    category        = Column(Enum(DMCategory))
    fan_username    = Column(String(200))
    last_message    = Column(Text)
    last_message_at = Column(DateTime)
    reply_sent      = Column(Boolean, default=False)
    reply_text      = Column(Text)
    auto_replied    = Column(Boolean, default=False)
    approved_by_human = Column(Boolean, default=False)
    created_at      = Column(DateTime, default=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Background jobs
# ─────────────────────────────────────────────────────────────────────────────
class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id          = Column(Integer, primary_key=True, index=True)
    job_type    = Column(String(100))   # generate_batch | publish_post | fetch_analytics
    payload     = Column(JSON)
    status      = Column(Enum(JobStatus), default=JobStatus.QUEUED)
    result      = Column(JSON)
    error       = Column(Text)
    created_at  = Column(DateTime, default=utcnow)
    started_at  = Column(DateTime)
    finished_at = Column(DateTime)


def init_db():
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("Database initialised.")
