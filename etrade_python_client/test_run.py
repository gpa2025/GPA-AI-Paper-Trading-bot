"""
Quick single-cycle test of the paper trading bot with rebalancing.
Seeds a portfolio with positions, then runs one cycle to show
how the bot sells existing holdings to fund new buys.
"""

import math
import logging
from market.market_data import MarketData
from strategy.signals import SignalGenerator, SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD
from paper_trading.portfolio import PaperPortfolio
from trading_config import (
    WATCHLIST, DEFAULT_TRADE_QTY, SYMBOL_LIMITS, EXCLUDED_SYMBOLS,
    MAX_POSITION_SIZE, SHORT_SMA_WINDOW, LONG_SMA_WINDOW, RSI_PERIOD,
    RSI_OVERBOUGHT, RSI_OVERSOLD, MIN_DATA_POINTS,
    ENABLE_REBALANCE, REBALANCE_SELL_PRIORITY,
)

logger = logging.getLogger("my_logger")
logger.setLevel(logging.INFO)
console = logging.StreamHandler()
fmt = logging.Formatter("%(asctime)-15s %(message)s", datefmt="%m/%d/%Y %I:%M:%S %p")
console.setFormatter(fmt)
logger.addHandler(console)


def get_trade_qty(symbol, action):
    limits = SYMBOL_LIMITS.get(symbol)
    if limits is None:
        return DEFAULT_TRADE_QTY
    key = "max_buy" if action == "BUY" else "max_sell"
    return limits.get(key, DEFAULT_TRADE_QTY)


# Test seed: pretend we hold these positions with $0 cash
TEST_SEED = {
    "AAPL": {"qty": 10, "avg_cost": 250.00},
    "MSFT": {"qty": 5,  "avg_cost": 400.00},
    "GOOGL": {"qty": 3, "avg_cost": 330.00},
}


