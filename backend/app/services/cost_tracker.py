"""
app/services/cost_tracker.py
Central cost tracking — call record_cost() after every paid API call.
All costs are stored in CostLog and rolled up to PersonaAccount.total_cost_usd.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import CostLog, PersonaAccount

logger = logging.getLogger(__name__)

# ── Per-provider cost constants (USD) ─────────────────────────────────────────
# Update these when pricing changes; they're used for estimation too.
PROVIDER_COSTS: dict[str, dict] = {
    # Image generation
    "azure_gpt_image":      {"per_image": 0.04,  "unit": "image"},
    "azure_dalle3":         {"per_image": 0.04,  "unit": "image"},
    # LLM
    "azure_openai":         {"per_1k_tokens": 0.00015, "unit": "tokens"},
    "openai":               {"per_1k_tokens": 0.00015, "unit": "tokens"},
    # TTS
    "elevenlabs":           {"per_1k_chars": 0.003,    "unit": "chars"},
    "azure_tts":            {"per_1k_chars": 0.00016,  "unit": "chars"},
    # Lipsync / talking head
    "hedra":                {"per_video": 0.05,   "unit": "video"},
    "replicate_latentsync": {"per_video": 0.01,   "unit": "video"},
    "syncso":               {"per_video": 0.0,    "unit": "video"},
    "sadtalker_replicate":  {"per_video": 0.01,   "unit": "video"},
    "did":                  {"per_video": 0.10,   "unit": "video"},
    # Local / free
    "kokoro":               {"per_call": 0.0,  "unit": "call"},
    "voicebox":             {"per_call": 0.0,  "unit": "call"},
    "ollama":               {"per_call": 0.0,  "unit": "call"},
    "a1111":                {"per_call": 0.0,  "unit": "call"},
    "comfyui":              {"per_call": 0.0,  "unit": "call"},
    "ffmpeg":               {"per_call": 0.0,  "unit": "call"},
    "latentsync":           {"per_call": 0.0,  "unit": "call"},
    "slideshow":            {"per_call": 0.0,  "unit": "call"},
}


@contextmanager
def _db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_cost(
    provider: str,
    operation: str,
    cost_usd: float,
    persona_name: str = "default",
    job_type: str = "unknown",
    job_id: Optional[int] = None,
    units: float = 1.0,
    unit_label: str = "call",
    note: str = "",
    db: Optional[Session] = None,
) -> None:
    """Record a single billable event. Safe to call with cost_usd=0 for local ops."""
    def _write(session: Session) -> None:
        entry = CostLog(
            persona_name=persona_name,
            job_type=job_type,
            job_id=job_id,
            provider=provider,
            operation=operation,
            cost_usd=cost_usd,
            units=units,
            unit_label=unit_label,
            note=note,
        )
        session.add(entry)

        # Roll up to PersonaAccount if it exists
        acct = session.query(PersonaAccount).filter_by(persona_name=persona_name).first()
        if acct:
            acct.total_cost_usd = (acct.total_cost_usd or 0.0) + cost_usd
            acct.updated_at = datetime.now(timezone.utc)

        if cost_usd > 0:
            logger.info(
                "💰 $%.4f — %s/%s (persona=%s job=%s#%s)",
                cost_usd, provider, operation, persona_name, job_type, job_id,
            )

    if db is not None:
        _write(db)
    else:
        with _db() as session:
            _write(session)


def estimate_cost(provider: str, units: float = 1.0, unit_type: str = "call") -> float:
    """Quick cost estimate without writing to DB."""
    cfg = PROVIDER_COSTS.get(provider, {})
    if not cfg:
        return 0.0
    if unit_type == "image":
        return cfg.get("per_image", 0.0) * units
    if unit_type == "tokens":
        return cfg.get("per_1k_tokens", 0.0) * (units / 1000)
    if unit_type == "chars":
        return cfg.get("per_1k_chars", 0.0) * (units / 1000)
    if unit_type == "video":
        return cfg.get("per_video", 0.0) * units
    return cfg.get("per_call", 0.0) * units


# ── Aggregation queries ────────────────────────────────────────────────────────

def get_total_spend(db: Session, persona_name: Optional[str] = None) -> float:
    q = db.query(func.sum(CostLog.cost_usd))
    if persona_name:
        q = q.filter(CostLog.persona_name == persona_name)
    return float(q.scalar() or 0.0)


def get_spend_by_persona(db: Session) -> list[dict]:
    rows = (
        db.query(CostLog.persona_name, func.sum(CostLog.cost_usd).label("total"))
        .group_by(CostLog.persona_name)
        .order_by(func.sum(CostLog.cost_usd).desc())
        .all()
    )
    return [{"persona": r.persona_name, "total_usd": round(float(r.total), 4)} for r in rows]


def get_spend_by_provider(db: Session, persona_name: Optional[str] = None) -> list[dict]:
    q = db.query(CostLog.provider, func.sum(CostLog.cost_usd).label("total")).group_by(CostLog.provider)
    if persona_name:
        q = q.filter(CostLog.persona_name == persona_name)
    rows = q.order_by(func.sum(CostLog.cost_usd).desc()).all()
    return [{"provider": r.provider, "total_usd": round(float(r.total), 4)} for r in rows]


def get_spend_by_day(db: Session, days: int = 30, persona_name: Optional[str] = None) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = (
        db.query(
            func.date(CostLog.created_at).label("day"),
            func.sum(CostLog.cost_usd).label("total"),
        )
        .filter(CostLog.created_at >= since)
        .group_by(func.date(CostLog.created_at))
        .order_by(func.date(CostLog.created_at))
    )
    if persona_name:
        q = q.filter(CostLog.persona_name == persona_name)
    return [{"day": str(r.day), "total_usd": round(float(r.total), 4)} for r in q.all()]


def get_recent_events(db: Session, limit: int = 50, persona_name: Optional[str] = None) -> list[dict]:
    q = db.query(CostLog).order_by(CostLog.created_at.desc()).limit(limit)
    if persona_name:
        q = db.query(CostLog).filter(CostLog.persona_name == persona_name).order_by(CostLog.created_at.desc()).limit(limit)
    return [
        {
            "id": r.id,
            "persona": r.persona_name,
            "job_type": r.job_type,
            "job_id": r.job_id,
            "provider": r.provider,
            "operation": r.operation,
            "cost_usd": round(r.cost_usd, 4),
            "units": r.units,
            "unit_label": r.unit_label,
            "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in q.all()
    ]
