import time
import yfinance as yf
import pandas as pd

# Simple in-memory price cache: {symbol: (price, timestamp)}
_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL = 300  # 5 minutes

cache: dict[str, float] = {}  # Simplified view: {symbol: price}


def get_price(symbol: str) -> float | None:
    """Get price from cache or fetch from yfinance."""
    now = time.time()
    if symbol in _cache:
        price, ts = _cache[symbol]
        if now - ts < CACHE_TTL:
            return price

    try:
        data = yf.download(symbol, period="1d", interval="1d", progress=False)
        if data.empty:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)
        price = float(data["Close"].iloc[-1])
        _cache[symbol] = (price, now)
        cache[symbol] = price
        return price
    except Exception:
        return _cache.get(symbol, (None, 0))[0]


def update_cache_bulk(symbols: list[str]) -> None:
    """Fetch prices for multiple symbols and update cache."""
    now = time.time()
    for symbol in symbols:
        try:
            data = yf.download(symbol, period="1d", interval="1d", progress=False)
            if data.empty:
                continue
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.droplevel(1)
            price = float(data["Close"].iloc[-1])
            _cache[symbol] = (price, now)
            cache[symbol] = price
            time.sleep(0.2)
        except Exception:
            pass


def set_price(symbol: str, price: float) -> None:
    """Manually set a price in cache (used during scanning)."""
    _cache[symbol] = (price, time.time())
    cache[symbol] = price
