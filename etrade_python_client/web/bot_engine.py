"""
Bot engine — runs the trading loop in a background thread,
exposing state for the web dashboard to read.

State is persisted to bot_state.json so the bot can resume
after a reboot, stop, or crash.
"""

import json
import math
import os
import threading
import time
import logging
from datetime import datetime

from market.market_data import MarketData
from strategy.signals import SignalGenerator, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD
from strategy.screener import StockScreener
from paper_trading.portfolio import PaperPortfolio
from db.database import TradingDatabase
import trading_config as cfg

logger = logging.getLogger("my_logger")

BOT_STATE_FILE = "bot_state.json"


class BotEngine:
    """Wraps the trading loop so the web layer can start/stop/query it."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Shared state (read by the web API)
        self.running = False
        self.cycle = 0
        self.tradeable: list[str] = []
        self.current_prices: dict[str, float] = {}
        self.signals: dict[str, str] = {}
        self.screen_results: list[dict] = []
        self.activity_log: list[dict] = []
        self.last_update: str | None = None
        self.error: str | None = None

        # Core objects
        self.market: MarketData | None = None
        self.portfolio: PaperPortfolio | None = None
        self.signal_gen: SignalGenerator | None = None
        self.screener: StockScreener | None = None
        self.db = TradingDatabase(cfg.DB_FILE)

        # Load saved state first, then portfolio
        self._load_bot_state()
        self._load_portfolio()

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def start(self):
        if self.running:
            return
        self._stop_event.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.running = True
        self._log_activity("BOT", "Bot started")
        self._save_bot_state()

    def stop(self):
        if not self.running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.running = False
        self._log_activity("BOT", "Bot stopped")
        self._save_bot_state()

    # ------------------------------------------------------------------ #
    #  State accessors (thread-safe)
    # ------------------------------------------------------------------ #

    def get_state(self) -> dict:
        """Return a snapshot of the bot's current state for the web API."""
        with self._lock:
            positions = []
            if self.portfolio:
                for sym, pos in sorted(self.portfolio.positions.items()):
                    mkt = self.current_prices.get(sym)
                    mkt_val = pos["qty"] * mkt if mkt else None
                    pnl = (mkt_val - pos["qty"] * pos["avg_cost"]) if mkt_val else None
                    pnl_pct = (pnl / (pos["qty"] * pos["avg_cost"]) * 100) if pnl and pos["avg_cost"] > 0 else None
                    positions.append({
                        "symbol": sym,
                        "qty": pos["qty"],
                        "avg_cost": round(pos["avg_cost"], 2),
                        "mkt_price": round(mkt, 2) if mkt else None,
                        "mkt_value": round(mkt_val, 2) if mkt_val else None,
                        "pnl": round(pnl, 2) if pnl else None,
                        "pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
                        "signal": self.signals.get(sym, "—"),
                    })

            total_value = (self.portfolio.cash if self.portfolio else 0)
            for p in positions:
                total_value += p["mkt_value"] if p["mkt_value"] else (p["qty"] * p["avg_cost"])

            return {
                "running": self.running,
                "cycle": self.cycle,
                "cash": round(self.portfolio.cash, 2) if self.portfolio else 0,
                "total_value": round(total_value, 2),
                "positions": positions,
                "tradeable": self.tradeable,
                "signals": self.signals,
                "screen_results": self.screen_results,
                "trade_log": (self.portfolio.trade_log[-50:] if self.portfolio else []),
                "activity_log": self.activity_log[-100:],
                "last_update": self.last_update,
                "error": self.error,
                "data_source": self.market.source if self.market else "yahoo",
                "screener_rerun_cycles": cfg.SCREENER_RERUN_CYCLES,
                "poll_interval": cfg.POLL_INTERVAL_SEC,
                "config": {
                    "watchlist_mode": cfg.WATCHLIST_MODE,
                    "poll_interval": cfg.POLL_INTERVAL_SEC,
                    "default_trade_qty": cfg.TRADE_DOLLAR_AMOUNT,
                    "stop_loss_pct": cfg.STOP_LOSS_PCT,
                    "take_profit_pct": cfg.TAKE_PROFIT_PCT,
                    "enable_rebalance": cfg.ENABLE_REBALANCE,
                    "excluded_symbols": cfg.EXCLUDED_SYMBOLS,
                    "screener_top_n": cfg.SCREENER_TOP_N,
                },
            }

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _load_portfolio(self):
        """Load portfolio from disk so positions are visible before the bot starts."""
        try:
            self.portfolio = PaperPortfolio(
                starting_cash=cfg.STARTING_CASH,
                state_file=cfg.STATE_FILE,
                seed_positions=cfg.SEED_POSITIONS,
            )
            if self.portfolio.positions:
                logger.info(
                    "Dashboard loaded %d position(s): %s",
                    len(self.portfolio.positions),
                    ", ".join(self.portfolio.positions.keys()),
                )
        except Exception as exc:
            logger.error("Failed to load portfolio on init: %s", exc)

    def refresh_prices(self):
        """
        Fetch live prices for all held positions (called from the web API).
        This lets the dashboard show current market values even when the bot
        is stopped.
        """
        if not self.portfolio or not self.portfolio.positions:
            return
        if self.market is None:
            self.market = MarketData()
        with self._lock:
            for symbol in list(self.portfolio.positions.keys()):
                price = self.market.get_price(symbol)
                if price is not None:
                    self.current_prices[symbol] = price

    # ------------------------------------------------------------------ #
    #  Bot state persistence (survives reboots)
    # ------------------------------------------------------------------ #

    def _save_bot_state(self):
        """Save bot state to disk so it can resume after restart."""
        state = {
            "cycle": self.cycle,
            "tradeable": self.tradeable,
            "current_prices": self.current_prices,
            "signals": self.signals,
            "screen_results": self.screen_results,
            "activity_log": self.activity_log[-200:],
            "last_update": self.last_update,
            "excluded_symbols": list(cfg.EXCLUDED_SYMBOLS),
            "symbol_limits": cfg.SYMBOL_LIMITS,
            "saved_at": datetime.now().isoformat(),
        }
        try:
            with open(BOT_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except OSError as exc:
            logger.error("Failed to save bot state: %s", exc)

    def _load_bot_state(self):
        """Restore bot state from disk after restart."""
        if not os.path.exists(BOT_STATE_FILE):
            return
        try:
            with open(BOT_STATE_FILE, "r") as f:
                state = json.load(f)

            self.cycle = state.get("cycle", 0)
            self.tradeable = state.get("tradeable", [])
            self.current_prices = state.get("current_prices", {})
            self.signals = state.get("signals", {})
            self.screen_results = state.get("screen_results", [])
            self.activity_log = state.get("activity_log", [])
            self.last_update = state.get("last_update")

            # Restore runtime config changes
            saved_excluded = state.get("excluded_symbols")
            if saved_excluded is not None:
                cfg.EXCLUDED_SYMBOLS.clear()
                cfg.EXCLUDED_SYMBOLS.extend(saved_excluded)

            saved_limits = state.get("symbol_limits")
            if saved_limits is not None:
                cfg.SYMBOL_LIMITS.clear()
                cfg.SYMBOL_LIMITS.update(saved_limits)

            saved_at = state.get("saved_at", "unknown")
            logger.info(
                "Restored bot state: cycle=%d, %d tradeable, %d prices, saved at %s",
                self.cycle, len(self.tradeable), len(self.current_prices), saved_at,
            )
            self._log_activity("BOT", f"State restored from {saved_at}")

        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load bot state: %s", exc)

    def _log_activity(self, action: str, message: str):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "message": message,
        }
        with self._lock:
            self.activity_log.append(entry)
            if len(self.activity_log) > 200:
                self.activity_log = self.activity_log[-200:]
        # Persist to database
        try:
            self.db.record_activity(action, message, cycle=self.cycle)
        except Exception:
            pass  # don't let DB errors break the bot

    @staticmethod
    def _get_trade_qty(symbol: str, action: str) -> int:
        limits = cfg.SYMBOL_LIMITS.get(symbol)
        if limits is None:
            return cfg.DEFAULT_TRADE_QTY
        key = "max_buy" if action == "BUY" else "max_sell"
        return limits.get(key, cfg.DEFAULT_TRADE_QTY)

    # ------------------------------------------------------------------ #
    #  Main loop (runs in background thread)
    # ------------------------------------------------------------------ #

    def _run(self):
        try:
            if self.market is None:
                self.market = MarketData()
            # Reuse the portfolio loaded at init (don't overwrite it)
            if self.portfolio is None:
                self.portfolio = PaperPortfolio(
                    starting_cash=cfg.STARTING_CASH,
                    state_file=cfg.STATE_FILE,
                    seed_positions=cfg.SEED_POSITIONS,
                )
            self.signal_gen = SignalGenerator(
                short_window=cfg.SHORT_SMA_WINDOW,
                long_window=cfg.LONG_SMA_WINDOW,
                rsi_period=cfg.RSI_PERIOD,
                rsi_overbought=cfg.RSI_OVERBOUGHT,
                rsi_oversold=cfg.RSI_OVERSOLD,
            )

            # Determine watchlist
            if cfg.WATCHLIST_MODE == "screener":
                self.screener = StockScreener(
                    sma_period=cfg.SCREENER_SMA_PERIOD,
                    rsi_period=cfg.RSI_PERIOD,
                    min_volume=cfg.SCREENER_MIN_VOLUME,
                    history_period=cfg.SCREENER_HISTORY,
                    excluded_symbols=set(cfg.EXCLUDED_SYMBOLS),
                    sectors=cfg.SCREENER_SECTORS,
                )
                self._log_activity("SCREEN", "Running stock screener...")
                self.screen_results = self.screener.screen(top_n=cfg.SCREENER_TOP_N)
                self.tradeable = [r["symbol"] for r in self.screen_results]
                self._log_activity("SCREEN", f"Selected: {', '.join(self.tradeable)}")
                self.db.record_screen(self.screen_results)
            else:
                self.tradeable = [s for s in cfg.WATCHLIST if s not in cfg.EXCLUDED_SYMBOLS]

            # Load historical data
            self._log_activity("DATA", "Loading historical prices...")
            for symbol in self.tradeable:
                history = self.market.fetch_history(symbol, period="3mo", interval="1d")
                if not history.empty:
                    self._log_activity("DATA", f"{symbol}: {len(history)} bars loaded")

            # Trading loop
            while not self._stop_event.is_set():
                self.cycle += 1
                self._run_cycle()
                self.last_update = datetime.now().isoformat()

                # Wait with interruptible sleep
                for _ in range(cfg.POLL_INTERVAL_SEC):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

        except Exception as exc:
            self.error = str(exc)
            logger.error("Bot engine error: %s", exc, exc_info=True)
            self._log_activity("ERROR", str(exc))
        finally:
            self.running = False

    def _run_cycle(self):
        """Execute one trading cycle."""
        # Skip trading when market is closed
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        if now_et.weekday() >= 5 or now_et.hour * 60 + now_et.minute < 570 or now_et.hour >= 16:
            self._log_activity("MARKET", f"Market closed ({now_et.strftime('%a %H:%M ET')}) — skipping")
            return
        # Verify not a holiday by checking if SPY traded today
        try:
            import yfinance as yf
            hist = yf.Ticker("SPY").history(period="1d", interval="1m")
            if hist.empty or hist.index[-1].strftime("%Y-%m-%d") != now_et.strftime("%Y-%m-%d"):
                self._log_activity("MARKET", "Market closed today (holiday) — skipping")
                return
        except Exception:
            pass  # If check fails, proceed with trading

        # Re-screen periodically
        if (cfg.WATCHLIST_MODE == "screener"
                and self.screener
                and self.cycle > 1
                and (self.cycle - 1) % cfg.SCREENER_RERUN_CYCLES == 0):
            self._log_activity("SCREEN", "Refreshing stock screen...")
            self.screen_results = self.screener.screen(top_n=cfg.SCREENER_TOP_N)
            new_tradeable = [r["symbol"] for r in self.screen_results]
            self.db.record_screen(self.screen_results, cycle=self.cycle)
            if new_tradeable != self.tradeable:
                self.tradeable = new_tradeable
                self._log_activity("SCREEN", f"Updated: {', '.join(self.tradeable)}")
                for symbol in self.tradeable:
                    if len(self.market.get_price_history(symbol)) == 0:
                        self.market.fetch_history(symbol, period="3mo", interval="1d")

        # Phase 1: Collect prices and signals
        with self._lock:
            self.signals = {}

        # Build the active symbol list: screener picks + non-excluded held positions
        active_symbols = list(self.tradeable)
        if self.portfolio:
            for sym in self.portfolio.positions:
                if sym not in active_symbols and sym not in cfg.EXCLUDED_SYMBOLS:
                    active_symbols.append(sym)

        # Fetch prices for ALL active symbols (tradeable + held)
        if self.portfolio:
            for symbol in list(self.portfolio.positions.keys()):
                if symbol in cfg.EXCLUDED_SYMBOLS:
                    continue
                if symbol not in active_symbols:
                    continue
                price = self.market.get_price(symbol)
                if price:
                    with self._lock:
                        self.current_prices[symbol] = price

        # Fetch prices and generate signals for tradeable (screener) symbols

        for symbol in self.tradeable:
            if symbol in cfg.EXCLUDED_SYMBOLS:
                continue
            price = self.market.get_price(symbol)
            if price is None:
                continue
            with self._lock:
                self.current_prices[symbol] = price
            self.market.record_price(symbol, price)
            history = self.market.get_price_history(symbol)

            if len(history) < cfg.MIN_DATA_POINTS:
                sig = SIGNAL_HOLD
            else:
                sig = self.signal_gen.generate(history)

            with self._lock:
                self.signals[symbol] = sig

        # Record prices to database
        for symbol in active_symbols:
            price = self.current_prices.get(symbol)
            if price:
                try:
                    self.db.record_price(symbol, price, cycle=self.cycle)
                except Exception:
                    pass

        # ---------------------------------------------------------------
        #  SELL-ONLY-TO-BUY LOGIC
        #  Sells only happen to fund a specific buy. No standalone sells.
        # ---------------------------------------------------------------

        # Collect BUY candidates (symbols with BUY signal we can act on)
        buy_candidates = []
        for symbol in active_symbols:
            if symbol in cfg.EXCLUDED_SYMBOLS:
                continue
            if self.signals.get(symbol) != SIGNAL_BUY:
                continue
            price = self.current_prices.get(symbol)
            if not price:
                continue
            buy_qty = self._get_trade_qty(symbol, "BUY")
            held = self.portfolio.positions.get(symbol, {}).get("qty", 0)
            buy_qty = min(buy_qty, cfg.MAX_POSITION_SIZE - held)
            if buy_qty <= 0:
                continue
            cost = buy_qty * price
            buy_candidates.append((symbol, buy_qty, price, cost))

        # Execute buys — sell only when needed to fund them
        for symbol, buy_qty, price, cost in buy_candidates:
            if self.portfolio.cash >= cost:
                # Have enough cash already
                if self.portfolio.buy(symbol, buy_qty, price):
                    self._log_activity("BUY", f"{buy_qty} x {symbol} @ ${price:.2f}")
                    self.db.record_trade("BUY", symbol, buy_qty, price, self.portfolio.cash,
                                         trigger="SIGNAL", cycle=self.cycle)
            elif cfg.ENABLE_REBALANCE:
                # Need to sell something to fund this buy
                funded = self._rebalance(symbol, cost)
                if funded and self.portfolio.buy(symbol, buy_qty, price):
                    self._log_activity("BUY", f"{buy_qty} x {symbol} @ ${price:.2f} (rebalanced)")
                    self.db.record_trade("BUY", symbol, buy_qty, price, self.portfolio.cash,
                                         trigger="REBALANCE", cycle=self.cycle)
                else:
                    affordable = int(self.portfolio.cash // price) if price > 0 else 0
                    if affordable > 0 and self.portfolio.buy(symbol, affordable, price):
                        self._log_activity("BUY", f"{affordable} x {symbol} @ ${price:.2f} (partial)")
                        self.db.record_trade("BUY", symbol, affordable, price, self.portfolio.cash,
                                             trigger="PARTIAL", cycle=self.cycle)

        # Record portfolio snapshot at end of cycle
        try:
            total_val = self.portfolio.cash
            for sym, pos in self.portfolio.positions.items():
                p = self.current_prices.get(sym)
                total_val += pos["qty"] * p if p else pos["qty"] * pos["avg_cost"]
            self.db.record_snapshot(
                self.portfolio.cash, total_val,
                self.portfolio.positions, cycle=self.cycle,
            )
        except Exception:
            pass

        # Save bot state after every cycle
        self._save_bot_state()

    def _rebalance(self, buy_symbol: str, cash_needed: float) -> bool:
        """Sell positions to raise cash for a buy."""
        signal_rank = {SIGNAL_SELL: 0, SIGNAL_HOLD: 1, SIGNAL_BUY: 2}
        candidates = []
        for sym, pos in self.portfolio.positions.items():
            if sym == buy_symbol or sym in cfg.EXCLUDED_SYMBOLS:
                continue
            price = self.current_prices.get(sym)
            if not price:
                continue
            sig = self.signals.get(sym, SIGNAL_HOLD)
            pnl_pct = (price - pos["avg_cost"]) / pos["avg_cost"] if pos["avg_cost"] > 0 else 0
            candidates.append((signal_rank.get(sig, 1), pnl_pct, sym, pos, price, sig))

        candidates.sort()

        for _, _, sym, pos, price, sig in candidates:
            if self.portfolio.cash >= cash_needed:
                break
            if sig == SIGNAL_BUY:
                continue
            max_sell = self._get_trade_qty(sym, "SELL")
            sell_qty = min(max_sell, pos["qty"])
            still_need = cash_needed - self.portfolio.cash
            min_shares = math.ceil(still_need / price)
            sell_qty = min(sell_qty, min_shares)
            if sell_qty > 0 and self.portfolio.sell(sym, sell_qty, price):
                self._log_activity("REBALANCE", f"SELL {sell_qty} x {sym} @ ${price:.2f} to fund {buy_symbol}")
                self.db.record_trade("SELL", sym, sell_qty, price, self.portfolio.cash,
                                     trigger="REBALANCE", cycle=self.cycle)

        return self.portfolio.cash >= cash_needed
