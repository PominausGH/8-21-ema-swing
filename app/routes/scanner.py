import asyncio
from fastapi import APIRouter
from app.database import get_db
from app.scanner import scan_all
from app.price_cache import set_price

router = APIRouter()


@router.get("/scanner/results")
def list_results(limit: int = 50):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM scanner_results ORDER BY scanned_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.post("/scanner/run")
async def run_scan():
    """Trigger a manual scan. Returns signals found."""
    signals = await asyncio.to_thread(scan_all)

    db = get_db()
    try:
        for sig in signals:
            set_price(sig["symbol"], sig["price"])
            db.execute(
                """INSERT INTO scanner_results
                   (symbol, signal_type, price, ema8, ema21, demarker,
                    adx, atr, relative_volume, confidence,
                    stop_price, target1_price, target2_price)
                   VALUES (?, 'buy', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sig["symbol"], sig["price"], sig["ema8"], sig["ema21"],
                 sig["demarker"], sig.get("adx"), sig.get("atr"),
                 sig.get("relative_volume"), sig.get("confidence"),
                 sig["stop_price"], sig["target1_price"],
                 sig["target2_price"]),
            )
        db.commit()
        return {"signals_found": len(signals), "signals": signals}
    finally:
        db.close()
