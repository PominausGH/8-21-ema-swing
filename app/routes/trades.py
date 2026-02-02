from fastapi import APIRouter
from app.database import get_db
from app.portfolio import get_trade_journal

router = APIRouter()


@router.get("/trades")
def list_trades():
    db = get_db()
    try:
        return get_trade_journal(db)
    finally:
        db.close()
