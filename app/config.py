import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "paper_trading.db")
SYMBOLS_PATH = os.path.join(BASE_DIR, "symbols.txt")

STARTING_CASH = 150_000.0
COMMISSION = 10.0
RISK_PCT = 0.02
MAX_POSITIONS = 10
SCAN_INTERVAL_MINUTES = 60
