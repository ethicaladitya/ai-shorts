"""Stock video fetcher using the Pexels API."""
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "this", "that", "these", "those", "it", "its", "you", "your", "we",
    "our", "they", "their", "i", "me", "my", "and", "or", "but", "so",
    "if", "not", "no", "up", "out", "about", "just", "like", "how",
    "what", "when", "where", "who", "which", "all", "more", "very",
    "even", "only", "also", "than", "then", "there", "here", "now",
    "time", "every", "each", "many", "much", "some", "such", "while",
    "well", "get", "got", "let", "put", "take", "know", "think", "make",
    "want", "need", "use", "used", "using", "already", "ever", "never",
}


def _extract_keywords(topic: str, script: str) -> list[str]:
    """Extract up to 5 stock-video-friendly search terms."""
    results: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        t = term.strip().lower()
        if t and t not in seen and len(t) > 2:
            seen.add(t)
            results.append(t)

    # 1. Topic words first (most relevant)
    for w in re.split(r"[\s,\-]+", topic):
        w = re.sub(r"[^a-zA-Z]", "", w)
        if w and w.lower() not in _STOPWORDS and len(w) > 3:
            _add(w)

    # 2. High-frequency content words from script
    words = re.sub(r"[^a-zA-Z\s]", "", script.lower()).split()
    freq: dict[str, int] = {}
    for w in words:
        if w not in _STOPWORDS and len(w) > 4:
            freq[w] = freq.get(w, 0) + 1
    for w in sorted(freq, key=freq.get, reverse=True):  # type: ignore[arg-type]
        _add(w)
        if len(results) >= 5:
            break

    return results[:5]


def _best_portrait_url(video: dict) -> str | None:
    """Pick the best portrait-oriented video file URL."""
    files = video.get("video_files", [])
    # Prefer portrait files ≤ 1080px wide
    portrait = [f for f in files if f.get("width", 9999) <= 1080 and f.get("height", 0) >= f.get("width", 1) and "link" in f]
    if not portrait:
        # Fall back to any file with a link
        portrait = [f for f in files if "link" in f]
    if not portrait:
        return None
    # Prefer highest resolution
    portrait.sort(key=lambda f: f.get("width", 0) * f.get("height", 0), reverse=True)
    return portrait[0]["link"]


async def _search_pexels(query: str, api_key: str) -> list[dict]:
    """Search Pexels for portrait videos."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                PEXELS_VIDEO_URL,
                headers={"Authorization": api_key},
                params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 3},
            )
            if resp.status_code != 200:
                logger.warning(f"Pexels search '{query}': HTTP {resp.status_code}")
                return []
            return resp.json().get("videos", [])
    except Exception as e:
        logger.warning(f"Pexels search error for '{query}': {e}")
        return []


async def _download_clip(url: str, dest: Path) -> Path | None:
    """Stream-download a video clip."""
    try:
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        f.write(chunk)
        return dest
    except Exception as e:
        logger.warning(f"Clip download failed {url}: {e}")
        if dest.exists():
            dest.unlink(missing_ok=True)
        return None


async def fetch_stock_clips(
    topic: str,
    script: str,
    target_duration: float,
    api_key: str,
    temp_dir: Path,
) -> list[Path]:
    """
    Download up to 5 portrait stock clips from Pexels matching the script content.
    Returns list of local clip Paths (may be empty if API key missing or all fail).
    """
    if not api_key:
        return []

    keywords = _extract_keywords(topic, script)
    logger.info(f"Stock clip keywords: {keywords}")

    clips: list[Path] = []
    seen_ids: set[int] = set()

    for kw in keywords:
        if len(clips) >= 5:
            break
        videos = await _search_pexels(kw, api_key)
        for v in videos:
            vid_id = v.get("id", 0)
            if vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            url = _best_portrait_url(v)
            if not url:
                continue
            dest = temp_dir / f"stock_{vid_id}.mp4"
            if dest.exists() and dest.stat().st_size > 10_000:
                logger.info(f"Reusing cached clip: {dest.name}")
                clips.append(dest)
            else:
                path = await _download_clip(url, dest)
                if path:
                    clips.append(path)
            if len(clips) >= 5:
                break

    logger.info(f"Fetched {len(clips)} stock clips for '{topic}'")
    return clips
