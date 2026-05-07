"""Per-job temp directory lifecycle management."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings


class TempJobDir:
    """Context manager that creates a per-job temp directory and cleans it up on exit.

    Usage:
        async with TempJobDir(job_id) as tmp:
            images_dir = tmp / "images"
    """

    def __init__(self, job_id: int | str, keep: bool = False):
        self.path: Path = settings.ugc_temp_dir / str(job_id)
        self._keep = keep

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "images").mkdir(exist_ok=True)
        (self.path / "audio").mkdir(exist_ok=True)
        (self.path / "head").mkdir(exist_ok=True)
        return self.path

    def __exit__(self, *_):
        if not self._keep and self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)

    # Allow use without context manager (pipeline keeps files until job complete)
    def create(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        for sub in ("images", "audio", "head"):
            (self.path / sub).mkdir(exist_ok=True)
        return self.path

    def cleanup(self):
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
