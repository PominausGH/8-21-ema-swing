from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.models import ManualBuyRequest, ManualCloseRequest
from app.portfolio import get_open_positions, get_portfolio_summary
from app.trader import execute_buy, execute_sell, calculate_position_size, get_setting
from app.price_cache import cache
from datetime import datetime, timezone

router = APIRouter()


@router.get("/positions")
def list_positions():
    db = get_db()
    try:
        return get_open_positions(db, cache)
    finally:
        db.close()


@router.post("/positions")
def manual_buy(req: ManualBuyRequest):
    # Server-side validation
    if req.price <= 0:
        raise HTTPException(400, "Price must be positive")
    if req.stop_price >= req.price:
        raise HTTPException(400, "Stop must be below entry price")
    if req.target1_price <= req.price:
        raise HTTPException(400, "Target 1 must be above entry price")
    if req.target2_price <= req.target1_price:
        raise HTTPException(400, "Target 2 must be above Target 1")
    if req.shares is not None and req.shares <= 0:
        raise HTTPException(400, "Shares must be positive")

    db = get_db()
    try:
        summary = get_portfolio_summary(db, cache)

        signal = {
            "symbol": req.symbol.upper().strip(),
            "price": req.price,
            "stop_price": req.stop_price,
            "target1_price": req.target1_price,
            "target2_price": req.target2_price,
        }

        if req.shares:
            # Manual share count — insert directly
            commission = float(get_setting(db, "commission", "10.0"))
            cost = (req.shares * req.price) + commission
            portfolio = db.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()
            if cost > portfolio["cash"]:
                raise HTTPException(400, "Insufficient cash")

            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            cursor = db.execute(
                """INSERT INTO positions
                   (symbol, side, initial_shares, shares, entry_price, entry_date,
                    stop_price, target1_price, target2_price, commission_paid, notes)
                   VALUES (?, 'long', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (signal["symbol"], req.shares, req.shares, req.price, now,
                 req.stop_price, req.target1_price, req.target2_price, commission,
                 req.notes or ""),
            )
            position_id = cursor.lastrowid
            db.execute(
                """INSERT INTO trades (position_id, symbol, action, shares, price, commission, reason, executed_at)
                   VALUES (?, ?, 'buy', ?, ?, ?, 'manual', ?)""",
                (position_id, signal["symbol"], req.shares, req.price, commission, now),
            )
            db.execute(
                "UPDATE portfolio SET cash = cash - ?, updated_at = ? WHERE id = 1",
                (cost, now),
            )
            db.commit()
            return {"position_id": position_id, "shares": req.shares}
        else:
            # Auto-size from risk
            position_id = execute_buy(db, signal, summary["total_equity"])
            if not position_id:
                raise HTTPException(400, "Could not open position (check cash, limits, or sizing)")
            pos = db.execute("SELECT shares FROM positions WHERE id = ?", (position_id,)).fetchone()
            return {"position_id": position_id, "shares": pos["shares"]}
    finally:
        db.close()


@router.post("/positions/{position_id}/close")
def close_position(position_id: int, req: ManualCloseRequest):
    db = get_db()
    try:
        pos = db.execute("SELECT * FROM positions WHERE id = ? AND status = 'open'", (position_id,)).fetchone()
        if not pos:
            raise HTTPException(404, "Position not found or already closed")

        shares = req.shares or pos["shares"]
        execute_sell(db, position_id, shares, req.price, req.reason)
        return {"closed": shares, "price": req.price, "reason": req.reason}
    finally:
        db.close()
