import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, Float, JSON, Boolean
from app.database import Base


class VideoStatus(str, enum.Enum):
    DRAFT = "draft"
    HOOKS_GENERATED = "hooks_generated"
    SCRIPT_READY = "script_ready"
    VOICE_GENERATING = "voice_generating"
    VOICE_READY = "voice_ready"
    RENDERING = "rendering"
    SUBTITLING = "subtitling"
    COMPLETE = "complete"
    FAILED = "failed"


class RenderJobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    topic = Column(String(500))
    wordpress_url = Column(String(1000))
    custom_notes = Column(Text)
    source_content = Column(Text)
    status = Column(Enum(VideoStatus), default=VideoStatus.DRAFT)

    # Hooks
    hooks = Column(JSON, default=list)
    selected_hook = Column(Text)

    # Script
    script_raw = Column(Text)
    script_formatted = Column(Text)

    # Voice
    voice_file = Column(String(500))
    voice_duration = Column(Float)

    # Subtitles
    subtitle_file = Column(String(500))

    # Output
    output_file = Column(String(500))
    thumbnail_file = Column(String(500))

    # Meta
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    error_message = Column(Text)


class RenderJob(Base):
    __tablename__ = "render_jobs"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, nullable=False, index=True)
    status = Column(Enum(RenderJobStatus), default=RenderJobStatus.QUEUED)
    step = Column(String(100))
    progress = Column(Float, default=0.0)
    log = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    error_message = Column(Text)


class KnowledgeEntry(Base):
    __tablename__ = "knowledge"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(100), nullable=False, index=True)
    title = Column(String(500))
    content = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AvatarJobStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING_SCRIPT = "generating_script"
    GENERATING_VOICE = "generating_voice"
    ANIMATING = "animating"
    ADDING_CAPTIONS = "adding_captions"
    COMPLETE = "complete"
    FAILED = "failed"


class AvatarJob(Base):
    __tablename__ = "avatar_jobs"

    id = Column(Integer, primary_key=True, index=True)
    topic = Column(String(500))
    custom_notes = Column(Text)

    # Generated script
    script = Column(Text)

    # Files
    face_image_path = Column(String(500))
    voice_file = Column(String(500))
    did_talk_id = Column(String(200))
    output_file = Column(String(500))

    # Status tracking
    status = Column(Enum(AvatarJobStatus), default=AvatarJobStatus.PENDING)
    step = Column(String(100))
    progress = Column(Float, default=0.0)
    log = Column(Text, default="")
    error_message = Column(Text)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(200), unique=True, nullable=False, index=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
