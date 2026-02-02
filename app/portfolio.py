import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

SYDNEY_TZ = ZoneInfo("Australia/Sydney")


def get_portfolio_summary(db: sqlite3.Connection, price_cache: dict) -> dict:
    """Return portfolio summary with live position values."""
    row = db.execute("SELECT * FROM portfolio WHERE id = 1").fetchone()
    cash = row["cash"]
    starting_cash = row["starting_cash"]

    positions = db.execute("SELECT * FROM positions WHERE status = 'open'").fetchall()
    positions_value = 0.0
    unrealized_pnl = 0.0

    for pos in positions:
        current = price_cache.get(pos["symbol"], pos["entry_price"])
        value = pos["shares"] * current
        positions_value += value
        unrealized_pnl += (current - pos["entry_price"]) * pos["shares"]

    total_equity = cash + positions_value
    net_pnl = total_equity - starting_cash

    return {
        "cash": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "total_equity": round(total_equity, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "net_pnl": round(net_pnl, 2),
        "starting_cash": starting_cash,
    }


def get_open_positions(db: sqlite3.Connection, price_cache: dict) -> list[dict]:
    """Return open positions enriched with live data."""
    positions = db.execute("SELECT * FROM positions WHERE status = 'open' ORDER BY entry_date DESC").fetchall()
    result = []
    for pos in positions:
        current = price_cache.get(pos["symbol"], pos["entry_price"])
        unrealized = (current - pos["entry_price"]) * pos["shares"]
        pnl_pct = ((current - pos["entry_price"]) / pos["entry_price"]) * 100 if pos["entry_price"] else 0
        result.append({
            "id": pos["id"],
            "symbol": pos["symbol"],
            "side": pos["side"],
            "initial_shares": pos["initial_shares"],
            "shares": pos["shares"],
            "entry_price": pos["entry_price"],
            "entry_date": pos["entry_date"],
            "current_price": round(current, 3),
            "unrealized_pnl": round(unrealized, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop_price": pos["stop_price"],
            "target1_price": pos["target1_price"],
            "target2_price": pos["target2_price"],
            "target1_hit": bool(pos["target1_hit"]),
            "commission_paid": pos["commission_paid"],
        })
    return result


def get_trade_journal(db: sqlite3.Connection) -> list[dict]:
    """Return closed positions as trade journal entries."""
    positions = db.execute(
        "SELECT * FROM positions WHERE status = 'closed' ORDER BY close_date DESC"
    ).fetchall()

    journal = []
    for pos in positions:
        entry_cost = pos["initial_shares"] * pos["entry_price"]
        exit_value = pos["initial_shares"] * pos["close_price"] if pos["close_price"] else 0

        # Calculate total proceeds from all sell trades for this position
        sells = db.execute(
            "SELECT SUM(shares * price) as total_proceeds, SUM(commission) as total_comm FROM trades WHERE position_id = ? AND action = 'sell'",
            (pos["id"],),
        ).fetchone()
        total_proceeds = sells["total_proceeds"] or 0
        total_sell_commission = sells["total_comm"] or 0

        buy_commission = db.execute(
            "SELECT SUM(commission) as comm FROM trades WHERE position_id = ? AND action = 'buy'",
            (pos["id"],),
        ).fetchone()["comm"] or 0

        gross_pnl = total_proceeds - entry_cost
        net_pnl = gross_pnl - buy_commission - total_sell_commission

        risk_per_share = pos["entry_price"] - pos["stop_price"]
        risk_total = risk_per_share * pos["initial_shares"]
        rr_ratio = round(gross_pnl / risk_total, 2) if risk_total > 0 else 0

        journal.append({
            "id": pos["id"],
            "symbol": pos["symbol"],
            "side": pos["side"],
            "initial_shares": pos["initial_shares"],
            "entry_price": pos["entry_price"],
            "entry_date": pos["entry_date"],
            "close_price": pos["close_price"],
            "close_date": pos["close_date"],
            "close_reason": pos["close_reason"],
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": round(net_pnl, 2),
            "rr_ratio": rr_ratio,
            "commission_paid": pos["commission_paid"],
        })
    return journal


def get_stats(db: sqlite3.Connection) -> dict:
    """Calculate cumulative trading statistics."""
    positions = db.execute("SELECT * FROM positions WHERE status = 'closed'").fetchall()

    if not positions:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_pct": 0,
            "avg_win_pct": 0, "avg_loss_pct": 0, "profit_factor": 0,
            "net_pnl": 0, "max_drawdown": 0, "best_trade": None, "worst_trade": None,
        }

    wins = []
    losses = []

    for pos in positions:
        # Get total proceeds from sells
        sells = db.execute(
            "SELECT SUM(shares * price) as proceeds FROM trades WHERE position_id = ? AND action = 'sell'",
            (pos["id"],),
        ).fetchone()
        proceeds = sells["proceeds"] or 0
        cost = pos["initial_shares"] * pos["entry_price"]
        pnl = proceeds - cost - pos["commission_paid"]
        pnl_pct = (pnl / cost) * 100 if cost > 0 else 0

        if pnl >= 0:
            wins.append({"symbol": pos["symbol"], "pnl": pnl, "pnl_pct": pnl_pct})
        else:
            losses.append({"symbol": pos["symbol"], "pnl": pnl, "pnl_pct": pnl_pct})

    total = len(wins) + len(losses)
    gross_wins = sum(w["pnl"] for w in wins)
    gross_losses = abs(sum(l["pnl"] for l in losses))

    # Max drawdown from equity snapshots
    snapshots = db.execute("SELECT total_equity FROM equity_snapshots ORDER BY date").fetchall()
    max_drawdown = 0.0
    peak = 0.0
    for snap in snapshots:
        equity = snap["total_equity"]
        if equity > peak:
            peak = equity
        dd = ((peak - equity) / peak) * 100 if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

    all_trades = wins + losses
    best = max(all_trades, key=lambda t: t["pnl"]) if all_trades else None
    worst = min(all_trades, key=lambda t: t["pnl"]) if all_trades else None

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_pct": round((len(wins) / total) * 100, 1) if total > 0 else 0,
        "avg_win_pct": round(sum(w["pnl_pct"] for w in wins) / len(wins), 2) if wins else 0,
        "avg_loss_pct": round(sum(l["pnl_pct"] for l in losses) / len(losses), 2) if losses else 0,
        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else 0,
        "net_pnl": round(gross_wins - gross_losses, 2),
        "max_drawdown": round(max_drawdown, 2),
        "best_trade": best,
        "worst_trade": worst,
    }


def record_equity_snapshot(db: sqlite3.Connection, price_cache: dict) -> None:
    """Save today's equity snapshot (once per day, upsert)."""
    summary = get_portfolio_summary(db, price_cache)
    today = datetime.now(SYDNEY_TZ).strftime("%Y-%m-%d")

    db.execute(
        """INSERT INTO equity_snapshots (date, cash, positions_value, total_equity)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
           cash = excluded.cash, positions_value = excluded.positions_value,
           total_equity = excluded.total_equity, recorded_at = datetime('now')""",
        (today, summary["cash"], summary["positions_value"], summary["total_equity"]),
    )
    db.commit()


def get_equity_curve(db: sqlite3.Connection) -> list[dict]:
    """Return equity snapshots for charting."""
    rows = db.execute("SELECT date, total_equity FROM equity_snapshots ORDER BY date").fetchall()
    return [{"date": r["date"], "total_equity": r["total_equity"]} for r in rows]
