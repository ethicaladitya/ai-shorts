"""
Retention Optimization Engine — scores and improves UGC content pre-render.

Responsibilities:
1. Hook quality scoring (is the first sentence scroll-stopping?)
2. Pacing analysis (avg shot duration — flag if too slow)
3. Scene variety check (no 3+ identical framings in a row)
4. Script length check (not padding the video)

Designed to run BEFORE the approval checkpoint so issues can be caught early.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.ugc.scene_planner import ShotSpec
    from app.services.ugc.script_generator import UGCScript

logger = logging.getLogger(__name__)

# Hook quality: first N words that must hook the viewer
HOOK_WORD_COUNT = 10

# Pacing thresholds
MAX_AVG_SHOT_DURATION = 5.0  # seconds — above this feels like slideshow; 2-5s is TikTok creator range
MIN_AVG_SHOT_DURATION = 1.0  # seconds — below this is too frantic

# Minimum distinct framings across consecutive shots
MAX_CONSECUTIVE_SAME_FRAMING = 2


@dataclass
class RetentionScore:
    hook_score: float           # 0.0–1.0, higher = more scroll-stopping
    hook_issues: list[str]      # reasons hook may underperform
    pacing_ok: bool
    pacing_avg_secs: float
    variety_ok: bool
    variety_issues: list[str]
    overall_ok: bool
    suggestions: list[str]


def score_retention(
    script: "UGCScript",
    shots: list["ShotSpec"],
    duration_target: int = 30,
) -> RetentionScore:
    """Score the script + shot plan for retention potential."""
    hook_score, hook_issues = _score_hook(script.hook)
    pacing_ok, pacing_avg = _check_pacing(shots)
    variety_ok, variety_issues = _check_variety(shots)

    suggestions: list[str] = []
    if hook_score < 0.5:
        suggestions.append("Rewrite hook — it sounds like an intro, not a pattern interrupt")
    if not pacing_ok and pacing_avg > MAX_AVG_SHOT_DURATION:
        suggestions.append(f"Add more shots — avg {pacing_avg:.1f}s/shot is too slow, aim for <3s")
    if not variety_ok:
        suggestions.extend(variety_issues)
    if script.word_count > _word_limit_for_duration(duration_target) * 1.15:
        suggestions.append(f"Script too long ({script.word_count} words for {duration_target}s) — trim or it'll rush")

    overall_ok = hook_score >= 0.4 and pacing_ok and variety_ok

    return RetentionScore(
        hook_score=hook_score,
        hook_issues=hook_issues,
        pacing_ok=pacing_ok,
        pacing_avg_secs=pacing_avg,
        variety_ok=variety_ok,
        variety_issues=variety_issues,
        overall_ok=overall_ok,
        suggestions=suggestions,
    )


def _score_hook(hook_text: str) -> tuple[float, list[str]]:
    """Score the hook sentence for scroll-stopping potential."""
    issues: list[str] = []
    score = 1.0

    lower = hook_text.lower().strip()

    # Penalise generic openers
    generic_openers = [
        "hey", "hi ", "hello", "welcome", "today i", "today we",
        "in this video", "so today", "alright so",
        "i want to", "i'm going to", "let me tell",
    ]
    for opener in generic_openers:
        if lower.startswith(opener):
            score -= 0.35
            issues.append(f"Starts with generic opener: '{opener}'")
            break

    # Reward specificity indicators
    specific_signals = [
        "nobody told me", "i found out", "turns out", "i stopped", "i started",
        "the reason", "this thing", "one thing", "the only", "actually",
        "genuinely", "lowkey", "okay so", "not to be",
    ]
    has_specific = any(sig in lower for sig in specific_signals)
    if has_specific:
        score += 0.2

    # Penalise questions (questions as hooks perform worse than statements on TikTok)
    if lower.strip().endswith("?"):
        score -= 0.15
        issues.append("Hook ends in question — statements outperform questions on TikTok")

    # Penalise if too short (under 6 words) — probably cut off
    word_count = len(hook_text.split())
    if word_count < 6:
        score -= 0.2
        issues.append(f"Hook too short ({word_count} words) — may not have enough time to hook")

    return min(max(round(score, 2), 0.0), 1.0), issues


def _check_pacing(shots: list["ShotSpec"]) -> tuple[bool, float]:
    if not shots:
        return False, 0.0
    avg = sum(s.duration_hint for s in shots) / len(shots)
    ok = MIN_AVG_SHOT_DURATION <= avg <= MAX_AVG_SHOT_DURATION
    return ok, round(avg, 2)


def _check_variety(shots: list["ShotSpec"]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    consecutive = 1
    max_consecutive = 1

    for i in range(1, len(shots)):
        if shots[i].framing == shots[i - 1].framing:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 1

    if max_consecutive > MAX_CONSECUTIVE_SAME_FRAMING:
        issues.append(
            f"{max_consecutive} consecutive shots with same framing — add variety"
        )

    # Check for environment variety
    envs = [s.environment for s in shots]
    unique_envs = len(set(envs))
    if len(shots) >= 4 and unique_envs < 2:
        issues.append("All shots in same environment — vary locations for more social-native feel")

    return len(issues) == 0, issues


def _word_limit_for_duration(duration: int) -> int:
    return {15: 75, 30: 150, 60: 300}.get(duration, 150)


def log_retention_score(score: RetentionScore, job_id: int) -> None:
    """Log retention analysis to the pipeline log."""
    status = "✓" if score.overall_ok else "⚠"
    logger.info(
        "%s Retention [job %d]: hook=%.2f, pacing=%.1fs/shot, variety=%s",
        status, job_id, score.hook_score, score.pacing_avg_secs,
        "ok" if score.variety_ok else "issues",
    )
    for suggestion in score.suggestions:
        logger.info("  → %s", suggestion)
