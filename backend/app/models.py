import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, Float, JSON, Boolean, ForeignKey
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

    hooks = Column(JSON, default=list)
    selected_hook = Column(Text)

    script_raw = Column(Text)
    script_formatted = Column(Text)

    voice_file = Column(String(500))
    voice_duration = Column(Float)

    subtitle_file = Column(String(500))

    output_file = Column(String(500))
    thumbnail_file = Column(String(500))

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
    persona_id = Column(Integer, nullable=True)

    script = Column(Text)

    face_image_path = Column(String(500))
    voice_file = Column(String(500))
    did_talk_id = Column(String(200))
    output_file = Column(String(500))

    voice_provider = Column(String(100), default="")

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
    persona_id = Column(Integer, nullable=True)
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
    scene_images = Column(Text)
    voice_file = Column(String(500))
    talking_head_file = Column(String(500))
    subtitle_file = Column(String(500))
    output_file = Column(String(500))
    thumbnail_file = Column(String(500))

    estimated_cost = Column(Float, default=0.0)
    approval_decision = Column(String(50))

    # Extended campaign brief fields
    product_name = Column(String(300))
    product_description = Column(Text)
    product_url = Column(String(500))
    creator_name = Column(String(200))
    creator_instagram = Column(String(100))
    creator_tiktok = Column(String(100))
    creator_location = Column(String(200))
    creator_languages = Column(String(200))
    creator_age_range = Column(String(20))
    video_purpose = Column(String(50))
    main_goal = Column(String(50))
    target_audience = Column(String(300))
    cta_text = Column(String(200))
    hook_style = Column(String(50))
    tone = Column(String(50))
    script_language = Column(String(20), default="en")
    script_notes = Column(Text)
    brand_guidelines = Column(Text)
    example_videos = Column(Text)
    key_phrases = Column(Text)
    structure_flags = Column(Text)  # JSON of checked structure items
    style_flags = Column(Text)      # JSON of checked style flags

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    @property
    def scene_images_list(self) -> list[str]:
        if not self.scene_images:
            return []
        try:
            import json
            paths = json.loads(self.scene_images)
            if not isinstance(paths, list):
                return []
            from app.config import settings
            temp_dir_str = str(settings.temp_dir)
            urls = []
            for p in paths:
                p_str = str(p)
                if p_str.startswith(temp_dir_str):
                    urls.append(p_str.replace(temp_dir_str, "/temp", 1))
                else:
                    urls.append(p_str)
            return urls
        except Exception:
            return []

    @property
    def voice_url(self) -> str | None:
        if not self.voice_file:
            return None
        try:
            from app.config import settings
            temp_dir_str = str(settings.temp_dir)
            vf_str = str(self.voice_file)
            if vf_str.startswith(temp_dir_str):
                return vf_str.replace(temp_dir_str, "/temp", 1)
            return vf_str
        except Exception:
            return None

    @property
    def output_url(self) -> str | None:
        if not self.output_file:
            return None
        try:
            from pathlib import Path
            from app.config import settings
            p = Path(self.output_file)
            if p.exists():
                rel = p.relative_to(settings.ugc_output_dir.parent)
                return f"/output/{rel}"
        except Exception:
            pass
        return None

    @property
    def thumbnail_url(self) -> str | None:
        if not self.thumbnail_file:
            return None
        try:
            from pathlib import Path
            from app.config import settings
            p = Path(self.thumbnail_file)
            if p.exists():
                rel = p.relative_to(settings.ugc_output_dir.parent)
                return f"/output/{rel}"
        except Exception:
            pass
        return None


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
    user_email = Column(String(300), nullable=True, index=True)
    job_type = Column(String(50), index=True)
    job_id = Column(Integer, index=True)
    provider = Column(String(100))
    operation = Column(String(200))
    cost_usd = Column(Float, default=0.0)
    units = Column(Float, default=1.0)
    unit_label = Column(String(50), default="call")
    note = Column(String(500))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class PersonaAccount(Base):
    """Full persona definition — avatar identity, voice, personality, social connections."""
    __tablename__ = "persona_accounts"

    id = Column(Integer, primary_key=True, index=True)
    persona_name = Column(String(200), unique=True, nullable=False, index=True)
    display_name = Column(String(200))
    bio = Column(Text)
    personality = Column(Text)           # Personality description for AI behaviour
    reference_image_path = Column(String(500))
    avatar_style = Column(String(50), default="photo")  # photo | generated

    # Social accounts
    instagram_user_id = Column(String(200))
    instagram_username = Column(String(200))
    instagram_token_env = Column(String(200))
    onlyfans_username = Column(String(200))
    onlyfans_cookie_env = Column(String(200))
    tiktok_username = Column(String(200))
    tiktok_token_env = Column(String(200))

    # Voice config — supports "maya:aditya", "maya:of_model", "kokoro", etc.
    voice_provider = Column(String(100), default="maya")
    voice_profile_id = Column(String(200))

    # Content & DM config
    content_style = Column(String(100), default="ugc")
    dm_auto_reply = Column(Boolean, default=False)
    dm_guardrails = Column(Text)

    # Stats
    total_cost_usd = Column(Float, default=0.0)
    total_posts = Column(Integer, default=0)
    total_videos = Column(Integer, default=0)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DMThread(Base):
    __tablename__ = "dm_threads"

    id = Column(Integer, primary_key=True, index=True)
    persona_name = Column(String(200), index=True)
    platform = Column(String(50))
    external_thread_id = Column(String(300), unique=True, index=True)
    sender_handle = Column(String(300))
    sender_id = Column(String(300))

    last_message = Column(Text)
    last_message_at = Column(DateTime)
    category = Column(String(50))
    confidence = Column(Float, default=0.0)

    suggested_replies = Column(Text)
    approved_reply = Column(Text)
    reply_sent = Column(Boolean, default=False)
    reply_sent_at = Column(DateTime)
    auto_replied = Column(Boolean, default=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# ── RBAC: Users and per-user feature permissions ──────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, default="")
    picture = Column(String, default="")
    is_active = Column(Boolean, default=True)
    credits = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)


class UserPermissions(Base):
    """One row per user — each column gates a feature."""
    __tablename__ = "user_permissions"

    id = Column(Integer, primary_key=True)
    user_email = Column(String, nullable=False, unique=True, index=True)
    can_create_video = Column(Boolean, default=False)
    can_knowledge = Column(Boolean, default=False)
    can_avatar_video = Column(Boolean, default=False)
    can_ugc_video = Column(Boolean, default=False)
    can_personas = Column(Boolean, default=False)
    can_dms = Column(Boolean, default=False)
    can_costs = Column(Boolean, default=False)
    can_queue = Column(Boolean, default=False)
    can_settings = Column(Boolean, default=False)


class CreditTransaction(Base):
    """Credit purchase and usage log."""
    __tablename__ = "credit_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String, nullable=False, index=True)
    amount = Column(Integer, nullable=False)        # positive = credit, negative = debit
    balance_after = Column(Integer, default=0)
    type = Column(String(50), nullable=False)       # signup_bonus | purchase | video_generation | refund
    description = Column(String(500))
    payment_id = Column(String(200), nullable=True) # Instamojo payment ID
    payment_status = Column(String(50), nullable=True)
    amount_paid_inr = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
