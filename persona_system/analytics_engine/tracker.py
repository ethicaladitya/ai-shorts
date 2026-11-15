"""
persona_system/analytics_engine/tracker.py
Performance tracking, scoring, and feedback loop for content improvement.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from persona_system.config.settings import settings
from persona_system.shared.database import (
    PostAnalytics, ScheduledPost, ContentPiece, SessionLocal, Platform
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Instagram analytics fetch
# ─────────────────────────────────────────────────────────────────────────────
async def fetch_instagram_post_metrics(media_id: str) -> dict[str, Any]:
    """Fetch engagement metrics for a single IG media object."""
    url = f"https://graph.instagram.com/{media_id}/insights"
    params = {
        "metric": "impressions,reach,likes,comments,saved,shares",
        "access_token": settings.instagram_access_token,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    metrics = {}
    for item in data.get("data", []):
        metrics[item["name"]] = item["values"][0]["value"] if item.get("values") else 0
    return metrics


def compute_engagement_rate(metrics: dict[str, Any]) -> float:
    """engagement_rate = (likes + comments + saves + shares) / reach"""
    reach = max(metrics.get("reach", 1), 1)
    interactions = (
        metrics.get("likes", 0)
        + metrics.get("comments", 0)
        + metrics.get("saved", 0)
        + metrics.get("shares", 0)
    )
    return round(interactions / reach, 4)


def compute_performance_score(metrics: dict[str, Any], platform: str = "instagram") -> float:
    """
    Normalize metrics to a 0–1 performance score.
    Weights: engagement_rate 40%, reach 30%, saves 20%, comments 10%.
    """
    er = compute_engagement_rate(metrics)
    # Normalize reach (assume 10k reach = 1.0 for IG)
    reach_norm = min(metrics.get("reach", 0) / 10_000, 1.0)
    saves_norm = min(metrics.get("saved", 0) / 500, 1.0)
    comments_norm = min(metrics.get("comments", 0) / 100, 1.0)

    score = 0.4 * min(er / 0.05, 1.0) + 0.3 * reach_norm + 0.2 * saves_norm + 0.1 * comments_norm
    return round(score, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Store analytics to DB
# ─────────────────────────────────────────────────────────────────────────────
async def track_post_analytics(scheduled_post_id: int) -> PostAnalytics | None:
    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).filter(ScheduledPost.id == scheduled_post_id).first()
        if not post or not post.platform_post_id:
            return None

        if post.platform == Platform.INSTAGRAM:
            metrics = await fetch_instagram_post_metrics(post.platform_post_id)
        else:
            logger.warning("Analytics for %s not yet implemented", post.platform)
            return None

        er = compute_engagement_rate(metrics)
        score = compute_performance_score(metrics, post.platform.value)

        analytics = PostAnalytics(
            scheduled_post_id=scheduled_post_id,
            platform=post.platform,
            persona=post.persona,
            views=metrics.get("impressions", 0),
            likes=metrics.get("likes", 0),
            comments=metrics.get("comments", 0),
            saves=metrics.get("saved", 0),
            shares=metrics.get("shares", 0),
            reach=metrics.get("reach", 0),
            engagement_rate=er,
            performance_score=score,
            raw_data=metrics,
        )
        db.add(analytics)
        db.commit()
        db.refresh(analytics)
        logger.info("Analytics stored for post %d (score=%.3f)", scheduled_post_id, score)
        return analytics
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Pattern analysis
# ─────────────────────────────────────────────────────────────────────────────
def analyse_top_patterns(persona: str = "default", limit: int = 50) -> dict[str, Any]:
    """
    Analyse top-performing posts to extract patterns for the feedback loop.
    Returns: best_pillars, best_hours, best_captions_sample, avg_score.
    """
    db = SessionLocal()
    try:
        # Get analytics joined with scheduled posts + content pieces
        rows = (
            db.query(PostAnalytics, ScheduledPost, ContentPiece)
            .join(ScheduledPost, PostAnalytics.scheduled_post_id == ScheduledPost.id)
            .join(ContentPiece, ScheduledPost.content_id == ContentPiece.id)
            .filter(PostAnalytics.persona == persona)
            .order_by(PostAnalytics.performance_score.desc())
            .limit(limit)
            .all()
        )

        if not rows:
            return {"status": "no_data"}

        scores = [r[0].performance_score for r in rows]
        avg_score = sum(scores) / len(scores)

        # Best posting hours
        hour_scores: dict[int, list[float]] = {}
        for analytics, post, _ in rows:
            if post.published_at:
                h = post.published_at.hour
                hour_scores.setdefault(h, []).append(analytics.performance_score)
        best_hours = sorted(
            hour_scores.keys(),
            key=lambda h: sum(hour_scores[h]) / len(hour_scores[h]),
            reverse=True,
        )[:3]

        # Best pillars
        pillar_scores: dict[str, list[float]] = {}
        for analytics, _, content in rows:
            p = content.pillar or "unknown"
            pillar_scores.setdefault(p, []).append(analytics.performance_score)
        best_pillars = sorted(
            pillar_scores.keys(),
            key=lambda p: sum(pillar_scores[p]) / len(pillar_scores[p]),
            reverse=True,
        )[:3]

        # Sample top captions
        top_captions = [r[2].caption for r in rows[:5] if r[2].caption]

        return {
            "persona": persona,
            "avg_score": round(avg_score, 4),
            "best_pillars": best_pillars,
            "best_hours": best_hours,
            "top_captions_sample": top_captions,
            "total_posts_analysed": len(rows),
        }
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Feedback loop — improve generation prompts based on analytics
# ─────────────────────────────────────────────────────────────────────────────
async def generate_feedback_prompt_adjustments(persona: str = "default") -> dict[str, Any]:
    """
    Use top-performing content to generate LLM-based prompt adjustments.
    Returns suggestions for image prompts and caption tone.
    """
    from persona_system.shared.llm import generate

    patterns = analyse_top_patterns(persona)
    if patterns.get("status") == "no_data":
        return {"status": "no_data", "suggestions": []}

    top_captions = "\n".join(f"- {c}" for c in patterns.get("top_captions_sample", []))
    system = "You are an analytics expert for a social media creator. Be concise and specific."
    prompt = (
        f"These captions performed best for persona '{persona}':\n{top_captions}\n\n"
        f"Best content pillars: {', '.join(patterns.get('best_pillars', []))}\n"
        f"Best posting hours: {patterns.get('best_hours', [])}\n\n"
        "Provide 3 specific, actionable suggestions to improve:\n"
        "1. Caption tone/style\n"
        "2. Image prompt style\n"
        "3. Script emotional angle\n"
        "Format as JSON: [{\"area\": \"...\", \"suggestion\": \"...\"}]"
    )

    import json, re
    raw = await generate(prompt, system=system, temperature=0.5, max_tokens=400)
    try:
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        suggestions = json.loads(match.group()) if match else []
    except Exception:
        suggestions = []

    return {
        "persona": persona,
        "patterns": patterns,
        "suggestions": suggestions,
    }
