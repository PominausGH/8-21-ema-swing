"""
Backtest the 8-21 EMA Swing strategy over historical data.
Uses the same signal detection, position sizing, and exit rules as the live system.

Usage: python backtest.py [--symbols AAPL BHP.AX ...] [--start 2024-07-01] [--end 2026-01-31]
"""

import argparse
import time
import sys
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field

from app.scanner import (
    calculate_ema, calculate_demarker, calculate_adx, calculate_atr,
    find_swing_low_high, score_signal, load_symbols,
    SIGNAL_LOOKBACK, MIN_DISPLAY_SCORE,
)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_START = "2024-07-01"
DEFAULT_END = "2026-01-31"
WARMUP_DAYS = 90  # extra days before start for EMA warmup
STARTING_CASH = 150_000.0
COMMISSION = 10.0
RISK_PCT = 0.02
MAX_POSITIONS = 10
SLIPPAGE_PCT = 0.001  # 0.1% slippage on entries


@dataclass
class Trade:
    date: str
    action: str
    shares: int
    price: float
    reason: str


@dataclass
class Position:
    symbol: str
    initial_shares: int
    shares: int
    entry_price: float
    entry_date: str
    stop_price: float
    target1_price: float
    target2_price: float
    target1_hit: bool = False
    commission_paid: float = 0.0
    close_price: float = None
    close_date: str = None
    close_reason: str = None
    trades: list = field(default_factory=list)


