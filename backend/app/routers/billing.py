"""Billing router — credit balance, purchase packs via Instamojo."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
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


async def _fulfil_verified_payment(
    db: Session, request_id: str, payment_id: str, *, email: str | None = None,
) -> str:
    """Treat callbacks as hints; only authenticated provider evidence grants credits."""
    if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value)
               for value in (request_id, payment_id)):
        raise HTTPException(400, "Invalid payment identifiers")

    # payment_id historically stores the REQUEST id in the local purchase row.
    rows = db.query(CreditTransaction).filter(
        CreditTransaction.payment_id == request_id,
        CreditTransaction.type == "purchase",
    ).limit(2).all()
    if len(rows) != 1:
        raise HTTPException(400, "Unknown or ambiguous payment request")
    tx = rows[0]
    if email is not None and (not email or tx.user_email.lower() != email.lower()):
        raise HTTPException(403, "Payment belongs to another account")
    if tx.payment_status == "completed":
        return "completed"
    if tx.payment_status != "pending" or tx.amount <= 0:
        raise HTTPException(400, "Purchase is not pending")
    tx_id, owner, credits, price = tx.id, tx.user_email, tx.amount, tx.amount_paid_inr
    # Do not retain a database read transaction across the provider request.
    db.rollback()

    if not settings.instamojo_api_key or not settings.instamojo_auth_token:
        raise HTTPException(503, "Payment verification is unavailable")
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=15) as client:
            response = await client.get(
                f"{settings.instamojo_base_url.rstrip('/')}/payment-requests/{request_id}/{payment_id}/",
                headers={"X-Api-Key": settings.instamojo_api_key,
                         "X-Auth-Token": settings.instamojo_auth_token},
            )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Payment verification is unavailable") from None

    provider_request = body.get("payment_request") if isinstance(body, dict) else None
    payment = provider_request.get("payment") if isinstance(provider_request, dict) else None
    if (not isinstance(payment, dict) or body.get("success") is not True or
            provider_request.get("id") != request_id or
            payment.get("payment_id") != payment_id):
        raise HTTPException(400, "Payment verification failed")
    try:
        paid, expected = Decimal(str(payment.get("amount"))), Decimal(str(price))
        amount_matches = paid.is_finite() and expected.is_finite() and expected > 0 and paid == expected
    except InvalidOperation:
        amount_matches = False
    if (payment.get("status") != "Credit" or payment.get("currency") != "INR" or
            not amount_matches or
            str(payment.get("buyer_email", "")).lower() != owner.lower()):
        raise HTTPException(400, "Payment verification failed")

    try:
        # Compare-and-set and balance increment share one transaction. Only one
        # concurrent redirect/webhook can claim the pending purchase.
        claimed = db.query(CreditTransaction).filter(
            CreditTransaction.id == tx_id,
            CreditTransaction.payment_status == "pending",
            CreditTransaction.payment_id == request_id,
            CreditTransaction.user_email == owner,
            CreditTransaction.type == "purchase",
            CreditTransaction.amount == credits,
            CreditTransaction.amount_paid_inr == price,
        ).update({CreditTransaction.payment_status: "completed"}, synchronize_session=False)
        if not claimed:
            db.rollback()
            current = db.get(CreditTransaction, tx_id)
            if current is not None and current.payment_status == "completed":
                return "completed"
            raise HTTPException(409, "Purchase changed during verification")
        updated = db.query(User).filter(User.email == owner).update(
            {User.credits: func.coalesce(User.credits, 0) + credits}, synchronize_session=False)
        if updated != 1:
            raise HTTPException(409, "Purchase account is unavailable")
        balance = db.query(User.credits).filter(User.email == owner).scalar()
        db.query(CreditTransaction).filter(CreditTransaction.id == tx_id).update(
            {CreditTransaction.balance_after: balance}, synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return "completed"


@router.get("/payment-return")
async def payment_return(request: Request, db: Session = Depends(get_db)):
    """Verify the provider payment, never the browser's payment_status parameter."""
    email = request.session.get("user_email", "")
    if not email:
        return RedirectResponse("/auth/login", status_code=303)
    await _fulfil_verified_payment(
        db, request.query_params.get("payment_request_id", ""),
        request.query_params.get("payment_id", ""), email=email,
    )
    return RedirectResponse("/billing?status=Credit", status_code=303)


@router.post("/webhook")
async def instamojo_webhook(request: Request, db: Session = Depends(get_db)):
    """A public notification triggers authenticated API verification, not trust."""
    form = await request.form()
    await _fulfil_verified_payment(
        db, form.get("payment_request_id", ""), form.get("payment_id", ""),
    )
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
