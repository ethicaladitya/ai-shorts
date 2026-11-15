"""
Audio-Driven Cutter — maps shot specs to actual audio timestamps.

Instead of giving each shot equal duration, this module:
1. Runs Whisper word-level transcription on the audio
2. Finds natural cut points (pauses between words, sentence ends)
3. Assigns each planned shot to cover a contiguous audio segment
4. Returns (shot_spec, start_sec, end_sec) tuples for the assembler

This is what makes the video feel edited rather than rendered.
A real TikTok editor would cut on pauses and sentence breaks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.ugc.scene_planner import ShotSpec

logger = logging.getLogger(__name__)

# Minimum pause gap (seconds) between words to consider a natural cut point
_MIN_PAUSE_GAP = 0.18

# Maximum shot duration — a hard cap that also prevents any single shot from
# running longer than the Ken-Burns clip. Set just under CLIP_DURATION_SECS (7s).
# NOTE: _cut_on_pauses() computes an adaptive per-job max anyway; this is the fallback.
_MAX_SHOT_DURATION = 6.0

# Minimum shot duration (cuts faster than this feel jarring)
_MIN_SHOT_DURATION = 0.9


@dataclass
class TimedShot:
    """A shot spec with concrete audio timestamps."""
    shot: "ShotSpec"
    start: float      # audio start time (seconds)
    end: float        # audio end time (seconds)
    duration: float   # end - start

    @property
    def image_index(self) -> int:
        return self.shot.index


def map_shots_to_audio(
    shots: list["ShotSpec"],
    audio_path: Path,
    model_size: str = "base",
) -> list[TimedShot]:
    """
    Transcribe audio, find natural pause points, assign shots to segments.

    Falls back to uniform duration if transcription fails.
    """
    try:
        word_timestamps = _transcribe_word_timestamps(audio_path, model_size)
        if word_timestamps:
            return _cut_on_pauses(shots, word_timestamps)
    except Exception as exc:
        logger.warning("Audio-driven cut failed (%s) — falling back to uniform duration", exc)

    return _uniform_cuts(shots, _get_audio_duration(audio_path))


def _transcribe_word_timestamps(audio_path: Path, model_size: str = "base") -> list[dict]:
    """Run faster-whisper and return word-level timestamps."""
    from app.services.subtitle_generator import _get_model
    model = _get_model(model_size)
    segments, _ = model.transcribe(
        str(audio_path),
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
    )
    words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    return words


def _find_cut_candidates(words: list[dict]) -> list[float]:
    """
    Find timestamps that are good places to cut:
    - After sentence-ending punctuation (. ! ?)
    - At gaps between words longer than _MIN_PAUSE_GAP
    - Spread evenly if neither condition is met often enough
    """
    candidates: list[float] = []

    for i in range(len(words) - 1):
        word_end = words[i]["end"]
        next_start = words[i + 1]["start"]
        gap = next_start - word_end
        txt = words[i]["word"]

        is_sentence_end = any(txt.endswith(p) for p in (".", "!", "?", "...", "—"))
        is_natural_pause = gap >= _MIN_PAUSE_GAP

        if is_sentence_end or is_natural_pause:
            candidates.append(word_end + gap * 0.3)  # cut slightly into the pause

    return candidates


def _cut_on_pauses(shots: list["ShotSpec"], words: list[dict]) -> list[TimedShot]:
    """Assign shots to audio segments using natural pause cut points."""
    if not words:
        total = _estimate_duration_from_shots(shots)
        return _uniform_cuts(shots, total)

    total_duration = words[-1]["end"]
    cut_candidates = _find_cut_candidates(words)

    # We need exactly len(shots)-1 cuts to produce len(shots) segments
    n_cuts_needed = len(shots) - 1

    if len(cut_candidates) >= n_cuts_needed:
        # Spread cuts evenly across candidates to avoid clustering
        step = len(cut_candidates) / n_cuts_needed
        selected_cuts = [
            cut_candidates[min(int(i * step), len(cut_candidates) - 1)]
            for i in range(n_cuts_needed)
        ]
    else:
        # Not enough natural pauses — fill gaps with evenly spaced cuts
        selected_cuts = list(cut_candidates)
        while len(selected_cuts) < n_cuts_needed:
            # Add synthetic cuts at roughly even intervals in uncovered spans
            current_spans = [0.0] + selected_cuts + [total_duration]
            longest_span_idx = max(
                range(len(current_spans) - 1),
                key=lambda i: current_spans[i + 1] - current_spans[i]
            )
            midpoint = (current_spans[longest_span_idx] + current_spans[longest_span_idx + 1]) / 2
            selected_cuts.insert(longest_span_idx, midpoint)

    selected_cuts.sort()

    # Build timed shots from cut boundaries
    boundaries = [0.0] + selected_cuts + [total_duration]

    # Adaptive max: each shot can be at most 1.5× the average, capped by _MAX_SHOT_DURATION.
    # This ensures total duration is fully covered even for long audio.
    avg_duration = total_duration / len(shots)
    adaptive_max = min(_MAX_SHOT_DURATION, avg_duration * 1.5)

    timed: list[TimedShot] = []
    for i, shot in enumerate(shots):
        start = boundaries[i]
        end = boundaries[i + 1] if i + 1 < len(boundaries) else total_duration
        duration = end - start
        is_last = i == len(shots) - 1

        # Minimum shot floor (prevents jarring sub-second cuts on non-final shots)
        if duration < _MIN_SHOT_DURATION and not is_last:
            end = start + _MIN_SHOT_DURATION
            duration = _MIN_SHOT_DURATION

        # Maximum cap — but NEVER truncate the last shot: it must reach total_duration
        # so the audio isn't cut short. Trimming only happens in non-final shots.
        if duration > adaptive_max and not is_last:
            end = start + adaptive_max

        timed.append(TimedShot(shot=shot, start=start, end=end, duration=end - start))

    logger.info(
        "Audio-driven cut: %d shots, durations=%s",
        len(timed),
        [f"{t.duration:.1f}s" for t in timed],
    )
    return timed


def _uniform_cuts(shots: list["ShotSpec"], total_duration: float) -> list[TimedShot]:
    """Fallback: equal duration per shot."""
    per_shot = total_duration / len(shots) if shots else 2.0
    return [
        TimedShot(shot=s, start=i * per_shot, end=(i + 1) * per_shot, duration=per_shot)
        for i, s in enumerate(shots)
    ]


def _estimate_duration_from_shots(shots: list["ShotSpec"]) -> float:
    return sum(s.duration_hint for s in shots)


def _get_audio_duration(audio_path: Path) -> float:
    try:
        from app.services.video_renderer import get_audio_duration
        import asyncio
        loop = asyncio.new_event_loop()
        dur = loop.run_until_complete(get_audio_duration(audio_path))
        loop.close()
        return dur
    except Exception:
        return 30.0
