import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import init_db
from app.tasks import trading_loop
from app.routes.portfolio import router as portfolio_router
from app.routes.positions import router as positions_router
from app.routes.trades import router as trades_router
from app.routes.scanner import router as scanner_router
from app.routes.settings import router as settings_router

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# API key from environment (optional — if not set, auth is disabled)
API_KEY = os.environ.get("PAPER_TRADER_API_KEY", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(trading_loop())
    yield
    task.cancel()


app = FastAPI(title="8-21 EMA Paper Trader", lifespan=lifespan)

# CORS — restrict to same origin in production; allow localhost for dev
allowed_origins = os.environ.get("CORS_ORIGINS", "http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Simple API key auth for /api routes. Skipped if PAPER_TRADER_API_KEY is not set."""
    if API_KEY and request.url.path.startswith("/api"):
        key = request.headers.get("X-API-Key")
        if key != API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key")
    return await call_next(request)


# API routes
app.include_router(portfolio_router, prefix="/api")
app.include_router(positions_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(scanner_router, prefix="/api")
app.include_router(settings_router, prefix="/api")

# Serve frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def serve_spa():
    return FileResponse("frontend/index.html")
