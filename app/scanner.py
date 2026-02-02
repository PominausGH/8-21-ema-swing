import logging
import re
import time
import yfinance as yf
import pandas as pd
import numpy as np
from app.config import SYMBOLS_PATH

log = logging.getLogger(__name__)

# Matches ASX (BHP.AX) and US (AAPL, BRK-B) tickers
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,6}(-[A-Z])?(\.[A-Z]{1,3})?$")

# Signal lookback: how many bars back to search for the pullback bounce
SIGNAL_LOOKBACK = 3

# Minimum score to include in results (auto-trade threshold is separate setting)
MIN_DISPLAY_SCORE = 30


def load_symbols(filepath: str = None) -> list[str]:
    path = filepath or SYMBOLS_PATH
    try:
        with open(path) as f:
            symbols = []
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if SYMBOL_RE.match(line):
                        symbols.append(line)
                    else:
                        log.warning("Skipping invalid symbol: %s", line)
            return symbols
    except FileNotFoundError:
        log.error("Symbols file not found: %s", path)
        return []


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calculate_demarker(high: pd.Series, low: pd.Series, period: int = 14) -> pd.Series:
    demax = high - high.shift(1)
    demax[demax < 0] = 0
    demin = low.shift(1) - low
    demin[demin < 0] = 0
    sma_demax = demax.rolling(window=period).mean()
    sma_demin = demin.rolling(window=period).mean()
    return sma_demax / (sma_demax + sma_demin)


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength (0-100)."""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    # Where +DM > -DM, keep +DM, else zero (and vice versa)
    plus_dm[plus_dm <= minus_dm] = 0
    minus_dm[minus_dm <= plus_dm] = 0

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx.fillna(0)


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range — measures volatility."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def find_swing_low_high(high: pd.Series, low: pd.Series, lookback: int = 40, pivot_bars: int = 5) -> tuple[float, float]:
    """Find structural swing points using pivot detection, not rolling min/max."""
    data_high = high.iloc[-lookback:]
    data_low = low.iloc[-lookback:]

    swing_highs = []
    swing_lows = []

    for i in range(pivot_bars, len(data_high) - pivot_bars):
        # Pivot high: highest of surrounding bars
        if data_high.iloc[i] >= data_high.iloc[i - pivot_bars:i + pivot_bars + 1].max():
            swing_highs.append(float(data_high.iloc[i]))
        # Pivot low: lowest of surrounding bars
        if data_low.iloc[i] <= data_low.iloc[i - pivot_bars:i + pivot_bars + 1].min():
            swing_lows.append(float(data_low.iloc[i]))

    sh = swing_highs[-1] if swing_highs else float(data_high.max())
    sl = swing_lows[-1] if swing_lows else float(data_low.min())
    return sl, sh


def score_signal(data: pd.DataFrame, latest: pd.Series, entry_price: float,
                 stop_price: float, target2: float) -> int:
    """Score signal quality 0-100. Higher = better setup."""
    score = 50

    # Volume confirmation: is money backing this bounce?
    avg_vol = data["Volume"].rolling(20).mean().iloc[-1]
    if avg_vol > 0:
        rel_vol = latest["Volume"] / avg_vol
        if rel_vol > 1.5:
            score += 15
        elif rel_vol > 1.0:
            score += 5
        elif rel_vol < 0.8:
            score -= 15

    # ADX trend strength
    adx = latest["ADX"]
    if adx > 30:
        score += 12
    elif adx > 25:
        score += 6
    elif adx < 20:
        score -= 10

    # EMA separation (momentum quality)
    if latest["EMA21"] > 0:
        ema_spread = (latest["EMA8"] - latest["EMA21"]) / latest["EMA21"] * 100
        if ema_spread > 2:
            score += 8
        elif ema_spread > 1:
            score += 4
        elif ema_spread < 0.3:
            score -= 5

    # R:R ratio quality
    risk = entry_price - stop_price
    reward = target2 - entry_price
    if risk > 0:
        rr = reward / risk
        if rr >= 3:
            score += 10
        elif rr >= 2:
            score += 5
        elif rr < 1.5:
            score -= 10

    # DeMarker depth (deeper oversold = stronger bounce)
    demarker = latest["DeMarker"]
    if demarker < 0.25:
        score += 5
    elif demarker > 0.5:
        score -= 5

    return max(0, min(100, score))


def check_signal(symbol: str) -> dict | None:
    """Check a single symbol for a buy signal. Returns structured dict or None."""
    data = yf.download(symbol, period="3mo", interval="1d", progress=False)
    if data.empty or len(data) < 50:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)

    data["EMA8"] = calculate_ema(data["Close"], 8)
    data["EMA21"] = calculate_ema(data["Close"], 21)
    data["DeMarker"] = calculate_demarker(data["High"], data["Low"])
    data["ADX"] = calculate_adx(data["High"], data["Low"], data["Close"])
    data["ATR14"] = calculate_atr(data["High"], data["Low"], data["Close"])
    data["AvgVol20"] = data["Volume"].rolling(20).mean()

    latest = data.iloc[-1]

    # Current trend must be intact: price above 21 EMA, 8 EMA above 21 EMA
    if latest["Close"] <= latest["EMA21"] or latest["EMA8"] <= latest["EMA21"]:
        return None

    # ADX filter: skip ranging/choppy markets
    if latest["ADX"] < 20:
        return None

    # Volume filter: skip dead stocks
    avg_vol = latest["AvgVol20"]
    if avg_vol > 0 and latest["Volume"] < avg_vol * 0.5:
        return None

    # Look back up to SIGNAL_LOOKBACK bars for the pullback bounce
    bounce_found = False
    for offset in range(1, SIGNAL_LOOKBACK + 1):
        idx = -offset
        if abs(idx) >= len(data) - 1:
            break
        bar = data.iloc[idx]
        prev = data.iloc[idx - 1]

        # Pullback bounce: prev close at/below 8 EMA, bar close above 8 EMA
        pullback = prev["Close"] <= prev["EMA8"] and bar["Close"] > bar["EMA8"]
        # DeMarker bounce from oversold
        demarker_bounce = prev["DeMarker"] < 0.3 and bar["DeMarker"] > 0.35

        if pullback and demarker_bounce:
            bounce_found = True
            break

    if not bounce_found:
        return None

    # Swing detection and fibonacci targets
    swing_low, swing_high = find_swing_low_high(data["High"], data["Low"])
    fib_range = swing_high - swing_low
    if fib_range <= 0:
        return None

    target1 = swing_high + fib_range * 0.272  # 127.2%
    target2 = swing_high + fib_range * 0.618  # 161.8%

    # Stop: higher of swing low and EMA21, with buffer
    raw_stop = max(swing_low, float(latest["EMA21"]))
    stop_price = round(raw_stop * 0.995, 3)
    entry_price = round(float(latest["Close"]), 3)

    # Validate levels
    if stop_price >= entry_price:
        return None
    if target1 <= entry_price:
        return None

    # Relative volume
    rel_vol = round(float(latest["Volume"] / avg_vol), 2) if avg_vol > 0 else 1.0

    # Confidence score
    confidence = score_signal(data, latest, entry_price, stop_price, target2)
    if confidence < MIN_DISPLAY_SCORE:
        return None

    return {
        "symbol": symbol,
        "price": entry_price,
        "ema8": round(float(latest["EMA8"]), 3),
        "ema21": round(float(latest["EMA21"]), 3),
        "demarker": round(float(latest["DeMarker"]), 4),
        "adx": round(float(latest["ADX"]), 1),
        "atr": round(float(latest["ATR14"]), 3),
        "relative_volume": rel_vol,
        "confidence": confidence,
        "stop_price": round(stop_price, 3),
        "target1_price": round(target1, 3),
        "target2_price": round(target2, 3),
        "swing_low": round(swing_low, 3),
        "swing_high": round(swing_high, 3),
    }


def get_market_regime(index_symbol: str = "^GSPC") -> dict:
    """Check broad market regime using index EMAs."""
    try:
        data = yf.download(index_symbol, period="1y", interval="1d", progress=False)
        if data.empty:
            return {"regime": "UNKNOWN", "index": index_symbol}
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)

        ema50 = calculate_ema(data["Close"], 50).iloc[-1]
        ema200 = calculate_ema(data["Close"], 200).iloc[-1]
        price = float(data["Close"].iloc[-1])

        if price > ema50 > ema200:
            regime = "BULL"
        elif price < ema50 < ema200:
            regime = "BEAR"
        else:
            regime = "MIXED"

        return {
            "regime": regime,
            "index": index_symbol,
            "price": round(price, 2),
            "ema50": round(float(ema50), 2),
            "ema200": round(float(ema200), 2),
        }
    except Exception as e:
        log.warning("Market regime check failed: %s", e)
        return {"regime": "UNKNOWN", "index": index_symbol}


def scan_all(symbols: list[str] | None = None) -> list[dict]:
    """Scan all symbols, return list of signal dicts sorted by confidence."""
    if symbols is None:
        symbols = load_symbols()

    signals = []
    for symbol in symbols:
        try:
            signal = check_signal(symbol)
            if signal:
                signals.append(signal)
                log.info("Signal: %s score=%d @ $%.2f", symbol, signal["confidence"], signal["price"])
        except Exception as e:
            log.warning("Scanner error for %s: %s", symbol, e)
        time.sleep(0.3)

    # Sort by confidence descending — best signals first
    signals.sort(key=lambda s: s["confidence"], reverse=True)
    log.info("Scan complete: %d signals from %d symbols", len(signals), len(symbols))
    return signals
