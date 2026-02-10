from fastapi import APIRouter, HTTPException
from app.database import get_db, init_db
from app.models import SettingUpdate
from app.config import STARTING_CASH

router = APIRouter()

ALLOWED_SETTING_KEYS = {
    "auto_trade",
    "scan_interval_minutes",
    "risk_pct",
    "commission",
    "max_positions",
    "min_signal_score",
    "max_drawdown_pct",
    "daily_loss_limit_pct",
    "slippage_pct",
    "trailing_stop_enabled",
}


@router.get("/settings")
def get_settings():
    db = get_db()
    try:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        db.close()


@router.put("/settings")
def update_setting(req: SettingUpdate):
    if req.key not in ALLOWED_SETTING_KEYS:
        raise HTTPException(status_code=400, detail=f"Invalid setting key: {req.key}")
    db = get_db()
    try:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (req.key, req.value),
        )
        db.commit()
        return {"key": req.key, "value": req.value}
    finally:
        db.close()


@router.post("/reset")
def reset_portfolio():
    """Reset portfolio to starting cash, delete all positions and trades."""
    db = get_db()
    try:
        db.execute("DELETE FROM trades")
        db.execute("DELETE FROM positions")
        db.execute("DELETE FROM scanner_results")
        db.execute("DELETE FROM equity_snapshots")
        db.execute("DELETE FROM notifications")
        db.execute(
            "UPDATE portfolio SET cash = ?, updated_at = datetime('now') WHERE id = 1",
            (STARTING_CASH,),
        )
        db.commit()
        return {"status": "reset", "cash": STARTING_CASH}
    finally:
        db.close()
