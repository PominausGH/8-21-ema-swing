import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "paper_trading.db")
SYMBOLS_PATH = os.path.join(BASE_DIR, "symbols.txt")

STARTING_CASH = 150_000.0
COMMISSION = 10.0
RISK_PCT = 0.02
MAX_POSITIONS = 10
SCAN_INTERVAL_MINUTES = 60

# Google OAuth
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
ALLOWED_EMAILS = [e.strip() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()]
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "change-me-in-production")
