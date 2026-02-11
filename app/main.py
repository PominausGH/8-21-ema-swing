import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from app.database import init_db
from app.tasks import trading_loop
from app.routes.portfolio import router as portfolio_router
from app.routes.positions import router as positions_router
from app.routes.trades import router as trades_router
from app.routes.scanner import router as scanner_router
from app.routes.settings import router as settings_router
from app.auth import router as auth_router, is_authenticated, get_current_user
from app.config import SESSION_SECRET_KEY, GOOGLE_CLIENT_ID

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(trading_loop())
    yield
    task.cancel()


app = FastAPI(title="8-21 EMA Paper Trader", lifespan=lifespan)

# Session middleware for OAuth (required by authlib)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)

# CORS — restrict to same origin in production; allow localhost for dev
allowed_origins = os.environ.get("CORS_ORIGINS", "http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


def auth_enabled() -> bool:
    """Check if OAuth authentication is configured."""
    return bool(GOOGLE_CLIENT_ID)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Session-based auth for /api routes. Skipped if OAuth is not configured."""
    path = request.url.path

    # Always allow auth routes
    if path.startswith("/auth"):
        return await call_next(request)

    # If OAuth not configured, allow all requests
    if not auth_enabled():
        return await call_next(request)

    # Protect API routes
    if path.startswith("/api"):
        if not is_authenticated(request):
            raise HTTPException(status_code=401, detail="Not authenticated")

    return await call_next(request)


# Auth routes
app.include_router(auth_router)

# API routes
app.include_router(portfolio_router, prefix="/api")
app.include_router(positions_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(scanner_router, prefix="/api")
app.include_router(settings_router, prefix="/api")

# Serve frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def serve_spa(request: Request):
    """Serve dashboard if authenticated, otherwise serve login page."""
    if auth_enabled() and not is_authenticated(request):
        return FileResponse("frontend/login.html")
    return FileResponse("frontend/index.html")
