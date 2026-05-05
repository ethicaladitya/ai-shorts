"""Auth middleware — enforces Google OAuth session on all routes."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse
from starlette.requests import Request

from app.config import settings

# Paths that are always public (no auth required)
_PUBLIC_PREFIXES = (
    "/auth/",
    "/health",
    "/static/",
    "/output/",
    "/favicon",
)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth if Google OAuth is not configured
        if not settings.google_client_id or not settings.google_client_secret:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        user_email = request.session.get("user_email", "")
        if user_email.lower() == settings.allowed_email.lower():
            return await call_next(request)

        # Not authenticated — save intended destination and redirect to login
        request.session["next"] = str(request.url)
        return RedirectResponse(url="/auth/login", status_code=303)
