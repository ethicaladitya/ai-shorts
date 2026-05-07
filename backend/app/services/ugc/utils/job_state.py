"""Lightweight JSON sidecar for per-job state — complements the DB for batch visibility."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def _state_path(job_id: int | str) -> Path:
    return settings.ugc_temp_dir / str(job_id) / "state.json"


def write_state(job_id: int | str, **fields: Any) -> None:
    path = _state_path(job_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if path.exists():
            existing = json.loads(path.read_text())
        existing.update(fields)
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(existing, indent=2, default=str))
    except Exception as exc:
        logger.warning("Failed to write job state for %s: %s", job_id, exc)


def read_state(job_id: int | str) -> dict:
    path = _state_path(job_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}
