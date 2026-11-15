"""Billing router — credit balance, purchase packs via Instamojo."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import CreditTransaction, User

router = APIRouter(prefix="/billing", tags=["billing"])
templates = Jinja2Templates(directory="app/templates")

CREDIT_PACKS = [
    {"id": "starter",  "credits": 10,  "price_inr": 499,   "label": "Starter",  "popular": False},
    {"id": "pro",      "credits": 50,  "price_inr": 1999,  "label": "Pro",       "popular": True},
    {"id": "business", "credits": 100, "price_inr": 3499,  "label": "Business",  "popular": False},
]


def _get_user(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.lower()).first()


def _get_balance(db: Session, email: str) -> int:
    user = _get_user(db, email)
    return user.credits if user else 0


def _add_credits(db: Session, email: str, amount: int, tx_type: str,
                 description: str, payment_id: str | None = None,
                 amount_paid_inr: float | None = None):
    user = _get_user(db, email)
    if not user:
        return
    user.credits = (user.credits or 0) + amount
    tx = CreditTransaction(
        user_email=email,
        amount=amount,
        balance_after=user.credits,
        type=tx_type,
        description=description,
        payment_id=payment_id,
        payment_status="completed" if amount > 0 else None,
        amount_paid_inr=amount_paid_inr,
    )
    db.add(tx)
    db.commit()
    return user.credits


@router.get("", response_class=HTMLResponse)
async def billing_page(request: Request, db: Session = Depends(get_db)):
    email = request.session.get("user_email", "")
    balance = _get_balance(db, email)
    txns = (db.query(CreditTransaction)
            .filter(CreditTransaction.user_email == email)
            .order_by(CreditTransaction.created_at.desc())
            .limit(20).all())
    return templates.TemplateResponse("billing.html", {
        "request": request,
        "balance": balance,
        "packs": CREDIT_PACKS,
        "transactions": txns,
        "instamojo_configured": bool(settings.instamojo_api_key and settings.instamojo_auth_token),
    })


@router.post("/purchase")
async def initiate_purchase(
    request: Request,
    pack_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """Create Instamojo payment request and redirect user."""
    email = request.session.get("user_email", "")
    if not email:
        return RedirectResponse("/auth/login", status_code=303)

    pack = next((p for p in CREDIT_PACKS if p["id"] == pack_id), None)
    if not pack:
        return HTMLResponse("<p class='text-red-600'>Invalid pack.</p>")

    if not settings.instamojo_api_key or not settings.instamojo_auth_token:
        return HTMLResponse(
            '<div class="bg-amber-50 border border-amber-200 rounded-lg p-4 text-amber-700">'
            '<strong>Payment not configured.</strong> The admin needs to add Instamojo API keys in Settings.'
            '</div>'
        )

    redirect_url = f"{settings.base_url}/billing/payment-return"
    webhook_url = f"{settings.base_url}/billing/webhook"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.instamojo_base_url}/payment-requests/",
                headers={
                    "X-Api-Key": settings.instamojo_api_key,
                    "X-Auth-Token": settings.instamojo_auth_token,
                },
                data={
                    "purpose": f"{pack['label']} Pack — {pack['credits']} credits",
                    "amount": pack["price_inr"],
                    "buyer_name": request.session.get("user_name", ""),
                    "email": email,
                    "redirect_url": redirect_url,
                    "webhook": webhook_url,
                    "allow_repeated_payments": False,
                    "send_email": False,
                    "send_sms": False,
                },
                timeout=15,
            )
            data = resp.json()
            if data.get("success"):
                payment_url = data["payment_request"]["longurl"]
                # Store pending transaction
                tx = CreditTransaction(
                    user_email=email,
                    amount=pack["credits"],
                    balance_after=(db.query(User).filter(User.email == email).first().credits or 0),
                    type="purchase",
                    description=f"Pending: {pack['label']} pack",
                    payment_id=data["payment_request"]["id"],
                    payment_status="pending",
                    amount_paid_inr=pack["price_inr"],
                )
                db.add(tx)
                db.commit()
                return RedirectResponse(payment_url, status_code=303)
            else:
                return HTMLResponse(
                    f'<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">'
                    f'Payment initiation failed: {data}</div>'
                )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">'
            f'Error: {exc}</div>'
        )


@router.get("/payment-return")
async def payment_return(request: Request, db: Session = Depends(get_db)):
    """Instamojo redirects here after payment."""
    payment_id = request.query_params.get("payment_id", "")
    payment_request_id = request.query_params.get("payment_request_id", "")
    payment_status = request.query_params.get("payment_status", "")
    email = request.session.get("user_email", "")

    if payment_status == "Credit":
        # Verify and fulfil
        tx = (db.query(CreditTransaction)
              .filter(CreditTransaction.payment_id == payment_request_id)
              .first())
        if tx and tx.payment_status == "pending":
            tx.payment_status = "completed"
            user = _get_user(db, email)
            if user:
                user.credits = (user.credits or 0) + tx.amount
                tx.balance_after = user.credits
            db.commit()

    return RedirectResponse("/billing?status=" + payment_status, status_code=303)


@router.post("/webhook")
async def instamojo_webhook(request: Request, db: Session = Depends(get_db)):
    """Instamojo webhook for payment confirmation."""
    form = await request.form()
    payment_request_id = form.get("payment_request_id", "")
    payment_status = form.get("status", "")
    mac = form.get("mac", "")

    if payment_status == "Credit":
        tx = (db.query(CreditTransaction)
              .filter(CreditTransaction.payment_id == payment_request_id)
              .first())
        if tx and tx.payment_status == "pending":
            tx.payment_status = "completed"
            user = _get_user(db, tx.user_email)
            if user:
                user.credits = (user.credits or 0) + tx.amount
                tx.balance_after = user.credits
            db.commit()

    return {"status": "ok"}


# ── Admin helpers ──────────────────────────────────────────────────────────────

def admin_grant_credits(db: Session, email: str, amount: int, description: str) -> int:
    """Grant credits from admin panel. Returns new balance."""
    return _add_credits(db, email, amount, "admin_grant", description) or 0


def deduct_credit(db: Session, email: str, description: str = "Video generation") -> bool:
    """Deduct 1 credit. Returns False if insufficient balance."""
    user = _get_user(db, email)
    if not user:
        return True  # don't block if user record missing
    if user.credits <= 0:
        return False
    _add_credits(db, email, -settings.credit_cost_per_video, "video_generation", description)
    return True
