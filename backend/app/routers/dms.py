"""app/routers/dms.py — DM webhook receiver + auto-reply with guardrails."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.models import DMThread, PersonaAccount

templates = Jinja2Templates(directory="app/templates")

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dms", tags=["dms"])

# ── Guardrail topics (global fallback — per-persona adds more) ─────────────────
_GLOBAL_FORBIDDEN = [
    "meet in person", "real name", "home address", "phone number",
    "other platform", "payment outside", "refund",
]


def _check_guardrails(text: str, extra: list[str]) -> bool:
    """Return True if message violates any guardrail."""
    lower = text.lower()
    for phrase in _GLOBAL_FORBIDDEN + extra:
        if phrase.lower() in lower:
            return True
    return False


async def _classify_and_reply(thread_id: int) -> None:
    """Background: classify DM and optionally auto-send reply."""
    from app.database import SessionLocal
    from persona_system.dm_engine.handler import classify_dm, generate_dm_replies
    from persona_system.shared.llm import generate as llm_generate

    db = SessionLocal()
    try:
        thread = db.query(DMThread).filter(DMThread.id == thread_id).first()
        if not thread:
            return

        persona_acct = db.query(PersonaAccount).filter_by(persona_name=thread.persona_name).first()
        persona_guardrails: list[str] = json.loads(
            (persona_acct.dm_guardrails if persona_acct else None) or "[]"
        )

        # Check guardrails first
        if _check_guardrails(thread.last_message or "", persona_guardrails):
            thread.category = "blocked"
            thread.suggested_replies = json.dumps(["Sorry, I can't discuss that topic."])
            db.commit()
            logger.info("DM %d blocked by guardrail", thread_id)
            return

        # Classify
        try:
            classification = await classify_dm(thread.last_message or "", thread.persona_name)
            thread.category = classification.get("category", "warm")
            thread.confidence = float(classification.get("confidence", 0.5))
        except Exception as e:
            logger.warning("DM classification failed: %s", e)
            thread.category = "warm"
            thread.confidence = 0.5

        # Generate reply variants
        try:
            replies = await generate_dm_replies(
                message=thread.last_message or "",
                category=thread.category,
                persona_name=thread.persona_name,
            )
            thread.suggested_replies = json.dumps(replies if isinstance(replies, list) else [replies])
        except Exception as e:
            logger.warning("DM reply generation failed: %s", e)
            thread.suggested_replies = json.dumps(["Hey! Thanks for reaching out 💕"])

        db.commit()

        # Auto-reply if persona has it enabled and category is auto_reply
        if persona_acct and persona_acct.dm_auto_reply and thread.category in ("auto_reply", "cold"):
            variants = json.loads(thread.suggested_replies or "[]")
            if variants:
                await _send_reply(thread, variants[0], db)

    finally:
        db.close()


async def _send_reply(thread: DMThread, reply_text: str, db: Session) -> None:
    """Dispatch reply to the right platform."""
    try:
        if thread.platform == "instagram":
            await _send_instagram_dm(thread.external_thread_id, reply_text)
        elif thread.platform == "onlyfans":
            await _send_onlyfans_dm(thread.sender_id, reply_text)
        thread.approved_reply = reply_text
        thread.reply_sent = True
        thread.reply_sent_at = datetime.now(timezone.utc)
        thread.auto_replied = True
        db.commit()
        logger.info("Auto-replied to DM %d on %s", thread.id, thread.platform)
    except Exception as e:
        logger.error("Failed to send DM reply: %s", e)


async def _send_instagram_dm(thread_id: str, text: str) -> None:
    import os
    import httpx
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN not set")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"https://graph.facebook.com/v19.0/me/messages",
            params={"access_token": token},
            json={"recipient": {"id": thread_id}, "message": {"text": text}},
        )
        r.raise_for_status()


async def _send_onlyfans_dm(user_id: str, text: str) -> None:
    # OnlyFans DM sending via existing automation module
    from persona_system.automation.onlyfans import send_dm
    await send_dm(user_id, text)


# ── Instagram webhook ──────────────────────────────────────────────────────────

@router.get("/webhook/instagram")
async def instagram_webhook_verify(request: Request):
    """Instagram webhook verification challenge."""
    import os
    verify_token = os.environ.get("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "aishorts_verify")
    params = dict(request.query_params)
    if params.get("hub.verify_token") == verify_token:
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Forbidden", status_code=403)


@router.post("/webhook/instagram")
async def instagram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Receive Instagram DM events and queue classification."""
    payload = await request.json()
    for entry in payload.get("entry", []):
        for msg_event in entry.get("messaging", []):
            sender_id = msg_event.get("sender", {}).get("id", "")
            text = msg_event.get("message", {}).get("text", "")
            thread_id = msg_event.get("sender", {}).get("id", sender_id)

            if not text:
                continue

            # Determine which persona owns this IG account
            persona_acct = db.query(PersonaAccount).filter_by(
                instagram_user_id=entry.get("id")
            ).first()
            persona_name = persona_acct.persona_name if persona_acct else "default"

            thread = db.query(DMThread).filter_by(external_thread_id=thread_id, platform="instagram").first()
            if not thread:
                thread = DMThread(
                    persona_name=persona_name,
                    platform="instagram",
                    external_thread_id=thread_id,
                    sender_id=sender_id,
                    sender_handle=sender_id,
                )
                db.add(thread)

            thread.last_message = text
            thread.last_message_at = datetime.now(timezone.utc)
            db.commit()

            background_tasks.add_task(_classify_and_reply, thread.id)

    return JSONResponse({"status": "ok"})


# ── Manual DM pages ────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dms_list(request: Request, db: Session = Depends(get_db)):
    threads = (
        db.query(DMThread)
        .order_by(DMThread.last_message_at.desc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse(
        "dms.html",
        {
            "request": request,
            "threads": [
                {
                    "id": t.id,
                    "persona": t.persona_name,
                    "platform": t.platform,
                    "sender": t.sender_handle or t.sender_id,
                    "last_message": t.last_message,
                    "category": t.category,
                    "confidence": t.confidence,
                    "reply_sent": t.reply_sent,
                    "auto_replied": t.auto_replied,
                    "suggested_replies": json.loads(t.suggested_replies or "[]"),
                    "last_message_at": str(t.last_message_at or ""),
                }
                for t in threads
            ],
        },
    )


@router.post("/threads/{thread_id}/send")
async def send_approved_reply(
    thread_id: int,
    reply: str = Form(...),
    db: Session = Depends(get_db),
):
    """Manually approve and send a specific reply variant."""
    import asyncio
    thread = db.query(DMThread).filter(DMThread.id == thread_id).first()
    if not thread:
        return JSONResponse({"error": "Not found"}, status_code=404)
    asyncio.create_task(_send_reply(thread, reply, db))
    return JSONResponse({"status": "queued"})
