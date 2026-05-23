"""
AI Paper Trading Bot — Real Prices, Simulated Trades.

This bot:
  1. Fetches REAL market prices from Yahoo Finance (no API keys needed)
  2. Seeds the strategy with historical price data so signals start immediately
  3. Polls live quotes at a configurable interval
  4. Feeds price data into a technical-indicator strategy (SMA crossover + RSI)
  5. Executes BUY / SELL decisions against a simulated paper portfolio
  6. Rebalances by selling existing positions to fund new buys (no seed cash needed)
  7. Applies stop-loss and take-profit risk management
  8. Logs every decision and prints a portfolio summary each cycle

*** PAPER TRADING ONLY — no real orders are placed ***
"""

from __future__ import print_function
import math
import time
import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from market.market_data import MarketData
from strategy.signals import SignalGenerator, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD
from paper_trading.portfolio import PaperPortfolio
from trading_config import (
    WATCHLIST, WATCHLIST_MODE, SYMBOL_LIMITS, EXCLUDED_SYMBOLS,
    POLL_INTERVAL_SEC, STARTING_CASH, MAX_POSITION_SIZE, MAX_PORTFOLIO_PCT,
    SHORT_SMA_WINDOW, LONG_SMA_WINDOW, RSI_PERIOD,
    RSI_OVERBOUGHT, RSI_OVERSOLD, MIN_DATA_POINTS,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT,
    ENABLE_STOP_LOSS, ENABLE_TAKE_PROFIT, STATE_FILE,
    SEED_POSITIONS, ENABLE_REBALANCE, REBALANCE_SELL_PRIORITY,
    SCREENER_TOP_N, SCREENER_SMA_PERIOD, SCREENER_MIN_VOLUME,
    SCREENER_HISTORY, SCREENER_RERUN_CYCLES, SCREENER_SECTORS,
    TRADE_DOLLAR_AMOUNT, COOLDOWN_HOURS,
    ENABLE_MARKET_REGIME, REGIME_BENCHMARK, REGIME_SMA_PERIOD,
)
from strategy.indicators import sma as compute_sma
from strategy.screener import StockScreener, print_screening_results

# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("my_logger")
logger.setLevel(logging.DEBUG)
file_handler = RotatingFileHandler(
    "python_client.log", maxBytes=5 * 1024 * 1024, backupCount=3
)
fmt = logging.Formatter(
    "%(asctime)-15s %(message)s", datefmt="%m/%d/%Y %I:%M:%S %p"
)
file_handler.setFormatter(fmt)
logger.addHandler(file_handler)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(fmt)
logger.addHandler(console)


