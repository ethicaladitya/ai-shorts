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


class UGCJobStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING_SCRIPT = "generating_script"
    GENERATING_IMAGES = "generating_images"
    GENERATING_VOICE = "generating_voice"
    AWAITING_APPROVAL = "awaiting_approval"
    RENDERING_HEAD = "rendering_head"
    GENERATING_SUBTITLES = "generating_subtitles"
    ASSEMBLING = "assembling"
    COMPLETE = "complete"
    FAILED = "failed"


class UGCJob(Base):
    __tablename__ = "ugc_jobs"

    id = Column(Integer, primary_key=True, index=True)
    persona_name = Column(String(200), default="default")
    topic = Column(String(500))
    style = Column(String(100), default="ugc")
    platform = Column(String(50), default="tiktok")
    duration_target = Column(Integer, default=30)

    script_provider = Column(String(100), default="ollama")
    image_provider = Column(String(100), default="a1111")
    voice_provider = Column(String(100), default="kokoro")
    head_provider = Column(String(100), default="sadtalker")

    status = Column(Enum(UGCJobStatus), default=UGCJobStatus.PENDING)
    step = Column(String(200))
    progress = Column(Float, default=0.0)
    log = Column(Text, default="")
    error_message = Column(Text)

    script_raw = Column(Text)
    script_formatted = Column(Text)
    scene_images = Column(Text)  # JSON list of paths
    voice_file = Column(String(500))
    talking_head_file = Column(String(500))
    subtitle_file = Column(String(500))
    output_file = Column(String(500))
    thumbnail_file = Column(String(500))

    estimated_cost = Column(Float, default=0.0)
    approval_decision = Column(String(50))

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(200), unique=True, nullable=False, index=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CostLog(Base):
    """Every billable API call recorded here — one row per event."""
    __tablename__ = "cost_log"

    id = Column(Integer, primary_key=True, index=True)
    persona_name = Column(String(200), default="default", index=True)
    job_type = Column(String(50), index=True)   # ugc | avatar | video | image | voice | lipsync
    job_id = Column(Integer, index=True)         # FK to the job table (loose — no FK constraint)
    provider = Column(String(100))               # azure_gpt_image | elevenlabs | hedra | ...
    operation = Column(String(200))              # e.g. "generate_image", "tts", "lipsync"
    cost_usd = Column(Float, default=0.0)
    units = Column(Float, default=1.0)           # images generated, characters, seconds, etc.
    unit_label = Column(String(50), default="call")  # "image" | "chars" | "seconds" | "call"
    note = Column(String(500))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class PersonaAccount(Base):
    """Links a persona to its social media accounts and tracks per-persona state."""
    __tablename__ = "persona_accounts"

    id = Column(Integer, primary_key=True, index=True)
    persona_name = Column(String(200), unique=True, nullable=False, index=True)
    display_name = Column(String(200))
    bio = Column(Text)
    reference_image_path = Column(String(500))

    # Social account IDs
    instagram_user_id = Column(String(200))
    instagram_username = Column(String(200))
    onlyfans_username = Column(String(200))
    tiktok_username = Column(String(200))

    # Secrets stored as env-key references (never stored raw in DB)
    instagram_token_env = Column(String(200))    # env var name holding the token
    onlyfans_cookie_env = Column(String(200))

    # Voice + content config
    voice_provider = Column(String(100), default="voicebox")
    voice_profile_id = Column(String(200))       # Voicebox profile UUID
    content_style = Column(String(100), default="ugc")
    dm_auto_reply = Column(Boolean, default=False)
    dm_guardrails = Column(Text)                 # JSON list of forbidden topics

    # Stats
    total_cost_usd = Column(Float, default=0.0)
    total_posts = Column(Integer, default=0)
    total_videos = Column(Integer, default=0)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DMThread(Base):
    """Incoming DM threads from any platform, with auto-reply state."""
    __tablename__ = "dm_threads"

    id = Column(Integer, primary_key=True, index=True)
    persona_name = Column(String(200), index=True)
    platform = Column(String(50))               # instagram | onlyfans
    external_thread_id = Column(String(300), unique=True, index=True)
    sender_handle = Column(String(300))
    sender_id = Column(String(300))

    last_message = Column(Text)
    last_message_at = Column(DateTime)
    category = Column(String(50))               # cold | warm | high_value | auto_reply
    confidence = Column(Float, default=0.0)

    # Reply state
    suggested_replies = Column(Text)            # JSON list of 3 variants
    approved_reply = Column(Text)
    reply_sent = Column(Boolean, default=False)
    reply_sent_at = Column(DateTime)
    auto_replied = Column(Boolean, default=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