def main():
    market = MarketData()
    portfolio = PaperPortfolio(
        starting_cash=0.00,
        state_file="test_portfolio.json",
        seed_positions=TEST_SEED,
    )
    signal_gen = SignalGenerator(
        short_window=SHORT_SMA_WINDOW,
        long_window=LONG_SMA_WINDOW,
        rsi_period=RSI_PERIOD,
        rsi_overbought=RSI_OVERBOUGHT,
        rsi_oversold=RSI_OVERSOLD,
    )

    tradeable = [s for s in WATCHLIST if s not in EXCLUDED_SYMBOLS]

    print("=" * 60)
    print("  TEST RUN — Rebalancing Demo (single cycle)")
    print("=" * 60)
    print(f"  Tradeable : {', '.join(tradeable)}")
    print(f"  Excluded  : {', '.join(EXCLUDED_SYMBOLS)}")
    print(f"  Cash      : ${portfolio.cash:,.2f}")
    print(f"  Rebalance : {'ON' if ENABLE_REBALANCE else 'OFF'} ({REBALANCE_SELL_PRIORITY})")
    print("  Seed positions:")
    for sym, pos in TEST_SEED.items():
        print(f"    {sym}: {pos['qty']} shares @ ${pos['avg_cost']:.2f}")
    print("=" * 60)

    # Load history
    print("\nFetching historical prices...")
    for symbol in tradeable:
        history = market.fetch_history(symbol, period="3mo", interval="1d")
        print(f"  {symbol}: {len(history)} bars")

    # Phase 1: collect prices and signals
    print("\n--- Collecting prices & signals ---\n")
    current_prices = {}
    signals = {}

    for symbol in tradeable:
        price = market.get_price(symbol)
        if price is None:
            print(f"  {symbol}: no price — skipping")
            continue
        current_prices[symbol] = price
        market.record_price(symbol, price)
        history = market.get_price_history(symbol)

        if len(history) < MIN_DATA_POINTS:
            signals[symbol] = SIGNAL_HOLD
        else:
            signals[symbol] = signal_gen.generate(history)

        print(f"  {symbol}: ${price:.2f}  signal={signals[symbol]}")

    # Phase 2: execute sells first
    print("\n--- Executing SELL signals ---")
    for symbol in tradeable:
        if signals.get(symbol) != SIGNAL_SELL:
            continue
        if symbol not in portfolio.positions:
            print(f"  {symbol}: SELL signal but no position")
            continue
        price = current_prices[symbol]
        sell_qty = get_trade_qty(symbol, "SELL")
        sell_qty = min(sell_qty, portfolio.positions[symbol]["qty"])
        ok = portfolio.sell(symbol, sell_qty, price)
        print(f"  SELL {sell_qty} x {symbol} @ ${price:.2f} -> {'OK' if ok else 'FAILED'}")

    # Phase 3: execute buys (with rebalancing)
    print("\n--- Executing BUY signals (with rebalance) ---")
    for symbol in tradeable:
        if signals.get(symbol) != SIGNAL_BUY:
            continue
        price = current_prices[symbol]
        buy_qty = get_trade_qty(symbol, "BUY")
        held = portfolio.positions.get(symbol, {}).get("qty", 0)
        buy_qty = min(buy_qty, MAX_POSITION_SIZE - held)
        if buy_qty <= 0:
            print(f"  {symbol}: at max position")
            continue

        cost = buy_qty * price
        print(f"  {symbol}: want to BUY {buy_qty} @ ${price:.2f} = ${cost:.2f}, cash=${portfolio.cash:.2f}")

        if portfolio.cash >= cost:
            ok = portfolio.buy(symbol, buy_qty, price)
            print(f"  BUY {buy_qty} x {symbol} -> {'OK' if ok else 'FAILED'}")
        elif ENABLE_REBALANCE:
            print(f"  Not enough cash — rebalancing...")
            # Simple rebalance: sell from weakest
            signal_rank = {SIGNAL_SELL: 0, SIGNAL_HOLD: 1, SIGNAL_BUY: 2}
            candidates = []
            for s, pos in portfolio.positions.items():
                if s == symbol or s in EXCLUDED_SYMBOLS:
                    continue
                p = current_prices.get(s)
                if p is None:
                    continue
                candidates.append((signal_rank.get(signals.get(s, SIGNAL_HOLD), 1), s, pos, p))
            candidates.sort()

            for _, cand_sym, cand_pos, cand_price in candidates:
                if portfolio.cash >= cost:
                    break
                if signals.get(cand_sym) == SIGNAL_BUY:
                    continue
                max_sell = get_trade_qty(cand_sym, "SELL")
                sell_qty = min(max_sell, cand_pos["qty"])
                still_need = cost - portfolio.cash
                min_shares = math.ceil(still_need / cand_price)
                sell_qty = min(sell_qty, min_shares)
                if sell_qty > 0:
                    ok = portfolio.sell(cand_sym, sell_qty, cand_price)
                    print(f"    REBALANCE SELL {sell_qty} x {cand_sym} @ ${cand_price:.2f} -> {'OK' if ok else 'FAILED'}")

            if portfolio.cash >= cost:
                ok = portfolio.buy(symbol, buy_qty, price)
                print(f"  BUY {buy_qty} x {symbol} (rebalanced) -> {'OK' if ok else 'FAILED'}")
            else:
                affordable = int(portfolio.cash // price) if price > 0 else 0
                if affordable > 0:
                    ok = portfolio.buy(symbol, affordable, price)
                    print(f"  BUY {affordable} x {symbol} (partial) -> {'OK' if ok else 'FAILED'}")
                else:
                    print(f"  Could not raise enough cash for {symbol}")

    print("\n" + portfolio.summary(current_prices))

    # Clean up test state
    import os
    if os.path.exists("test_portfolio.json"):
        os.remove("test_portfolio.json")
    print("\nTest complete (state file cleaned up).")


if __name__ == "__main__":
    main()
