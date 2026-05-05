"""Content fetching from WordPress and other sources."""
import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


async def fetch_wordpress_content(url: str) -> dict:
    """Fetch article content from a WordPress URL."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Try WP REST API first
        parsed = url.rstrip("/")
        slug = parsed.split("/")[-1]
        domain = "/".join(parsed.split("/")[:3])

        try:
            api_url = f"{domain}/wp-json/wp/v2/posts?slug={slug}"
            resp = await client.get(api_url)
            if resp.status_code == 200:
                posts = resp.json()
                if posts:
                    post = posts[0]
                    title = post.get("title", {}).get("rendered", "")
                    content_html = post.get("content", {}).get("rendered", "")
                    content_text = BeautifulSoup(content_html, "html.parser").get_text(separator="\n", strip=True)
                    return {"title": title, "content": content_text, "url": url, "source": "wp_api"}
        except Exception:
            logger.debug("WP REST API failed, falling back to scraping")

        # Fallback: scrape the page
        resp = await client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title = ""
        title_tag = soup.find("h1")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # Try common WP content selectors
        content = ""
        for selector in [".entry-content", ".post-content", "article", ".content"]:
            el = soup.select_one(selector)
            if el:
                content = el.get_text(separator="\n", strip=True)
                break

        if not content:
            content = soup.get_text(separator="\n", strip=True)[:5000]

        return {"title": title, "content": content, "url": url, "source": "scrape"}


async def fetch_rss_content(feed_url: str, limit: int = 5) -> list[dict]:
    """Fetch recent entries from an RSS feed."""
    import feedparser

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(feed_url)
        resp.raise_for_status()

    feed = feedparser.parse(resp.text)
    entries = []
    for entry in feed.entries[:limit]:
        content = ""
        if hasattr(entry, "content"):
            content = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            content = entry.summary

        content = BeautifulSoup(content, "html.parser").get_text(separator="\n", strip=True)
        entries.append({"title": entry.get("title", ""), "content": content, "url": entry.get("link", "")})

    return entries
