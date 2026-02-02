# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Paper trading simulator for ASX200 stocks using the "8-21 EMA Power Triangle" swing trading strategy:
- **8 & 21 EMA** — trend identification and pullback zones
- **DeMarker oscillator (14-period)** — timing entries (oversold bounce: crosses above 0.35 from below 0.30)
- **Fibonacci extensions (127.2%, 161.8%)** — profit targets from swing low/high
- **Auto-execution**: scanner detects signals, auto-opens positions with 2% risk sizing
- **Scale-out**: 25% at Target 1 (127.2%), remaining 75% at Target 2 (161.8%)

## Running

```bash
# Local development
pip install -r requirements.txt
python run.py                    # http://localhost:8000

# Docker (production)
docker compose up -d --build     # http://localhost:8000
```

## Architecture

```
app/
├── main.py          # FastAPI app, lifespan, routes, static files
├── config.py        # Constants (starting cash, commission, risk %, paths)
├── database.py      # SQLite init, 6 tables, get_db()
├── scanner.py       # Signal detection (refactored from 8-21.py)
├── trader.py        # Position sizing, buy/sell execution, stop/target monitoring
├── portfolio.py     # Portfolio summary, trade journal, stats, equity curve
├── price_cache.py   # In-memory price cache (5min TTL) for yfinance data
├── models.py        # Pydantic request schemas
├── tasks.py         # Background async loop: scan → trade → monitor → snapshot
└── routes/          # FastAPI routers (portfolio, positions, trades, scanner, settings)
frontend/
└── index.html       # Vue 3 + TailwindCSS + Chart.js SPA (all CDN, no build step)
symbols.txt          # ASX200 symbols, one per line
8-21.py              # Original standalone scanner (kept as reference)
```

## Key Files

- `symbols.txt` — ASX200 symbol list (175 equities, `.AX` suffix). Update quarterly after ASX rebalances.
- `data/paper_trading.db` — SQLite database (gitignored, created at runtime)
- `docker-compose.yml` — Production deployment config

## Database Tables

`portfolio` (single row), `positions`, `trades`, `scanner_results`, `equity_snapshots`, `settings`

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/portfolio` | Portfolio summary |
| GET/POST | `/api/positions` | List open / manual buy |
| POST | `/api/positions/{id}/close` | Close position |
| GET | `/api/trades` | Trade journal |
| GET | `/api/scanner/results` | Scanner history |
| POST | `/api/scanner/run` | Trigger manual scan |
| GET | `/api/stats` | Cumulative stats |
| GET | `/api/equity-curve` | Equity snapshots |
| GET/PUT | `/api/settings` | Config |
| POST | `/api/reset` | Reset to $150k |

## Data Source

Uses `yfinance` for OHLC data. Rate limited (0.3s between downloads). Price cache in `price_cache.py` avoids re-fetching within 5 minutes.