# ---------------------------------------------------------------------------
#  Trade sizing helper (dollar-based)
# ---------------------------------------------------------------------------
def get_trade_qty(symbol: str, action: str, price: float = None, portfolio_value: float = None) -> int:
    """
    Return shares to trade based on dollar amount.
    Uses per-symbol dollar limits from SYMBOL_LIMITS, falling back to TRADE_DOLLAR_AMOUNT.
    """
    if not price or price <= 0:
        return 1

    # Determine dollar budget for this trade
    limits = SYMBOL_LIMITS.get(symbol)
    if limits:
        key = "max_buy" if action == "BUY" else "max_sell"
        dollar_budget = limits.get(key, TRADE_DOLLAR_AMOUNT)
    else:
        dollar_budget = TRADE_DOLLAR_AMOUNT

    # Cap by portfolio percentage on buys
    if portfolio_value and portfolio_value > 0 and action == "BUY":
        max_dollar = portfolio_value * MAX_PORTFOLIO_PCT
        dollar_budget = min(dollar_budget, max_dollar)

    qty = int(dollar_budget // price)
    return max(qty, 1)


# ---------------------------------------------------------------------------
#  Cooldown tracker
# ---------------------------------------------------------------------------
_last_trade_time: dict[str, datetime] = {}


def is_on_cooldown(symbol: str) -> bool:
    """Return True if symbol was traded within COOLDOWN_HOURS."""
    last = _last_trade_time.get(symbol)
    if last is None:
        return False
    return datetime.now() - last < timedelta(hours=COOLDOWN_HOURS)


def record_cooldown(symbol: str):
    """Mark a symbol as just traded."""
    _last_trade_time[symbol] = datetime.now()


# ---------------------------------------------------------------------------
#  Risk management helpers
# ---------------------------------------------------------------------------
def check_stop_loss(portfolio: PaperPortfolio, symbol: str, price: float) -> bool:
    """Sell the full position if price dropped below stop-loss threshold."""
    if not ENABLE_STOP_LOSS or symbol not in portfolio.positions:
        return False
    pos = portfolio.positions[symbol]
    loss_pct = (pos["avg_cost"] - price) / pos["avg_cost"]
    if loss_pct >= STOP_LOSS_PCT:
        qty = pos["qty"]
        logger.warning(
            "STOP-LOSS triggered for %s — down %.1f%% from avg cost $%.2f",
            symbol, loss_pct * 100, pos["avg_cost"],
        )
        portfolio.sell(symbol, qty, price)
        print(f"  !! STOP-LOSS SELL {qty} x {symbol} @ ${price:.2f} (loss {loss_pct:.1%})")
        return True
    return False


def check_take_profit(portfolio: PaperPortfolio, symbol: str, price: float) -> bool:
    """Sell the full position if price rose above take-profit threshold."""
    if not ENABLE_TAKE_PROFIT or symbol not in portfolio.positions:
        return False
    pos = portfolio.positions[symbol]
    gain_pct = (price - pos["avg_cost"]) / pos["avg_cost"]
    if gain_pct >= TAKE_PROFIT_PCT:
        qty = pos["qty"]
        logger.info(
            "TAKE-PROFIT triggered for %s — up %.1f%% from avg cost $%.2f",
            symbol, gain_pct * 100, pos["avg_cost"],
        )
        portfolio.sell(symbol, qty, price)
        print(f"  $$ TAKE-PROFIT SELL {qty} x {symbol} @ ${price:.2f} (gain {gain_pct:.1%})")
        return True
    return False


# ---------------------------------------------------------------------------
#  Rebalancing — sell existing positions to fund a new buy
# ---------------------------------------------------------------------------
def rebalance_to_fund_buy(
    portfolio: PaperPortfolio,
    buy_symbol: str,
    cash_needed: float,
    signals: dict[str, str],
    current_prices: dict[str, float],
) -> bool:
    """
    Sell shares from existing positions to raise enough cash for a buy.

    Only sells positions that are NOT in EXCLUDED_SYMBOLS and NOT the
    symbol we want to buy. Respects per-symbol sell limits.

    Returns True if enough cash was raised, False otherwise.
    """
    if not ENABLE_REBALANCE:
        return False

    # Build list of candidates we can sell
    candidates = []
    for sym, pos in portfolio.positions.items():
        if sym == buy_symbol:
            continue
        if sym in EXCLUDED_SYMBOLS:
            continue
        price = current_prices.get(sym)
        if price is None or price <= 0:
            continue

        signal = signals.get(sym, SIGNAL_HOLD)
        pnl_pct = (price - pos["avg_cost"]) / pos["avg_cost"] if pos["avg_cost"] > 0 else 0
        mkt_value = pos["qty"] * price

        candidates.append({
            "symbol": sym,
            "qty": pos["qty"],
            "price": price,
            "signal": signal,
            "pnl_pct": pnl_pct,
            "mkt_value": mkt_value,
        })

    if not candidates:
        logger.info("Rebalance: no sellable positions available")
        return False

    # Sort candidates by priority
    signal_rank = {SIGNAL_SELL: 0, SIGNAL_HOLD: 1, SIGNAL_BUY: 2}

    if REBALANCE_SELL_PRIORITY == "weakest_signal":
        candidates.sort(key=lambda c: (signal_rank.get(c["signal"], 1), c["pnl_pct"]))
    elif REBALANCE_SELL_PRIORITY == "largest_loss":
        candidates.sort(key=lambda c: c["pnl_pct"])
    elif REBALANCE_SELL_PRIORITY == "largest_value":
        candidates.sort(key=lambda c: -c["mkt_value"])

    # Sell from candidates until we have enough cash
    raised = 0.0
    for cand in candidates:
        if portfolio.cash >= cash_needed:
            break
        # Never sell a position with a BUY signal during rebalance
        if cand["signal"] == SIGNAL_BUY:
            logger.info("Rebalance: skipping %s (has BUY signal)", cand["symbol"])
            continue

        max_sell = get_trade_qty(cand["symbol"], "SELL", cand["price"])
        sell_qty = min(max_sell, cand["qty"])

        # Only sell enough to cover what we need
        still_need = cash_needed - portfolio.cash
        min_shares = math.ceil(still_need / cand["price"])
        sell_qty = min(sell_qty, min_shares)

        if sell_qty <= 0:
            continue

        ok = portfolio.sell(cand["symbol"], sell_qty, cand["price"])
        if ok:
            raised += sell_qty * cand["price"]
            print(
                f"  << REBALANCE SELL {sell_qty} x {cand['symbol']} "
                f"@ ${cand['price']:.2f} to fund {buy_symbol}"
            )

    return portfolio.cash >= cash_needed


# ---------------------------------------------------------------------------
#  Main trading loop
# ---------------------------------------------------------------------------
def run_bot():
    """Fetch real prices, generate signals, execute paper trades."""

    market = MarketData()
    portfolio = PaperPortfolio(
        starting_cash=STARTING_CASH,
        state_file=STATE_FILE,
        seed_positions=SEED_POSITIONS,
    )
    signal_gen = SignalGenerator(
        short_window=SHORT_SMA_WINDOW,
        long_window=LONG_SMA_WINDOW,
        rsi_period=RSI_PERIOD,
        rsi_overbought=RSI_OVERBOUGHT,
        rsi_oversold=RSI_OVERSOLD,
    )

    # --- Determine watchlist ---
    if WATCHLIST_MODE == "screener":
        screener = StockScreener(
            sma_period=SCREENER_SMA_PERIOD,
            rsi_period=RSI_PERIOD,
            min_volume=SCREENER_MIN_VOLUME,
            history_period=SCREENER_HISTORY,
            excluded_symbols=set(EXCLUDED_SYMBOLS),
            sectors=SCREENER_SECTORS,
        )
        print("\nScreening stocks from diverse universe...")
        screen_results = screener.screen(top_n=SCREENER_TOP_N)
        print_screening_results(screen_results)
        tradeable = [r["symbol"] for r in screen_results]
    else:
        tradeable = [s for s in WATCHLIST if s not in EXCLUDED_SYMBOLS]

    excluded = list(EXCLUDED_SYMBOLS)

    print("\n" + "=" * 60)
    print("  AI PAPER TRADING BOT")
    print("  Real prices from Yahoo Finance — simulated trades only")
    print("=" * 60)
    print(f"  Mode         : {'Auto-screener' if WATCHLIST_MODE == 'screener' else 'Static watchlist'}")
    print(f"  Trading      : {', '.join(tradeable)}")
    if excluded:
        print(f"  Excluded     : {', '.join(excluded)} (protected)")
    if SEED_POSITIONS:
        print(f"  Seed positions: {', '.join(SEED_POSITIONS.keys())}")
    print(f"  Starting cash: ${STARTING_CASH:,.2f}")
    print(f"  Rebalancing  : {'ON' if ENABLE_REBALANCE else 'OFF'}"
          + (f" ({REBALANCE_SELL_PRIORITY})" if ENABLE_REBALANCE else ""))
    print(f"  Default trade: ${TRADE_DOLLAR_AMOUNT} per signal")
    print(f"  Poll interval: {POLL_INTERVAL_SEC}s")
    print(f"  Strategy     : SMA({SHORT_SMA_WINDOW}/{LONG_SMA_WINDOW}) + RSI({RSI_PERIOD})")
    print(f"  Stop-loss    : {'ON' if ENABLE_STOP_LOSS else 'OFF'} ({STOP_LOSS_PCT:.0%})")
    print(f"  Take-profit  : {'ON' if ENABLE_TAKE_PROFIT else 'OFF'} ({TAKE_PROFIT_PCT:.0%})")
    if SYMBOL_LIMITS:
        print("  Per-symbol limits:")
        for sym, lim in SYMBOL_LIMITS.items():
            print(f"    {sym}: max_buy=${lim.get('max_buy', TRADE_DOLLAR_AMOUNT)}, "
                  f"max_sell=${lim.get('max_sell', TRADE_DOLLAR_AMOUNT)}")
    print("=" * 60)

    # Seed with historical data so signals can fire on the first cycle
    print("\nLoading historical price data...")
    for symbol in tradeable:
        history = market.fetch_history(symbol, period="3mo", interval="1d")
        if not history.empty:
            print(f"  {symbol}: loaded {len(history)} daily bars")
        else:
            print(f"  {symbol}: WARNING — no historical data")

    # Load benchmark data for market regime filter
    if ENABLE_MARKET_REGIME:
        spy_hist = market.fetch_history(REGIME_BENCHMARK, period="3mo", interval="1d")
        print(f"  {REGIME_BENCHMARK}: loaded {len(spy_hist)} daily bars (regime filter)")

    print("\nStarting trading loop (Ctrl+C to stop)...\n")

    cycle = 0
    current_prices: dict[str, float] = {}

    try:
        while True:
            cycle += 1
            logger.info("=== Cycle %d ===", cycle)

            # Skip trading when market is closed (checks real market status via Yahoo Finance)
            from zoneinfo import ZoneInfo
            now_et = datetime.now(ZoneInfo("America/New_York"))
            market_open = False
            # Quick time check first (no point querying if obviously closed)
            if now_et.weekday() < 5 and now_et.hour * 60 + now_et.minute >= 570 and now_et.hour < 16:
                # Verify market is actually open (handles holidays) by checking if SPY has traded today
                try:
                    import yfinance as yf
                    spy = yf.Ticker("SPY")
                    today_str = now_et.strftime("%Y-%m-%d")
                    hist = spy.history(period="1d", interval="1m")
                    if not hist.empty and hist.index[-1].strftime("%Y-%m-%d") == today_str:
                        market_open = True
                    else:
                        logger.info("Market appears closed today (holiday/non-trading day)")
                except Exception:
                    # If check fails, fall back to time-based assumption
                    market_open = True

            if not market_open:
                logger.info("Market closed (%s ET) — sleeping", now_et.strftime("%a %H:%M"))
                print(f"  💤 Market closed ({now_et.strftime('%a %H:%M ET')}) — waiting...")
                time.sleep(POLL_INTERVAL_SEC)
                continue

            # Re-screen periodically to refresh picks
            if (WATCHLIST_MODE == "screener"
                    and cycle > 1
                    and (cycle - 1) % SCREENER_RERUN_CYCLES == 0):
                print("\n  Refreshing stock screen...")
                screen_results = screener.screen(top_n=SCREENER_TOP_N)
                new_tradeable = [r["symbol"] for r in screen_results]
                if new_tradeable != tradeable:
                    print(f"  Updated watchlist: {', '.join(new_tradeable)}")
                    tradeable = new_tradeable
                    # Load history for any new symbols
                    for symbol in tradeable:
                        if len(market.get_price_history(symbol)) == 0:
                            market.fetch_history(symbol, period="3mo", interval="1d")

            # Refresh daily price history every 16 cycles (~4 hours)
            if cycle > 1 and (cycle - 1) % 16 == 0:
                logger.info("Refreshing daily price history...")
                for symbol in tradeable:
                    market.fetch_history(symbol, period="3mo", interval="1d")
                if ENABLE_MARKET_REGIME:
                    market.fetch_history(REGIME_BENCHMARK, period="3mo", interval="1d")

            # --- Phase 1: Collect prices and signals for all symbols ---
            signals: dict[str, str] = {}

            for symbol in tradeable:
                price = market.get_price(symbol)
                if price is None:
                    logger.warning("Could not fetch price for %s — skipping", symbol)
                    continue

                current_prices[symbol] = price
                # Signal generation uses daily close data only (fetched at startup)
                # Live price is only used for stop-loss/take-profit and portfolio valuation
                history = market.get_price_history(symbol)

                logger.info("%s  price=$%.2f  history=%d daily bars", symbol, price, len(history))

                if len(history) < MIN_DATA_POINTS:
                    signals[symbol] = SIGNAL_HOLD
                else:
                    signals[symbol] = signal_gen.generate(history)

                logger.info("%s signal: %s", symbol, signals[symbol])

            # --- Phase 2: Execute risk management (stop-loss / take-profit) ---
            for symbol in tradeable:
                if symbol not in current_prices:
                    continue
                price = current_prices[symbol]
                if check_stop_loss(portfolio, symbol, price):
                    signals[symbol] = SIGNAL_HOLD  # already acted
                    continue
                if check_take_profit(portfolio, symbol, price):
                    signals[symbol] = SIGNAL_HOLD
                    continue

            # --- Phase 3: Execute SELL signals first (frees up cash) ---
            for symbol in tradeable:
                if signals.get(symbol) != SIGNAL_SELL:
                    continue
                if symbol not in portfolio.positions:
                    logger.info("SELL signal for %s but no position — skipping", symbol)
                    continue
                if is_on_cooldown(symbol):
                    logger.info("SELL signal for %s but on cooldown — skipping", symbol)
                    continue
                price = current_prices[symbol]
                sell_qty = get_trade_qty(symbol, "SELL", price)
                sell_qty = min(sell_qty, portfolio.positions[symbol]["qty"])
                ok = portfolio.sell(symbol, sell_qty, price)
                if ok:
                    record_cooldown(symbol)
                    print(f"  >> PAPER SELL {sell_qty} x {symbol} @ ${price:.2f}")

            # --- Phase 4: Execute BUY signals (use cash or rebalance) ---
            # Market regime filter: skip all buys if broad market is in downtrend
            market_regime_ok = True
            if ENABLE_MARKET_REGIME:
                spy_history = market.get_price_history(REGIME_BENCHMARK)
                if len(spy_history) >= REGIME_SMA_PERIOD:
                    spy_sma = compute_sma(spy_history, REGIME_SMA_PERIOD)
                    spy_price = float(spy_history.iloc[-1])
                    spy_sma_val = float(spy_sma.iloc[-1])
                    market_regime_ok = spy_price > spy_sma_val
                    if not market_regime_ok:
                        logger.info(
                            "MARKET REGIME: RISK-OFF — %s ($%.2f) below %d-day SMA ($%.2f). No buys.",
                            REGIME_BENCHMARK, spy_price, REGIME_SMA_PERIOD, spy_sma_val,
                        )
                        print(f"  ⚠️  RISK-OFF: {REGIME_BENCHMARK} below {REGIME_SMA_PERIOD}-day SMA — skipping all buys")

            portfolio_value = sum(
                current_prices.get(s, 0) * portfolio.positions.get(s, {}).get("qty", 0)
                for s in portfolio.positions
            ) + portfolio.cash

            for symbol in tradeable:
                if signals.get(symbol) != SIGNAL_BUY:
                    continue
                if not market_regime_ok:
                    logger.info("BUY signal for %s suppressed by market regime filter", symbol)
                    continue
                if is_on_cooldown(symbol):
                    logger.info("BUY signal for %s but on cooldown — skipping", symbol)
                    continue
                price = current_prices[symbol]
                buy_qty = get_trade_qty(symbol, "BUY", price, portfolio_value)

                # Enforce max position size
                held = portfolio.positions.get(symbol, {}).get("qty", 0)
                buy_qty = min(buy_qty, MAX_POSITION_SIZE - held)
                if buy_qty <= 0:
                    logger.info("%s — at max position (%d). Skipping BUY.", symbol, held)
                    continue

                cost = buy_qty * price

                # Try buying with available cash first
                if portfolio.cash >= cost:
                    ok = portfolio.buy(symbol, buy_qty, price)
                    if ok:
                        record_cooldown(symbol)
                        print(f"  >> PAPER BUY  {buy_qty} x {symbol} @ ${price:.2f}")
                else:
                    # Not enough cash — try rebalancing
                    logger.info(
                        "BUY %s needs $%.2f but only $%.2f cash — attempting rebalance",
                        symbol, cost, portfolio.cash,
                    )
                    funded = rebalance_to_fund_buy(
                        portfolio, symbol, cost, signals, current_prices
                    )
                    if funded:
                        ok = portfolio.buy(symbol, buy_qty, price)
                        if ok:
                            record_cooldown(symbol)
                            print(f"  >> PAPER BUY  {buy_qty} x {symbol} @ ${price:.2f} (funded by rebalance)")
                    else:
                        # Try buying fewer shares with whatever cash we have
                        affordable = int(portfolio.cash // price) if price > 0 else 0
                        if affordable > 0:
                            ok = portfolio.buy(symbol, affordable, price)
                            if ok:
                                record_cooldown(symbol)
                                print(f"  >> PAPER BUY  {affordable} x {symbol} @ ${price:.2f} (partial fill)")
                        else:
                            print(f"  >> BUY signal for {symbol} but cannot raise enough cash")

            # --- Portfolio summary ---
            print(portfolio.summary(current_prices))

            logger.info("Sleeping %ds until next cycle...", POLL_INTERVAL_SEC)
            time.sleep(POLL_INTERVAL_SEC)

    except KeyboardInterrupt:
        print("\n\nBot stopped by user.")
        print(portfolio.summary(current_prices))
        print(f"\nTotal trades executed: {len(portfolio.trade_log)}")


if __name__ == "__main__":
    run_bot()
