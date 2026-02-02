import logging
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def get_setting(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_portfolio(db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT * FROM portfolio WHERE id = 1").fetchone()
    return dict(row)


def calculate_position_size(
    total_equity: float,
    available_cash: float,
    entry_price: float,
    stop_price: float,
    risk_pct: float,
    commission: float,
) -> int:
    """Calculate shares based on risk rule. Returns 0 if invalid."""
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        log.warning("Invalid risk: entry %.3f <= stop %.3f", entry_price, stop_price)
        return 0
    dollar_risk = total_equity * risk_pct
    shares = int(dollar_risk / risk_per_share)
    # Account for buy + sell commission in total cost check
    cost = (shares * entry_price) + (commission * 2)
    if cost > available_cash:
        shares = int((available_cash - commission * 2) / entry_price)
    if shares <= 0:
        log.warning("Position size 0 for entry=%.2f stop=%.2f equity=%.0f", entry_price, stop_price, total_equity)
    return max(shares, 0)


def execute_buy(db: sqlite3.Connection, signal: dict, total_equity: float) -> int | None:
    """Open a position from a scanner signal. Returns position_id or None."""
    symbol = signal["symbol"]
    risk_pct = float(get_setting(db, "risk_pct", "0.02"))
    commission = float(get_setting(db, "commission", "10.0"))
    max_positions = int(get_setting(db, "max_positions", "10"))

    portfolio = get_portfolio(db)
    cash = portfolio["cash"]

    # Check max positions
    open_count = db.execute(
        "SELECT COUNT(*) as cnt FROM positions WHERE status = 'open'"
    ).fetchone()["cnt"]
    if open_count >= max_positions:
        log.info("Max positions (%d) reached, skipping %s", max_positions, symbol)
        return None

    # Check not already holding
    existing = db.execute(
        "SELECT id FROM positions WHERE symbol = ? AND status = 'open'", (symbol,)
    ).fetchone()
    if existing:
        return None

    slippage_pct = float(get_setting(db, "slippage_pct", "0.001"))
    entry_price = round(signal["price"] * (1 + slippage_pct), 3)  # Simulate slippage on buy
    stop_price = signal["stop_price"]
    target1 = signal["target1_price"]
    target2 = signal["target2_price"]

    # Validate signal makes sense
    if stop_price >= entry_price:
        log.warning("Stop %.3f >= entry %.3f for %s, skipping", stop_price, entry_price, symbol)
        return None
    if target1 <= entry_price or target2 <= target1:
        log.warning("Bad targets for %s: entry=%.2f t1=%.2f t2=%.2f", symbol, entry_price, target1, target2)
        return None

    shares = calculate_position_size(
        total_equity, cash, entry_price, stop_price, risk_pct, commission
    )
    if shares <= 0:
        return None

    cost = (shares * entry_price) + commission
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cursor = db.execute(
        """INSERT INTO positions
           (symbol, side, initial_shares, shares, entry_price, entry_date,
            stop_price, target1_price, target2_price, commission_paid)
           VALUES (?, 'long', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (symbol, shares, shares, entry_price, now, stop_price, target1, target2, commission),
    )
    position_id = cursor.lastrowid

    db.execute(
        """INSERT INTO trades (position_id, symbol, action, shares, price, commission, reason, executed_at)
           VALUES (?, ?, 'buy', ?, ?, ?, 'signal', ?)""",
        (position_id, symbol, shares, entry_price, commission, now),
    )

    db.execute(
        "UPDATE portfolio SET cash = cash - ?, updated_at = ? WHERE id = 1",
        (cost, now),
    )

    db.commit()
    log.info("BUY %s: %d shares @ $%.2f, stop $%.2f, T1 $%.2f, T2 $%.2f",
             symbol, shares, entry_price, stop_price, target1, target2)
    return position_id


def execute_sell(
    db: sqlite3.Connection,
    position_id: int,
    shares_to_sell: int,
    price: float,
    reason: str,
) -> None:
    """Sell shares from a position (partial or full)."""
    commission = float(get_setting(db, "commission", "10.0"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    pos = db.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
    if not pos or pos["status"] != "open":
        return

    shares_to_sell = min(shares_to_sell, pos["shares"])

    # FIX: proceeds = gross sale amount; commission tracked separately
    gross_proceeds = shares_to_sell * price
    net_proceeds = gross_proceeds - commission

    db.execute(
        """INSERT INTO trades (position_id, symbol, action, shares, price, commission, reason, executed_at)
           VALUES (?, ?, 'sell', ?, ?, ?, ?, ?)""",
        (position_id, pos["symbol"], shares_to_sell, price, commission, reason, now),
    )

    # Cash receives net proceeds (after commission)
    db.execute(
        "UPDATE portfolio SET cash = cash + ?, updated_at = ? WHERE id = 1",
        (net_proceeds, now),
    )

    remaining = pos["shares"] - shares_to_sell
    total_commission = pos["commission_paid"] + commission

    if remaining <= 0:
        db.execute(
            """UPDATE positions SET shares = 0, status = 'closed', close_price = ?,
               close_date = ?, close_reason = ?, commission_paid = ? WHERE id = ?""",
            (price, now, reason, total_commission, position_id),
        )
    else:
        # Partial close — after T1 hit, move stop to breakeven
        new_stop = pos["stop_price"]
        new_target1_hit = pos["target1_hit"]
        if reason == "target1_partial":
            new_target1_hit = 1
            new_stop = pos["entry_price"]  # Move stop to breakeven

        db.execute(
            """UPDATE positions SET shares = ?, commission_paid = ?, target1_hit = ?,
               stop_price = ? WHERE id = ?""",
            (remaining, total_commission, new_target1_hit, new_stop, position_id),
        )

    db.commit()
    log.info("SELL %s: %d shares @ $%.2f reason=%s", pos["symbol"], shares_to_sell, price, reason)


def check_circuit_breaker(db: sqlite3.Connection, price_cache: dict) -> str | None:
    """Check if portfolio drawdown or daily losses exceed limits. Returns reason or None."""
    from app.portfolio import get_portfolio_summary

    max_dd_pct = float(get_setting(db, "max_drawdown_pct", "10.0"))
    daily_limit_pct = float(get_setting(db, "daily_loss_limit_pct", "3.0"))

    summary = get_portfolio_summary(db, price_cache)
    starting = summary["starting_cash"]
    equity = summary["total_equity"]

    # Check total drawdown from starting capital
    drawdown_pct = ((starting - equity) / starting) * 100 if starting > 0 else 0
    if drawdown_pct >= max_dd_pct:
        return f"Portfolio drawdown {drawdown_pct:.1f}% exceeds limit {max_dd_pct}%"

    # Check daily realized losses
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = db.execute(
        """SELECT COALESCE(SUM(
            CASE WHEN action = 'sell' THEN shares * price - commission ELSE 0 END
           ) - SUM(
            CASE WHEN action = 'buy' THEN shares * price + commission ELSE 0 END
           ), 0) as daily_pnl
           FROM trades WHERE executed_at LIKE ? || '%'""",
        (today,),
    ).fetchone()
    daily_pnl = row["daily_pnl"] if row else 0

    if daily_pnl < 0:
        daily_loss_pct = abs(daily_pnl) / equity * 100 if equity > 0 else 0
        if daily_loss_pct >= daily_limit_pct:
            return f"Daily loss {daily_loss_pct:.1f}% exceeds limit {daily_limit_pct}%"

    return None


def update_trailing_stops(db: sqlite3.Connection, ema8_prices: dict[str, float]) -> list[str]:
    """Update stops on positions where T1 has been hit, using 8 EMA as trailing stop."""
    actions = []
    positions = db.execute(
        "SELECT * FROM positions WHERE status = 'open' AND target1_hit = 1"
    ).fetchall()

    for pos in positions:
        ema8 = ema8_prices.get(pos["symbol"])
        if ema8 is None:
            continue

        trailing = round(ema8 * 0.995, 3)  # 0.5% buffer below 8 EMA
        current_stop = pos["stop_price"]

        # Only ratchet up, never down
        if trailing > current_stop:
            db.execute(
                "UPDATE positions SET stop_price = ?, trailing_stop = ? WHERE id = ?",
                (trailing, trailing, pos["id"]),
            )
            actions.append(
                f"TRAIL: {pos['symbol']} stop raised ${current_stop:.2f} → ${trailing:.2f} (8 EMA)"
            )

    if actions:
        db.commit()
    return actions


def check_stops_and_targets(db: sqlite3.Connection, price_cache: dict) -> list[str]:
    """Check open positions against current prices. Returns list of action messages."""
    actions = []
    positions = db.execute("SELECT * FROM positions WHERE status = 'open'").fetchall()

    for pos in positions:
        symbol = pos["symbol"]
        current_price = price_cache.get(symbol)
        if current_price is None:
            continue

        # Stop loss hit
        if current_price <= pos["stop_price"]:
            execute_sell(db, pos["id"], pos["shares"], current_price, "stop")
            actions.append(f"STOP HIT: {symbol} sold {pos['shares']} @ ${current_price:.2f}")
            continue

        # FIX: Check Target 2 first (handles gap-through where price skips T1)
        if current_price >= pos["target2_price"]:
            if not pos["target1_hit"]:
                # Price gapped past both targets — sell 25% at T1 price, rest at T2
                t1_shares = max(1, int(pos["initial_shares"] * 0.25))
                t1_shares = min(t1_shares, pos["shares"])
                execute_sell(db, pos["id"], t1_shares, pos["target1_price"], "target1_partial")
                actions.append(f"TARGET 1 HIT (gap): {symbol} sold {t1_shares} (25%) @ ${pos['target1_price']:.2f}")
                # Re-fetch position after partial close
                pos_updated = db.execute("SELECT * FROM positions WHERE id = ?", (pos["id"],)).fetchone()
                if pos_updated and pos_updated["status"] == "open" and pos_updated["shares"] > 0:
                    execute_sell(db, pos["id"], pos_updated["shares"], current_price, "target2")
                    actions.append(f"TARGET 2 HIT: {symbol} sold {pos_updated['shares']} (remaining) @ ${current_price:.2f}")
            else:
                execute_sell(db, pos["id"], pos["shares"], current_price, "target2")
                actions.append(f"TARGET 2 HIT: {symbol} sold {pos['shares']} (remaining) @ ${current_price:.2f}")
            continue

        # Target 1: sell 25% of initial position
        if not pos["target1_hit"] and current_price >= pos["target1_price"]:
            shares_to_sell = max(1, int(pos["initial_shares"] * 0.25))
            shares_to_sell = min(shares_to_sell, pos["shares"])
            execute_sell(db, pos["id"], shares_to_sell, current_price, "target1_partial")
            actions.append(f"TARGET 1 HIT: {symbol} sold {shares_to_sell} (25%) @ ${current_price:.2f}")

    return actions
