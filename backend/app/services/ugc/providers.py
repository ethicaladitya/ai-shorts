"""StageProvider Protocol — every UGC pipeline provider must satisfy this interface."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StageProvider(Protocol):
    """Common interface for all UGC pipeline stage providers."""

    name: str   # e.g. 'ollama', 'comfyui', 'kokoro', 'sadtalker', 'ffmpeg'
    mode: str   # 'local' | 'api'

    async def execute(self, input: dict) -> dict:
        """Execute the stage with the given input dict, return output dict."""
        ...

    def estimate_cost(self, input: dict) -> float:
        """Return estimated USD cost for this execution. Return 0.0 for local providers."""
        ...
