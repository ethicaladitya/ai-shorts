"""Google OAuth router — multi-user with DB-backed permissions."""
import urllib.parse
from datetime import datetime, timezone

import httpx

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory="app/templates")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get("/login")
async def login(request: Request, error: str = ""):
    if not settings.google_client_id:
        return HTMLResponse(
            "<h2>Google OAuth not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env</h2>",
            status_code=503,
        )
    error_msg = ""
    if error == "access_denied":
        error_msg = "Your account is pending approval or has been deactivated. Contact the admin."
    return templates.TemplateResponse("login.html", {"request": request, "error": error_msg})


@router.get("/google")
async def google_redirect(request: Request):
    """Kick off the Google OAuth flow."""
    redirect_uri = f"{settings.base_url}/auth/callback"
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@router.get("/callback")
async def callback(request: Request, code: str = "", error: str = ""):
    if error:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Google returned an error: {error}",
        })

    redirect_uri = f"{settings.base_url}/auth/callback"
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if token_resp.status_code != 200:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Failed to exchange code with Google. Please try again.",
            })
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "No access token returned by Google.",
            })

        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Failed to fetch user info from Google.",
            })
        userinfo = userinfo_resp.json()

    email = userinfo.get("email", "").lower()
    name  = userinfo.get("name", email)
    pic   = userinfo.get("picture", "")

    # Upsert user in DB
    from app.database import SessionLocal
    from app.models import User, UserPermissions
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        is_super = (email == settings.super_admin_email.lower())

        if user is None:
            free_credits = settings.signup_free_credits if not is_super else 9999
            user = User(email=email, name=name, picture=pic, is_active=True, credits=free_credits)
            db.add(user)
            db.flush()
            # Award signup bonus
            from app.models import CreditTransaction
            db.add(CreditTransaction(
                user_email=email,
                amount=free_credits,
                balance_after=free_credits,
                type="signup_bonus",
                description="Welcome gift — free credit on signup" if not is_super else "Super admin",
            ))
            # Permissions
            perms = UserPermissions(
                user_email=email,
                can_create_video=is_super,
                can_knowledge=is_super,
                can_avatar_video=is_super,
                can_ugc_video=is_super,
                can_personas=is_super,
                can_dms=is_super,
                can_costs=is_super,
                can_queue=is_super,
                can_settings=is_super,
            )
            db.add(perms)
        else:
            # Refresh name/picture; update last_login
            user.name = name
            user.picture = pic
            user.last_login = datetime.now(timezone.utc)
            if is_super:
                user.is_active = True  # super admin can never be deactivated via DB

        db.commit()

        if not user.is_active and not is_super:
            db.close()
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": "Your account has been deactivated. Contact the admin.",
            })
    finally:
        db.close()

    # Set session
    request.session["user_email"] = email
    request.session["user_name"] = name
    request.session["user_picture"] = pic

    next_url = request.session.pop("next", "/")
    return RedirectResponse(url=next_url, status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)
