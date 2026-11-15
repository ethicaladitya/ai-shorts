"""Auth middleware — enforces Google OAuth session and per-user feature permissions."""
from types import SimpleNamespace

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, HTMLResponse
from starlette.requests import Request

from app.config import settings

_PUBLIC_PREFIXES = (
    "/auth/",
    "/health",
    "/static/",
    "/output/",
    "/persona/",
    "/favicon",
    "/billing/webhook",  # Instamojo webhook must be public
)

# Maps route prefix → permission attribute on the Perms object.
# Checked in order; first match wins.
_ROUTE_GUARDS = [
    ("/admin",    "is_super_admin"),
    ("/ugc",      "can_ugc_video"),
    ("/avatar",   "can_avatar_video"),
    ("/videos",   "can_create_video"),
    ("/queue",    "can_queue"),
    ("/knowledge","can_knowledge"),
    ("/personas", "can_personas"),
    ("/dms",      "can_dms"),
    ("/costs",    "can_costs"),
    ("/settings", "can_settings"),
]


def _full_perms(is_super_admin: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        can_create_video=True,
        can_knowledge=True,
        can_avatar_video=True,
        can_ugc_video=True,
        can_personas=True,
        can_dms=True,
        can_costs=True,
        can_queue=True,
        can_settings=True,
        is_super_admin=is_super_admin,
    )


def _empty_perms() -> SimpleNamespace:
    return SimpleNamespace(
        can_create_video=False,
        can_knowledge=False,
        can_avatar_video=False,
        can_ugc_video=False,
        can_personas=False,
        can_dms=False,
        can_costs=False,
        can_queue=False,
        can_settings=False,
        is_super_admin=False,
    )


def _perms_from_db(db_row) -> SimpleNamespace:
    if db_row is None:
        return _empty_perms()
    return SimpleNamespace(
        can_create_video=bool(db_row.can_create_video),
        can_knowledge=bool(db_row.can_knowledge),
        can_avatar_video=bool(db_row.can_avatar_video),
        can_ugc_video=bool(db_row.can_ugc_video),
        can_personas=bool(db_row.can_personas),
        can_dms=bool(db_row.can_dms),
        can_costs=bool(db_row.can_costs),
        can_queue=bool(db_row.can_queue),
        can_settings=bool(db_row.can_settings),
        is_super_admin=False,
    )


def _load_perms_from_db(email: str) -> SimpleNamespace | None:
    """Return permissions for *email*, or None if user not found / inactive."""
    try:
        from app.database import SessionLocal
        from app.models import User, UserPermissions
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email.lower()).first()
            if not user or not user.is_active:
                return None
            db_perms = db.query(UserPermissions).filter(
                UserPermissions.user_email == email.lower()
            ).first()
            return _perms_from_db(db_perms)
        finally:
            db.close()
    except Exception:
        return _empty_perms()


def _403(path: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!DOCTYPE html><html><head>
        <title>Access Denied</title>
        <script src="https://cdn.tailwindcss.com"></script>
        </head><body class="bg-gray-50 flex items-center justify-center min-h-screen">
        <div class="text-center">
          <p class="text-6xl font-bold text-gray-300 mb-4">403</p>
          <h1 class="text-xl font-semibold text-gray-700 mb-2">Access Denied</h1>
          <p class="text-gray-500 mb-6">You don't have permission to access this page.</p>
          <a href="/" class="px-4 py-2 bg-gray-900 text-white rounded-lg text-sm hover:bg-gray-800">
            Back to Dashboard
          </a>
        </div></body></html>""",
        status_code=403,
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Dev mode — no OAuth configured
        if not settings.google_client_id or not settings.google_client_secret:
            request.state.perms = _full_perms(is_super_admin=True)
            request.state.user_email = "dev"
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        user_email = request.session.get("user_email", "").lower()
        if not user_email:
            request.session["next"] = str(request.url)
            return RedirectResponse(url="/auth/login", status_code=303)

        # Super admin gets full permissions always
        if user_email == settings.super_admin_email.lower():
            request.state.perms = _full_perms(is_super_admin=True)
            request.state.user_email = user_email
        else:
            perms = _load_perms_from_db(user_email)
            if perms is None:
                request.session.clear()
                return RedirectResponse(url="/auth/login?error=access_denied", status_code=303)
            request.state.perms = perms
            request.state.user_email = user_email

        # Inject credit balance for sidebar display
        try:
            from app.database import SessionLocal
            from app.models import User as _User
            _db = SessionLocal()
            _u = _db.query(_User).filter(_User.email == user_email).first()
            request.state.credits = _u.credits if _u else 0
            _db.close()
        except Exception:
            request.state.credits = 0

        # Route-level permission guard
        for prefix, perm_attr in _ROUTE_GUARDS:
            if path.startswith(prefix):
                if not getattr(request.state.perms, perm_attr, False):
                    return _403(path)
                break

        return await call_next(request)
