"""Google OAuth router."""
import urllib.parse
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
async def login(request: Request):
    if not settings.google_client_id:
        # OAuth not configured — show error
        return HTMLResponse(
            "<h2>Google OAuth not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env</h2>",
            status_code=503,
        )
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
        # Exchange code for token
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

        # Fetch user info
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
        email = userinfo.get("email", "")

    if email.lower() != settings.allowed_email.lower():
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Access denied. This app is restricted to {settings.allowed_email}.",
        })

    # Set session
    request.session["user_email"] = email
    request.session["user_name"] = userinfo.get("name", email)
    request.session["user_picture"] = userinfo.get("picture", "")

    next_url = request.session.pop("next", "/")
    return RedirectResponse(url=next_url, status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)
