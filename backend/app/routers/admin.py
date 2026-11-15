"""Admin panel — super-admin-only user, permission, and credit management."""
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import User, UserPermissions, CreditTransaction, CostLog

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")

FEATURES = [
    ("can_create_video", "Faceless Reels"),
    ("can_knowledge",    "Knowledge Base"),
    ("can_avatar_video", "Avatar Video"),
    ("can_ugc_video",    "UGC Videos"),
    ("can_personas",     "Personas"),
    ("can_dms",          "DM Inbox"),
    ("can_costs",        "Cost Tracker"),
    ("can_queue",        "Render Queue"),
    ("can_settings",     "AI Settings"),
]


def _require_super_admin(request: Request):
    if not getattr(getattr(request.state, "perms", None), "is_super_admin", False):
        raise HTTPException(status_code=403, detail="Super admin only")


def _get_or_create_perms(db: Session, email: str) -> UserPermissions:
    p = db.query(UserPermissions).filter(UserPermissions.user_email == email.lower()).first()
    if p is None:
        p = UserPermissions(user_email=email.lower())
        db.add(p)
        db.flush()
    return p


@router.get("")
async def admin_users(request: Request, db: Session = Depends(get_db)):
    _require_super_admin(request)
    users = db.query(User).order_by(User.created_at).all()
    perms_map = {p.user_email: p for p in db.query(UserPermissions).all()}

    # Per-user cost totals
    cost_rows = (db.query(CostLog.user_email, func.sum(CostLog.cost_usd))
                 .group_by(CostLog.user_email).all())
    cost_map = {email: round(total, 4) for email, total in cost_rows if email}

    # System total cost
    system_cost = db.query(func.sum(CostLog.cost_usd)).scalar() or 0.0

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "users": users,
        "perms_map": perms_map,
        "features": FEATURES,
        "cost_map": cost_map,
        "system_cost": round(system_cost, 4),
    })


@router.get("/costs")
async def admin_costs(request: Request, db: Session = Depends(get_db)):
    _require_super_admin(request)
    # Per-user spend
    user_costs = (db.query(CostLog.user_email, func.sum(CostLog.cost_usd))
                  .group_by(CostLog.user_email)
                  .order_by(func.sum(CostLog.cost_usd).desc()).all())
    # Per-provider spend
    provider_costs = (db.query(CostLog.provider, func.sum(CostLog.cost_usd))
                      .group_by(CostLog.provider)
                      .order_by(func.sum(CostLog.cost_usd).desc()).all())
    # Recent events
    recent = (db.query(CostLog)
              .order_by(CostLog.created_at.desc())
              .limit(50).all())
    system_total = db.query(func.sum(CostLog.cost_usd)).scalar() or 0.0

    return templates.TemplateResponse("admin/costs.html", {
        "request": request,
        "user_costs": user_costs,
        "provider_costs": provider_costs,
        "recent": recent,
        "system_total": round(system_total, 4),
    })


@router.post("/users/invite")
async def invite_user(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_super_admin(request)
    email = email.strip().lower()
    if not email:
        return HTMLResponse('<p class="text-red-600 text-sm">Email is required.</p>')
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return HTMLResponse(f'<p class="text-yellow-600 text-sm">{email} already exists.</p>')
    from app.config import settings
    user = User(email=email, name=email, is_active=True, credits=settings.signup_free_credits)
    db.add(user)
    db.flush()
    db.add(UserPermissions(user_email=email))
    # Log free credits
    from app.models import CreditTransaction
    db.add(CreditTransaction(
        user_email=email, amount=settings.signup_free_credits,
        balance_after=settings.signup_free_credits,
        type="admin_grant", description="Invited by admin",
    ))
    db.commit()
    return HTMLResponse(
        f'<p class="text-green-600 text-sm">Added {email} with {settings.signup_free_credits} free credit(s).</p>'
        '<script>setTimeout(()=>location.reload(),1200)</script>'
    )


@router.post("/users/{email}/toggle-active")
async def toggle_active(email: str, request: Request, db: Session = Depends(get_db)):
    _require_super_admin(request)
    from app.config import settings
    if email.lower() == settings.super_admin_email.lower():
        return HTMLResponse('<span class="text-red-500 text-xs">Cannot deactivate super admin</span>')
    user = db.query(User).filter(User.email == email.lower()).first()
    if not user:
        raise HTTPException(404)
    user.is_active = not user.is_active
    db.commit()
    state = "Active" if user.is_active else "Inactive"
    color = "green" if user.is_active else "red"
    return HTMLResponse(
        f'<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium '
        f'bg-{color}-100 text-{color}-700">{state}</span>'
    )


@router.post("/users/{email}/perm/{feature}")
async def toggle_permission(email: str, feature: str, request: Request, db: Session = Depends(get_db)):
    _require_super_admin(request)
    valid = {f for f, _ in FEATURES}
    if feature not in valid:
        raise HTTPException(400, "Unknown feature")
    p = _get_or_create_perms(db, email)
    setattr(p, feature, not getattr(p, feature, False))
    db.commit()
    return HTMLResponse("")


@router.post("/users/{email}/credits")
async def grant_credits(
    email: str,
    request: Request,
    amount: int = Form(...),
    note: str = Form("Admin grant"),
    db: Session = Depends(get_db),
):
    _require_super_admin(request)
    from app.routers.billing import admin_grant_credits
    new_balance = admin_grant_credits(db, email, amount, note)
    return HTMLResponse(
        f'<span class="text-green-600 text-sm font-medium">✓ Balance: {new_balance} credits</span>'
        '<script>setTimeout(()=>location.reload(),1500)</script>'
    )