class Backtester:
    def __init__(self, starting_cash=STARTING_CASH):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.open_positions: list[Position] = []
        self.closed_positions: list[Position] = []
        self.equity_curve: list[dict] = []
        self.all_data: dict[str, pd.DataFrame] = {}
        self.signals_generated = 0
        self.signals_traded = 0

    # ── Data download ─────────────────────────────────────────────────────
    def download_data(self, symbols: list[str], start: str, end: str):
        """Download OHLCV for all symbols with warmup period."""
        warmup_start = pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)
        total = len(symbols)
        downloaded = 0
        failed = []

        for i, symbol in enumerate(symbols, 1):
            sys.stdout.write(f"\rDownloading {i}/{total}: {symbol:<10}")
            sys.stdout.flush()
            try:
                df = yf.download(
                    symbol,
                    start=warmup_start.strftime("%Y-%m-%d"),
                    end=end,
                    interval="1d",
                    progress=False,
                )
                if df.empty or len(df) < 50:
                    failed.append(symbol)
                    continue
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)

                # Pre-compute indicators
                df["EMA8"] = calculate_ema(df["Close"], 8)
                df["EMA21"] = calculate_ema(df["Close"], 21)
                df["DeMarker"] = calculate_demarker(df["High"], df["Low"])
                df["ADX"] = calculate_adx(df["High"], df["Low"], df["Close"])
                df["ATR14"] = calculate_atr(df["High"], df["Low"], df["Close"])
                df["AvgVol20"] = df["Volume"].rolling(20).mean()

                self.all_data[symbol] = df
                downloaded += 1
                time.sleep(0.15)
            except Exception as e:
                failed.append(symbol)

        print(f"\rDownloaded {downloaded}/{total} symbols. {len(failed)} failed.       ")
        if failed and len(failed) <= 20:
            print(f"  Failed: {', '.join(failed)}")

    # ── Signal detection (mirrors scanner.check_signal) ───────────────────
    def check_signal_at(self, symbol: str, day_idx: int) -> dict | None:
        """Check for buy signal on a specific bar index."""
        df = self.all_data[symbol]
        if day_idx < 50:
            return None

        data = df.iloc[:day_idx + 1]
        latest = data.iloc[-1]

        # Trend intact
        if latest["Close"] <= latest["EMA21"] or latest["EMA8"] <= latest["EMA21"]:
            return None

        # ADX filter
        if latest["ADX"] < 20:
            return None

        # Volume filter
        avg_vol = latest["AvgVol20"]
        if avg_vol > 0 and latest["Volume"] < avg_vol * 0.5:
            return None

        # Pullback bounce lookback
        bounce_found = False
        for offset in range(1, SIGNAL_LOOKBACK + 1):
            idx = -offset
            if abs(idx) >= len(data) - 1:
                break
            bar = data.iloc[idx]
            prev = data.iloc[idx - 1]

            pullback = prev["Close"] <= prev["EMA8"] and bar["Close"] > bar["EMA8"]
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

        target1 = swing_high + fib_range * 0.272
        target2 = swing_high + fib_range * 0.618

        raw_stop = max(swing_low, float(latest["EMA21"]))
        stop_price = round(raw_stop * 0.995, 3)
        entry_price = round(float(latest["Close"]), 3)

        if stop_price >= entry_price:
            return None
        if target1 <= entry_price:
            return None

        rel_vol = round(float(latest["Volume"] / avg_vol), 2) if avg_vol > 0 else 1.0

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
            "stop_price": stop_price,
            "target1_price": round(target1, 3),
            "target2_price": round(target2, 3),
            "swing_low": round(swing_low, 3),
            "swing_high": round(swing_high, 3),
        }

    # ── Position sizing ───────────────────────────────────────────────────
    def size_position(self, entry: float, stop: float) -> int:
        risk_per_share = entry - stop
        if risk_per_share <= 0:
            return 0
        dollar_risk = (self.cash + self._positions_value_at(entry)) * RISK_PCT
        shares = int(dollar_risk / risk_per_share)
        cost = (shares * entry) + (COMMISSION * 2)
        if cost > self.cash:
            shares = int((self.cash - COMMISSION * 2) / entry)
        return max(shares, 0)

    def _positions_value_at(self, fallback_price: float) -> float:
        total = 0.0
        for pos in self.open_positions:
            sym_data = self.all_data.get(pos.symbol)
            if sym_data is not None and len(sym_data) > 0:
                total += pos.shares * float(sym_data["Close"].iloc[-1])
            else:
                total += pos.shares * pos.entry_price
        return total

    # ── Execute buy ───────────────────────────────────────────────────────
    def execute_buy(self, signal: dict, date_str: str) -> bool:
        if len(self.open_positions) >= MAX_POSITIONS:
            return False
        if any(p.symbol == signal["symbol"] for p in self.open_positions):
            return False

        entry_price = round(signal["price"] * (1 + SLIPPAGE_PCT), 3)  # Slippage
        shares = self.size_position(entry_price, signal["stop_price"])
        if shares <= 0:
            return False

        cost = (shares * entry_price) + COMMISSION
        if cost > self.cash:
            return False

        self.cash -= cost
        pos = Position(
            symbol=signal["symbol"],
            initial_shares=shares,
            shares=shares,
            entry_price=entry_price,
            entry_date=date_str,
            stop_price=signal["stop_price"],
            target1_price=signal["target1_price"],
            target2_price=signal["target2_price"],
            commission_paid=COMMISSION,
        )
        pos.trades.append(Trade(date_str, "buy", shares, signal["price"], "signal"))
        self.open_positions.append(pos)
        self.signals_traded += 1
        return True

    # ── Execute sell ──────────────────────────────────────────────────────
    def execute_sell(self, pos: Position, shares: int, price: float, date_str: str, reason: str):
        shares = min(shares, pos.shares)
        gross = shares * price
        net = gross - COMMISSION
        self.cash += net
        pos.commission_paid += COMMISSION
        pos.shares -= shares
        pos.trades.append(Trade(date_str, "sell", shares, price, reason))

        if reason == "target1_partial":
            pos.target1_hit = True
            pos.stop_price = pos.entry_price  # breakeven stop

        if pos.shares <= 0:
            pos.close_price = price
            pos.close_date = date_str
            pos.close_reason = reason
            self.open_positions.remove(pos)
            self.closed_positions.append(pos)

    # ── Trailing stop update ─────────────────────────────────────────────
    def update_trailing_stops(self, date):
        """Ratchet stops up using 8 EMA for positions past T1."""
        for pos in self.open_positions:
            if not pos.target1_hit:
                continue
            df = self.all_data.get(pos.symbol)
            if df is None or date not in df.index:
                continue
            day_idx = df.index.get_loc(date)
            if isinstance(day_idx, slice):
                day_idx = day_idx.stop - 1
            if day_idx < 8:
                continue
            ema8 = float(df["EMA8"].iloc[day_idx])
            trailing = round(ema8 * 0.995, 3)
            if trailing > pos.stop_price:
                pos.stop_price = trailing

    # ── Daily check stops/targets ─────────────────────────────────────────
    def check_exits(self, date_str: str, prices: dict[str, float]):
        for pos in list(self.open_positions):
            price = prices.get(pos.symbol)
            if price is None:
                continue

            # Stop loss
            if price <= pos.stop_price:
                self.execute_sell(pos, pos.shares, price, date_str, "stop")
                continue

            # Target 2 (check first for gap-through)
            if price >= pos.target2_price:
                if not pos.target1_hit:
                    t1_shares = max(1, int(pos.initial_shares * 0.25))
                    t1_shares = min(t1_shares, pos.shares)
                    self.execute_sell(pos, t1_shares, pos.target1_price, date_str, "target1_partial")
                    if pos.shares > 0:
                        self.execute_sell(pos, pos.shares, price, date_str, "target2")
                else:
                    self.execute_sell(pos, pos.shares, price, date_str, "target2")
                continue

            # Target 1
            if not pos.target1_hit and price >= pos.target1_price:
                t1_shares = max(1, int(pos.initial_shares * 0.25))
                t1_shares = min(t1_shares, pos.shares)
                self.execute_sell(pos, t1_shares, price, date_str, "target1_partial")

    # ── Equity snapshot ───────────────────────────────────────────────────
    def record_equity(self, date_str: str, prices: dict[str, float]):
        pos_value = sum(
            pos.shares * prices.get(pos.symbol, pos.entry_price)
            for pos in self.open_positions
        )
        equity = self.cash + pos_value
        self.equity_curve.append({
            "date": date_str,
            "equity": round(equity, 2),
            "cash": round(self.cash, 2),
            "positions": len(self.open_positions),
        })

    # ── Main backtest loop ────────────────────────────────────────────────
    def run(self, symbols: list[str], start: str, end: str):
        print(f"\n{'='*70}")
        print(f"  BACKTEST: 8-21 EMA Swing Strategy")
        print(f"  Period: {start} → {end}")
        print(f"  Symbols: {len(symbols)} | Cash: ${self.starting_cash:,.0f}")
        print(f"{'='*70}\n")

        # Download data
        self.download_data(symbols, start, end)
        if not self.all_data:
            print("No data downloaded. Exiting.")
            return

        # Build a master trading calendar from all symbols
        all_dates = set()
        for df in self.all_data.values():
            all_dates.update(df.index)
        trading_days = sorted([d for d in all_dates if start <= d.strftime("%Y-%m-%d") <= end])
        print(f"Trading days in range: {len(trading_days)}\n")

        # Walk through each day
        for day_num, date in enumerate(trading_days):
            date_str = date.strftime("%Y-%m-%d")

            # Get current prices for all held symbols
            prices = {}
            for symbol, df in self.all_data.items():
                if date in df.index:
                    prices[symbol] = float(df.loc[date, "Close"])

            # Update trailing stops (8 EMA) then check exits
            self.update_trailing_stops(date)
            self.check_exits(date_str, prices)

            # Scan for new signals
            day_signals = []
            for symbol, df in self.all_data.items():
                if date not in df.index:
                    continue
                day_idx = df.index.get_loc(date)
                if isinstance(day_idx, slice):
                    day_idx = day_idx.stop - 1
                signal = self.check_signal_at(symbol, day_idx)
                if signal:
                    day_signals.append(signal)

            # Sort by confidence, execute buys
            day_signals.sort(key=lambda s: s["confidence"], reverse=True)
            self.signals_generated += len(day_signals)

            for sig in day_signals:
                bought = self.execute_buy(sig, date_str)
                if bought:
                    print(f"  {date_str}  BUY  {sig['symbol']:<8} @ ${sig['price']:>8.2f}  "
                          f"stop=${sig['stop_price']:.2f}  T1=${sig['target1_price']:.2f}  "
                          f"T2=${sig['target2_price']:.2f}  score={sig['confidence']}")

            # Record equity
            self.record_equity(date_str, prices)

            # Progress indicator every 20 days
            if day_num % 20 == 0:
                eq = self.equity_curve[-1]["equity"] if self.equity_curve else self.starting_cash
                sys.stdout.write(f"\r  Day {day_num+1}/{len(trading_days)}  "
                                 f"Equity: ${eq:>12,.2f}  "
                                 f"Open: {len(self.open_positions)}  "
                                 f"Closed: {len(self.closed_positions)}    ")
                sys.stdout.flush()

        # Force-close any remaining positions at last known price
        if self.open_positions:
            last_date = trading_days[-1].strftime("%Y-%m-%d")
            last_prices = {}
            for symbol, df in self.all_data.items():
                if len(df) > 0:
                    last_prices[symbol] = float(df["Close"].iloc[-1])
            for pos in list(self.open_positions):
                price = last_prices.get(pos.symbol, pos.entry_price)
                self.execute_sell(pos, pos.shares, price, last_date, "backtest_end")

        print(f"\r{' '*80}\r", end="")
        self.print_report()

    # ── Report ────────────────────────────────────────────────────────────
    def print_report(self):
        all_trades = self.closed_positions
        if not all_trades:
            print("No trades executed during backtest period.")
            return

        wins = []
        losses = []
        for pos in all_trades:
            total_proceeds = sum(t.shares * t.price for t in pos.trades if t.action == "sell")
            cost = pos.initial_shares * pos.entry_price
            pnl = total_proceeds - cost - pos.commission_paid
            pnl_pct = (pnl / cost) * 100 if cost > 0 else 0
            entry = {"pos": pos, "pnl": pnl, "pnl_pct": pnl_pct}
            if pnl >= 0:
                wins.append(entry)
            else:
                losses.append(entry)

        total = len(wins) + len(losses)
        gross_wins = sum(w["pnl"] for w in wins)
        gross_losses = abs(sum(l["pnl"] for l in losses))

        # Max drawdown from equity curve
        peak = 0.0
        max_dd = 0.0
        max_dd_date = ""
        for snap in self.equity_curve:
            if snap["equity"] > peak:
                peak = snap["equity"]
            dd = ((peak - snap["equity"]) / peak) * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_dd_date = snap["date"]

        final_equity = self.equity_curve[-1]["equity"] if self.equity_curve else self.starting_cash
        total_return = ((final_equity - self.starting_cash) / self.starting_cash) * 100

        print(f"\n{'='*70}")
        print(f"  BACKTEST RESULTS")
        print(f"{'='*70}")
        print(f"  Starting Cash:      ${self.starting_cash:>12,.2f}")
        print(f"  Final Equity:       ${final_equity:>12,.2f}")
        print(f"  Net P&L:            ${final_equity - self.starting_cash:>12,.2f}  ({total_return:+.1f}%)")
        print(f"{'─'*70}")
        print(f"  Signals Generated:  {self.signals_generated:>6}")
        print(f"  Trades Executed:    {total:>6}")
        print(f"  Wins:               {len(wins):>6}  ({(len(wins)/total*100):.1f}%)" if total else "")
        print(f"  Losses:             {len(losses):>6}  ({(len(losses)/total*100):.1f}%)" if total else "")
        print(f"{'─'*70}")
        if wins:
            print(f"  Avg Win:            {sum(w['pnl_pct'] for w in wins)/len(wins):>+11.2f}%")
            print(f"  Gross Wins:         ${gross_wins:>12,.2f}")
        if losses:
            print(f"  Avg Loss:           {sum(l['pnl_pct'] for l in losses)/len(losses):>+11.2f}%")
            print(f"  Gross Losses:       ${gross_losses:>12,.2f}")
        print(f"  Profit Factor:      {gross_wins/gross_losses:>12.2f}" if gross_losses > 0 else "  Profit Factor:       N/A")
        print(f"  Max Drawdown:       {max_dd:>11.2f}%  ({max_dd_date})")
        print(f"{'─'*70}")

        # Best and worst
        all_entries = wins + losses
        if all_entries:
            best = max(all_entries, key=lambda x: x["pnl"])
            worst = min(all_entries, key=lambda x: x["pnl"])
            print(f"  Best Trade:         {best['pos'].symbol:<8} ${best['pnl']:>+10,.2f}  ({best['pnl_pct']:+.1f}%)")
            print(f"  Worst Trade:        {worst['pos'].symbol:<8} ${worst['pnl']:>+10,.2f}  ({worst['pnl_pct']:+.1f}%)")

        # Trade journal — show every individual trade (buy + each sell)
        print(f"\n{'='*100}")
        print(f"  TRADE JOURNAL (every execution)")
        print(f"{'='*100}")
        print(f"  {'Symbol':<8} {'Date':<12} {'Action':<6} {'Shares':>7} {'Price':>9} "
              f"{'Proceeds':>11} {'Reason':<18} {'Position P&L':>12}")
        print(f"  {'─'*93}")

        # Collect all individual trades across all positions, sorted by date
        all_executions = []
        for entry in all_entries:
            pos = entry["pos"]
            running_cost = 0.0
            running_proceeds = 0.0
            for t in pos.trades:
                if t.action == "buy":
                    running_cost = t.shares * t.price + COMMISSION
                    all_executions.append({
                        "date": t.date, "symbol": pos.symbol, "action": "BUY",
                        "shares": t.shares, "price": t.price,
                        "proceeds": -(t.shares * t.price + COMMISSION),
                        "reason": t.reason, "pos_pnl": None, "pos": pos,
                    })
                else:
                    sell_net = t.shares * t.price - COMMISSION
                    running_proceeds += sell_net
                    # Show running P&L for this position on the final sell
                    is_final = (t == pos.trades[-1])
                    all_executions.append({
                        "date": t.date, "symbol": pos.symbol, "action": "SELL",
                        "shares": t.shares, "price": t.price,
                        "proceeds": sell_net,
                        "reason": t.reason,
                        "pos_pnl": entry["pnl"] if is_final else None,
                        "pos": pos,
                    })

        all_executions.sort(key=lambda x: (x["date"], x["action"] == "SELL"))

        for ex in all_executions:
            pnl_str = f"${ex['pos_pnl']:>+10,.2f}" if ex["pos_pnl"] is not None else ""
            reason_display = {
                "signal": "signal",
                "stop": "STOP",
                "target1_partial": "T1 (25% out)",
                "target2": "T2 (remaining)",
                "manual": "manual",
                "backtest_end": "backtest end",
            }.get(ex["reason"], ex["reason"])
            action_color = ex["action"]
            print(f"  {ex['symbol']:<8} {ex['date']:<12} {action_color:<6} {ex['shares']:>7} "
                  f"${ex['price']:>8.2f} ${ex['proceeds']:>+10,.2f} {reason_display:<18} {pnl_str}")

        # Monthly breakdown
        print(f"\n{'='*70}")
        print(f"  MONTHLY EQUITY")
        print(f"{'='*70}")
        monthly = {}
        for snap in self.equity_curve:
            month = snap["date"][:7]
            monthly[month] = snap["equity"]
        prev = self.starting_cash
        for month, equity in monthly.items():
            change = equity - prev
            pct = (change / prev) * 100 if prev > 0 else 0
            bar = "█" * max(0, int(pct * 2)) if pct > 0 else "░" * max(0, int(abs(pct) * 2))
            print(f"  {month}  ${equity:>12,.2f}  {pct:>+6.1f}%  {bar}")
            prev = equity

        print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Backtest 8-21 EMA Swing Strategy")
    parser.add_argument("--start", default=DEFAULT_START, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=DEFAULT_END, help="End date YYYY-MM-DD")
    parser.add_argument("--symbols", nargs="*", help="Specific symbols to test (default: all from symbols.txt)")
    parser.add_argument("--cash", type=float, default=STARTING_CASH, help="Starting cash")
    args = parser.parse_args()

    symbols = args.symbols if args.symbols else load_symbols()
    if not symbols:
        print("No symbols found. Check symbols.txt")
        return

    bt = Backtester(starting_cash=args.cash)
    bt.run(symbols, args.start, args.end)


if __name__ == "__main__":
    main()
