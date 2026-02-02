import sqlite3
import os
from app.config import DB_PATH, STARTING_CASH

def _safe_add_columns(db: sqlite3.Connection, table: str, columns: list[tuple[str, str]]):
    """Add columns to a table if they don't already exist."""
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    for col_name, col_type in columns:
        if col_name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            starting_cash REAL NOT NULL,
            cash REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'long',
            initial_shares INTEGER NOT NULL,
            shares INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            entry_date TEXT NOT NULL,
            stop_price REAL NOT NULL,
            target1_price REAL NOT NULL,
            target2_price REAL NOT NULL,
            target1_hit INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            close_price REAL,
            close_date TEXT,
            close_reason TEXT,
            commission_paid REAL NOT NULL DEFAULT 0.0,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            shares INTEGER NOT NULL,
            price REAL NOT NULL,
            commission REAL NOT NULL DEFAULT 10.0,
            reason TEXT,
            executed_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (position_id) REFERENCES positions(id)
        );

        CREATE TABLE IF NOT EXISTS scanner_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal_type TEXT NOT NULL DEFAULT 'buy',
            price REAL NOT NULL,
            ema8 REAL NOT NULL,
            ema21 REAL NOT NULL,
            demarker REAL NOT NULL,
            adx REAL,
            atr REAL,
            relative_volume REAL,
            confidence INTEGER,
            stop_price REAL NOT NULL,
            target1_price REAL NOT NULL,
            target2_price REAL NOT NULL,
            auto_traded INTEGER NOT NULL DEFAULT 0,
            position_id INTEGER,
            scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (position_id) REFERENCES positions(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            cash REAL NOT NULL,
            positions_value REAL NOT NULL,
            total_equity REAL NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

    # Insert portfolio row if not exists
    existing = db.execute("SELECT id FROM portfolio WHERE id = 1").fetchone()
    if not existing:
        db.execute(
            "INSERT INTO portfolio (id, starting_cash, cash) VALUES (1, ?, ?)",
            (STARTING_CASH, STARTING_CASH),
        )

    # Insert default settings if not exists
    # Add columns to existing tables if upgrading (safe to run multiple times)
    _safe_add_columns(db, "scanner_results", [
        ("adx", "REAL"), ("atr", "REAL"), ("relative_volume", "REAL"), ("confidence", "INTEGER"),
    ])
    _safe_add_columns(db, "positions", [
        ("trailing_stop", "REAL"),
    ])

    defaults = {
        "auto_trade": "true",
        "scan_interval_minutes": "60",
        "risk_pct": "0.02",
        "commission": "10.0",
        "max_positions": "10",
        "min_signal_score": "30",
        "max_drawdown_pct": "10.0",
        "daily_loss_limit_pct": "3.0",
        "slippage_pct": "0.001",
        "trailing_stop_enabled": "true",
    }
    for key, value in defaults.items():
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    db.commit()
    db.close()
