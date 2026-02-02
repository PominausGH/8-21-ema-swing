# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-file Python script (`8-21.py`) that scans ASX200 stocks for swing trading buy signals using the "8-21 EMA Power Triangle" strategy:
- **8 & 21 EMA** — trend identification and pullback zones
- **DeMarker oscillator (14-period)** — timing pullback entries (oversold bounce: crosses above 0.35 from below 0.30)
- **Fibonacci extensions (127.2%, 161.8%)** — profit targets from recent swing low/high range

Buy signal fires when: price > 21 EMA, 8 EMA > 21 EMA, previous close ≤ 8 EMA, latest close > 8 EMA, and DeMarker bounces. Stop loss is below 21 EMA.

## Running

```bash
pip install yfinance pandas numpy
python3 8-21.py
```

No tests, linting, or build system configured.

## Known Issues

- **Syntax error on line 42**: `il oc` should be `iloc` (space in method name)
- **Incomplete symbol list**: Only ~35 real ASX symbols; rest are placeholder `' PME.AX'` entries with leading spaces
- **Email alerting (`send_email`)**: Fully commented out / stub
- **No error handling**: Network failures or bad yfinance data will crash the script
- **No scheduling**: Must be run manually (intended for cron)

## Architecture

All logic is in `8-21.py`. Key functions:

| Function | Purpose |
|---|---|
| `calculate_ema(series, period)` | EMA via pandas `ewm` |
| `calculate_demarker(high, low, period)` | DeMarker oscillator from high/low series |
| `find_swing_low_high(close, lookback)` | Rolling min/max over lookback window |
| `check_signal(symbol)` | Downloads 3mo daily data, checks all conditions, returns signal string or None |
| `send_email(message)` | Stub for SMTP email alerts |

The `__main__` block iterates all symbols, collects signals, and prints results.

## Data Source

Uses `yfinance` to download OHLC data. ASX symbols use `.AX` suffix. Data period is 3 months daily.
