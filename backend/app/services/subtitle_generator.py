"""Subtitle generation using faster-whisper (no compilation required)."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Model cache: load once per process
_model_cache: dict = {}

# ── ASS style: TikTok-style large bold centred captions ─────────────────────
_ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,80,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,4,1,2,60,60,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _get_model(model_size: str = "base"):
    """Load (or retrieve cached) WhisperModel."""
    from faster_whisper import WhisperModel

    if model_size not in _model_cache:
        logger.info(f"Loading Whisper model '{model_size}'...")
        _model_cache[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model_cache[model_size]


def _format_srt_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_ass_timestamp(seconds: float) -> str:
    """H:MM:SS.cc  (centiseconds)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _segments_to_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = _format_srt_timestamp(seg.start)
        end = _format_srt_timestamp(seg.end)
        text = seg.text.strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


async def generate_subtitles(audio_path: Path, output_path: Path, model: str = "base", timeout: float = 300.0) -> Path:
    """Generate SRT subtitles from audio using faster-whisper."""
    import asyncio

    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _transcribe():
        whisper_model = _get_model(model)
        segments, _ = whisper_model.transcribe(
            str(audio_path), beam_size=5, word_timestamps=False, vad_filter=True,
        )
        return list(segments)

    loop = asyncio.get_event_loop()
    try:
        segments = await asyncio.wait_for(loop.run_in_executor(None, _transcribe), timeout=timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Subtitle generation timed out after {timeout:.0f}s")
    srt_content = _segments_to_srt(segments)
    output_path.write_text(srt_content, encoding="utf-8")
    logger.info(f"SRT subtitles generated: {output_path} ({len(segments)} segments)")
    return output_path


async def generate_ass_subtitles(audio_path: Path, output_path: Path, model: str = "base", timeout: float = 300.0) -> Path:
    """
    Generate TikTok-style ASS subtitles.

    • Word-level Whisper timestamps → grouped into 2-3 word chunks
    • Large bold centred white text with thick outline + shadow
    • ALL CAPS for punch
    """
    import asyncio

    def _transcribe():
        whisper_model = _get_model(model)
        segments, _ = whisper_model.transcribe(
            str(audio_path),
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
        )
        words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    words.append({"word": w.word, "start": w.start, "end": w.end})
        return words

    loop = asyncio.get_event_loop()
    try:
        words = await asyncio.wait_for(loop.run_in_executor(None, _transcribe), timeout=timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Subtitle generation timed out after {timeout:.0f}s")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not words:
        output_path.write_text(_ASS_HEADER, encoding="utf-8")
        return output_path

    # ── Group into ≤3-word chunks, breaking at sentence boundaries ──────────
    MAX_WORDS = 3
    MAX_DURATION = 2.0   # seconds per caption frame

    events: list[tuple[float, float, str]] = []
    chunk: list[dict] = []
    chunk_start: float | None = None

    def _flush():
        nonlocal chunk, chunk_start
        if not chunk:
            return
        text = " ".join(w["word"].strip() for w in chunk).strip().upper()
        end = chunk[-1]["end"]
        if text:
            events.append((chunk_start, end, text))  # type: ignore[arg-type]
        chunk = []
        chunk_start = None

    for w in words:
        txt = w["word"].strip()
        if not txt:
            continue
        if chunk_start is None:
            chunk_start = w["start"]
        chunk.append(w)
        is_sentence_end = any(txt.endswith(p) for p in (".", "!", "?"))
        too_long = (w["end"] - chunk_start) >= MAX_DURATION
        if len(chunk) >= MAX_WORDS or is_sentence_end or too_long:
            _flush()

    _flush()

    # ── Write ASS ────────────────────────────────────────────────────────────
    lines = [_ASS_HEADER.rstrip()]
    for start, end, text in events:
        t_s = _format_ass_timestamp(start)
        t_e = _format_ass_timestamp(end + 0.05)
        # Escape ASS special chars
        safe = text.replace("{", "\\{").replace("}", "\\}")
        lines.append(f"Dialogue: 0,{t_s},{t_e},Caption,,0,0,0,,{safe}")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"ASS subtitles generated: {output_path} ({len(events)} cues)")
    return output_path


async def generate_word_level_subtitles(audio_path: Path, output_path: Path) -> Path:
    """Generate word-level JSON subtitles for animated text display."""
    import asyncio
    import json

    def _transcribe():
        whisper_model = _get_model("base")
        segments, _ = whisper_model.transcribe(
            str(audio_path), beam_size=5, word_timestamps=True,
        )
        words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
        return words

    loop = asyncio.get_event_loop()
    words = await loop.run_in_executor(None, _transcribe)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(__import__("json").dumps(words, indent=2))
    return output_path



def _format_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _segments_to_srt(segments) -> str:
    """Convert faster-whisper segments to SRT format."""
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = _format_timestamp(seg.start)
        end = _format_timestamp(seg.end)
        text = seg.text.strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


async def generate_subtitles(audio_path: Path, output_path: Path, model: str = "base") -> Path:
    """Generate SRT subtitles from audio using faster-whisper."""
    import asyncio

    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _transcribe():
        whisper_model = _get_model(model)
        segments, _ = whisper_model.transcribe(
            str(audio_path),
            beam_size=5,
            word_timestamps=False,
            vad_filter=True,
        )
        return list(segments)  # consume generator in thread

    # Run in thread to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    segments = await loop.run_in_executor(None, _transcribe)

    srt_content = _segments_to_srt(segments)
    output_path.write_text(srt_content, encoding="utf-8")

    logger.info(f"Subtitles generated: {output_path} ({len(segments)} segments)")
    return output_path


async def generate_word_level_subtitles(audio_path: Path, output_path: Path) -> Path:
    """Generate word-level JSON subtitles for animated text display."""
    import asyncio
    import json

    def _transcribe():
        whisper_model = _get_model("base")
        segments, info = whisper_model.transcribe(
            str(audio_path),
            beam_size=5,
            word_timestamps=True,
        )
        words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
        return words

    loop = asyncio.get_event_loop()
    words = await loop.run_in_executor(None, _transcribe)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(words, indent=2))
    return output_path

