from fastapi import APIRouter
from app.database import get_db
from app.portfolio import get_portfolio_summary, get_equity_curve, get_stats
from app.price_cache import cache

router = APIRouter()


@router.get("/portfolio")
def portfolio_summary():
    db = get_db()
    try:
        return get_portfolio_summary(db, cache)
    finally:
        db.close()


@router.get("/equity-curve")
def equity_curve():
    db = get_db()
    try:
        return get_equity_curve(db)
    finally:
        db.close()


@router.get("/stats")
def stats():
    db = get_db()
    try:
        return get_stats(db)
    finally:
        db.close()


@router.get("/notifications")
def notifications(limit: int = 30):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()
