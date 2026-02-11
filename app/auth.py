import logging
from datetime import datetime, timedelta

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    ALLOWED_EMAILS,
    SESSION_SECRET_KEY,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Session cookie config
COOKIE_NAME = "session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# Serializer for signing session cookies
serializer = URLSafeTimedSerializer(SESSION_SECRET_KEY)

# OAuth setup
oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def create_session_cookie(email: str) -> str:
    """Create a signed session cookie value."""
    return serializer.dumps({"email": email, "created": datetime.utcnow().isoformat()})


def verify_session_cookie(cookie_value: str) -> dict | None:
    """Verify and decode session cookie. Returns session data or None."""
    if not cookie_value:
        return None
    try:
        data = serializer.loads(cookie_value, max_age=COOKIE_MAX_AGE)
        return data
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request) -> str | None:
    """Get current user email from session cookie."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    session = verify_session_cookie(cookie)
    if not session:
        return None
    return session.get("email")


def is_authenticated(request: Request) -> bool:
    """Check if request has valid session."""
    return get_current_user(request) is not None


@router.get("/login")
async def login(request: Request):
    """Redirect to Google OAuth."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="OAuth not configured")

    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def auth_callback(request: Request):
    """Handle Google OAuth callback."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")

    user_info = token.get("userinfo")
    if not user_info:
        raise HTTPException(status_code=401, detail="Could not get user info")

    email = user_info.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="No email in user info")

    # Check whitelist
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        logger.warning(f"Access denied for {email} - not in whitelist")
        raise HTTPException(status_code=403, detail=f"Access denied. {email} is not authorized.")

    logger.info(f"User {email} logged in")

    # Create session and redirect to home
    response = RedirectResponse(url="/", status_code=302)
    cookie_value = create_session_cookie(email)
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_value,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=True,  # HTTPS only
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout():
    """Clear session and redirect to login."""
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(key=COOKIE_NAME)
    return response


@router.get("/me")
async def get_me(request: Request):
    """Get current user info."""
    email = get_current_user(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"email": email}
