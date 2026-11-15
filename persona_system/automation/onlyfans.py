"""
persona_system/automation/onlyfans.py
OnlyFans posting via Playwright browser automation.
Handles login sessions, cookie persistence, and video/image uploads.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from persona_system.config.settings import settings
from persona_system.shared.database import (
    ContentPiece, ContentStatus, ScheduledPost, SessionLocal
)

logger = logging.getLogger(__name__)

OF_BASE_URL = "https://onlyfans.com"
COOKIES_FILE = Path(settings.onlyfans_cookies_file)


# ─────────────────────────────────────────────────────────────────────────────
# Session management
# ─────────────────────────────────────────────────────────────────────────────
async def get_browser_page(playwright_instance):
    """Launch Chromium with persistent cookies (no repeated logins)."""
    browser = await playwright_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )

    # Load saved cookies if they exist
    if COOKIES_FILE.exists():
        with COOKIES_FILE.open() as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        logger.info("Loaded %d saved cookies", len(cookies))

    page = await context.new_page()
    return browser, context, page


async def save_cookies(context) -> None:
    """Persist cookies after a successful session."""
    cookies = await context.cookies()
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with COOKIES_FILE.open("w") as f:
        json.dump(cookies, f)
    logger.info("Saved %d cookies to %s", len(cookies), COOKIES_FILE)


async def login_if_needed(page, context) -> bool:
    """Navigate to OF and login if not already authenticated."""
    await page.goto(OF_BASE_URL, wait_until="networkidle")
    # Check if already logged in
    if await page.query_selector('a[href="/my/posts"]'):
        logger.info("Already logged in to OnlyFans")
        return True

    logger.info("Logging in to OnlyFans...")
    await page.goto(f"{OF_BASE_URL}/login", wait_until="networkidle")

    email_field = await page.query_selector('input[name="email"]')
    pass_field  = await page.query_selector('input[name="password"]')
    if not email_field or not pass_field:
        raise RuntimeError("OnlyFans login form not found — check selectors")

    await email_field.fill(settings.onlyfans_email)
    await pass_field.fill(settings.onlyfans_password)
    await page.keyboard.press("Enter")
    await page.wait_for_load_state("networkidle", timeout=30_000)

    # Verify login
    if await page.query_selector('a[href="/my/posts"]'):
        await save_cookies(context)
        logger.info("OnlyFans login successful")
        return True
    else:
        raise RuntimeError("OnlyFans login failed — check credentials or 2FA requirement")


# ─────────────────────────────────────────────────────────────────────────────
# Post upload
# ─────────────────────────────────────────────────────────────────────────────
async def upload_post(
    page,
    video_path: Path,
    caption: str,
    price: float = 0.0,
) -> str:
    """
    Upload a video post to OnlyFans.
    Returns the post ID or URL.
    NOTE: Selectors may need updating as OF updates their UI.
    """
    await page.goto(f"{OF_BASE_URL}/my/posts/create", wait_until="networkidle")
    await page.wait_for_timeout(2000)

    # Find file input and upload
    file_input = await page.query_selector('input[type="file"]')
    if not file_input:
        # Some versions hide it; trigger via upload button
        upload_btn = await page.query_selector('[data-testid="upload-button"], .b-upload-list__btn')
        if upload_btn:
            await upload_btn.click()
            await page.wait_for_timeout(1000)
            file_input = await page.query_selector('input[type="file"]')

    if file_input:
        await file_input.set_input_files(str(video_path))
        logger.info("File uploaded to OF: %s", video_path.name)
    else:
        raise RuntimeError("Could not find file input on OnlyFans post creator")

    # Wait for upload to process
    await page.wait_for_timeout(5000)

    # Caption field
    caption_field = await page.query_selector(
        'textarea[name="text"], .ql-editor, [placeholder*="Write something"]'
    )
    if caption_field:
        await caption_field.click()
        await caption_field.fill(caption)
        await page.wait_for_timeout(500)

    # If PPV — set price
    if price > 0:
        lock_btn = await page.query_selector('[data-testid="lock-button"], .b-post-lock')
        if lock_btn:
            await lock_btn.click()
            await page.wait_for_timeout(500)
            price_input = await page.query_selector('input[type="number"][name="price"]')
            if price_input:
                await price_input.fill(str(price))

    # Submit
    submit_btn = await page.query_selector(
        'button[type="submit"], [data-testid="post-button"], .g-btn.m-btn-like'
    )
    if submit_btn:
        await submit_btn.click()
        await page.wait_for_load_state("networkidle", timeout=30_000)
        logger.info("OF post submitted for: %s", video_path.name)
    else:
        raise RuntimeError("Could not find submit button on OnlyFans")

    # Try to grab post URL from current URL or response
    current_url = page.url
    post_id = current_url.split("/")[-1] if "/" in current_url else "unknown"
    return post_id


# ─────────────────────────────────────────────────────────────────────────────
# Main posting function
# ─────────────────────────────────────────────────────────────────────────────
async def post_to_onlyfans(
    video_path: Path,
    caption: str,
    scheduled_post_id: int,
    price: float = 0.0,
) -> str:
    """Full OnlyFans posting flow with session management."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser, context, page = await get_browser_page(pw)
        try:
            await login_if_needed(page, context)
            post_id = await upload_post(page, video_path, caption, price)
            await save_cookies(context)
        finally:
            await browser.close()

    # Update DB
    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).filter(ScheduledPost.id == scheduled_post_id).first()
        if post:
            import datetime
            post.platform_post_id = post_id
            post.status = ContentStatus.PUBLISHED
            post.published_at = datetime.datetime.now(datetime.timezone.utc)
            db.commit()
    finally:
        db.close()

    logger.info("OnlyFans post complete: %s", post_id)
    return post_id
