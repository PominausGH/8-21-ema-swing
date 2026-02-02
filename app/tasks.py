import asyncio
import logging
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from app.database import get_db
from app.scanner import scan_all, get_market_regime, calculate_ema
from app.trader import (execute_buy, check_stops_and_targets, get_setting,
                        check_circuit_breaker, update_trailing_stops)
from app.portfolio import get_portfolio_summary, record_equity_snapshot
from app.price_cache import cache, set_price, update_cache_bulk

log = logging.getLogger(__name__)

SYDNEY_TZ = ZoneInfo("Australia/Sydney")
NEW_YORK_TZ = ZoneInfo("America/New_York")


def is_market_hours() -> bool:
    """Check if ASX or US market is likely open (weekdays only).
    ASX: 10:00am-4:30pm Sydney | US: 9:30am-4:00pm New York
    """
    syd = datetime.now(SYDNEY_TZ)
    ny = datetime.now(NEW_YORK_TZ)
    if syd.weekday() >= 5 and ny.weekday() >= 5:
        return False
    syd_hour = syd.hour + syd.minute / 60
    ny_hour = ny.hour + ny.minute / 60
    asx_open = syd.weekday() < 5 and 10.0 <= syd_hour <= 16.5
    us_open = ny.weekday() < 5 and 9.5 <= ny_hour <= 16.0
    return asx_open or us_open


async def trading_loop():
    """Background loop: scan -> trade -> monitor -> snapshot."""
    await asyncio.sleep(5)

    while True:
        db = get_db()
        try:
            interval = int(get_setting(db, "scan_interval_minutes", "60"))
            auto_trade = get_setting(db, "auto_trade", "true") == "true"

            if not is_market_hours():
                log.info("Outside market hours, recording snapshot only")
                record_equity_snapshot(db, cache)
                db.close()
                await asyncio.sleep(interval * 60)
                continue

            log.info("Starting scan cycle (auto_trade=%s)", auto_trade)

            # Check market regime
            regime = await asyncio.to_thread(get_market_regime)
            log.info("Market regime: %s (%s @ $%.2f)", regime["regime"], regime["index"], regime.get("price", 0))

            # Run scanner in thread (blocking yfinance calls)
            signals = await asyncio.to_thread(scan_all)
            log.info("Found %d signals", len(signals))

            # Update price cache from signals
            for sig in signals:
                set_price(sig["symbol"], sig["price"])

            # Store scanner results (with new intelligence fields)
            for sig in signals:
                db.execute(
                    """INSERT INTO scanner_results
                       (symbol, signal_type, price, ema8, ema21, demarker,
                        adx, atr, relative_volume, confidence,
                        stop_price, target1_price, target2_price, auto_traded)
                       VALUES (?, 'buy', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                    (sig["symbol"], sig["price"], sig["ema8"], sig["ema21"],
                     sig["demarker"], sig.get("adx"), sig.get("atr"),
                     sig.get("relative_volume"), sig.get("confidence"),
                     sig["stop_price"], sig["target1_price"],
                     sig["target2_price"]),
                )
            db.commit()

            # Circuit breaker check before auto-trading
            breaker_reason = check_circuit_breaker(db, cache)
            if breaker_reason:
                log.warning("CIRCUIT BREAKER: %s — skipping auto-trade", breaker_reason)
                _notify(db, "warning", f"Circuit breaker tripped: {breaker_reason}")
            elif regime["regime"] == "BEAR":
                log.warning("BEAR market regime — skipping auto-trade")
                _notify(db, "warning", "Bear market regime detected — auto-trade paused")
            elif auto_trade and signals:
                # Auto-trade if enabled and no breaker/bear
                summary = get_portfolio_summary(db, cache)
                for sig in signals:
                    pos_id = execute_buy(db, sig, summary["total_equity"])
                    if pos_id:
                        log.info("Opened position #%d for %s @ $%.2f (score=%d)",
                                 pos_id, sig["symbol"], sig["price"], sig.get("confidence", 0))
                        _notify(db, "info", f"Bought {sig['symbol']} @ ${sig['price']:.2f} (score {sig.get('confidence', 0)})")
                        # Mark latest scanner result for this symbol as auto-traded
                        result_row = db.execute(
                            """SELECT id FROM scanner_results
                               WHERE symbol = ? AND auto_traded = 0
                               ORDER BY scanned_at DESC LIMIT 1""",
                            (sig["symbol"],),
                        ).fetchone()
                        if result_row:
                            db.execute(
                                "UPDATE scanner_results SET auto_traded = 1, position_id = ? WHERE id = ?",
                                (pos_id, result_row["id"]),
                            )
                        db.commit()
                        summary = get_portfolio_summary(db, cache)

            # Update prices for open positions
            open_positions = db.execute(
                "SELECT DISTINCT symbol FROM positions WHERE status = 'open'"
            ).fetchall()
            open_symbols = [r["symbol"] for r in open_positions]
            if open_symbols:
                await asyncio.to_thread(update_cache_bulk, open_symbols)

            # Update trailing stops (8 EMA) for positions past T1
            trailing_enabled = get_setting(db, "trailing_stop_enabled", "true") == "true"
            if trailing_enabled and open_symbols:
                ema8_prices = await asyncio.to_thread(_fetch_ema8_values, open_symbols)
                trail_actions = update_trailing_stops(db, ema8_prices)
                for action in trail_actions:
                    log.info(action)
                    _notify(db, "info", action)

            # Check stops and targets
            actions = check_stops_and_targets(db, cache)
            for action in actions:
                log.info(action)
                _notify(db, "info", action)

            # Record daily equity snapshot
            record_equity_snapshot(db, cache)

            log.info("Cycle complete. Next scan in %d minutes.", interval)

        except Exception as e:
            log.error("Error in trading loop: %s", e)
            traceback.print_exc()
        finally:
            db.close()

        await asyncio.sleep(interval * 60)


def _notify(db, level: str, message: str):
    """Insert a notification row."""
    try:
        db.execute(
            "INSERT INTO notifications (level, message) VALUES (?, ?)",
            (level, message),
        )
        db.commit()
    except Exception:
        pass  # notifications are best-effort


def _fetch_ema8_values(symbols: list[str]) -> dict[str, float]:
    """Fetch current 8 EMA for a list of symbols."""
    import yfinance as yf
    import pandas as pd
    import time as _time

    result = {}
    for symbol in symbols:
        try:
            data = yf.download(symbol, period="1mo", interval="1d", progress=False)
            if data.empty:
                continue
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.droplevel(1)
            ema8 = calculate_ema(data["Close"], 8)
            result[symbol] = float(ema8.iloc[-1])
            _time.sleep(0.15)
        except Exception:
            pass
    return result
