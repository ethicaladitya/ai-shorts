"""UGC voice service — wraps existing voice_provider.py with UGC-specific preprocessing."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path

from app.config import settings
from app.services.ugc.utils.retry import async_retry

logger = logging.getLogger(__name__)

# Map pacing markers to SSML-compatible pauses or spoken equivalents
_PAUSE_REPLACEMENT = ", "     # commas create natural pauses in TTS
_EMPHASIS_RE = re.compile(r"\[EMPHASIS\](.*?)\[/EMPHASIS\]", re.IGNORECASE)
_LAUGH_RE = re.compile(r"\[LAUGH\]", re.IGNORECASE)
_MARKER_RE = re.compile(r"\[/?[A-Z_]+\]", re.IGNORECASE)


def preprocess_for_tts(script_raw: str) -> str:
    """Convert pacing markers to TTS-friendly text."""
    text = script_raw

    # [PAUSE] → natural comma pause
    text = text.replace("[PAUSE]", _PAUSE_REPLACEMENT)

    # [EMPHASIS]word[/EMPHASIS] → keep word, drop tags
    text = _EMPHASIS_RE.sub(r"\1", text)

    # [LAUGH] → light interjection
    text = _LAUGH_RE.sub("heh, ", text)

    # Strip any remaining unknown markers
    text = _MARKER_RE.sub("", text)

    # Normalise whitespace
    text = re.sub(r"\s{2,}", " ", text).strip()

    return text


def _resolve_ugc_voice_provider(ugc_voice_provider: str):
    """Return an initialised VoiceProvider for UGC jobs.

    Priority: UGC_VOICE_PROVIDER → VOICE_PROVIDER → kokoro
    """
    provider_name = ugc_voice_provider or settings.voice_provider

    # Reuse the existing factory — it already knows how to build every provider
    from app.services.voice_provider import get_voice_provider
    return get_voice_provider(provider_name)


@async_retry(max_attempts=3, base_delay=3.0)
async def generate_ugc_voice(
    script_raw: str,
    output_dir: Path,
    provider: str = "kokoro",
    speed: float = 0.95,
) -> Path:
    """Generate voice audio for the UGC script.

    Returns path to the final MP3 file (speed-adjusted).
    """
    tts_text = preprocess_for_tts(script_raw)
    raw_path = output_dir / f"voice_raw_{uuid.uuid4().hex[:8]}.mp3"

    voice_provider = _resolve_ugc_voice_provider(provider)
    await voice_provider.generate_speech(tts_text, raw_path)

    if not raw_path.exists():
        raise RuntimeError(f"Voice provider produced no output at {raw_path}")

    # Apply speed adjustment for casual UGC delivery
    if abs(speed - 1.0) > 0.01:
        out_path = output_dir / f"voice_{uuid.uuid4().hex[:8]}.mp3"
        await _apply_speed(raw_path, out_path, speed)
        raw_path.unlink(missing_ok=True)
        return out_path

    return raw_path


async def _apply_speed(src: Path, dst: Path, speed: float) -> None:
    """Apply atempo speed filter via FFmpeg."""
    from app.services.video_renderer import FFMPEG
    cmd = [
        FFMPEG, "-y", "-i", str(src),
        "-filter:a", f"atempo={speed}",
        "-c:a", "libmp3lame", "-q:a", "2",
        str(dst),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("Speed adjustment failed (%s) — using original", stderr.decode()[:200])
        import shutil
        shutil.copy2(str(src), str(dst))
