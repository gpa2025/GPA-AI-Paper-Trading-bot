"""
Trading signal generation using technical indicators.

Combines SMA trend + RSI to produce BUY / SELL / HOLD signals.
This is a momentum strategy intended for paper-trading experiments only.

Strategy rules:
  BUY  when short SMA is above long SMA AND RSI is in the 40-65 range
       (established uptrend, not overbought — good entry zone)
  SELL when short SMA crosses below long SMA OR RSI > overbought threshold
  HOLD otherwise
"""

import logging
import pandas as pd
from strategy.indicators import sma, rsi

logger = logging.getLogger("my_logger")

# Signal constants
SIGNAL_BUY = "BUY"
SIGNAL_SELL = "SELL"
SIGNAL_HOLD = "HOLD"


class SignalGenerator:
    """Generates trading signals from a price history DataFrame."""

    def __init__(
        self,
        short_window: int = 10,
        long_window: int = 30,
        rsi_period: int = 14,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
    ):
        self.short_window = short_window
        self.long_window = long_window
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold

    def generate(self, prices: pd.Series) -> str:
        """
        Analyse a price series and return the latest signal.

        :param prices: pd.Series of closing prices (oldest first).
        :return: one of SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD
        """
        if len(prices) < self.long_window + 1:
            return SIGNAL_HOLD

        short_sma = sma(prices, self.short_window)
        long_sma = sma(prices, self.long_window)
        rsi_values = rsi(prices, self.rsi_period)

        current_short = short_sma.iloc[-1]
        previous_short = short_sma.iloc[-2]
        current_long = long_sma.iloc[-1]
        previous_long = long_sma.iloc[-2]
        current_rsi = rsi_values.iloc[-1]

        # Trend state
        in_uptrend = current_short > current_long
        crossed_above = previous_short <= previous_long and current_short > current_long
        crossed_below = previous_short >= previous_long and current_short < current_long

        logger.info(
            "Indicators — Short SMA: %.2f, Long SMA: %.2f, RSI: %.2f, Uptrend: %s",
            current_short, current_long, current_rsi, in_uptrend,
        )

        # SELL: downward crossover or overbought
        if crossed_below:
            logger.info("Signal: SELL (SMA crossover down)")
            return SIGNAL_SELL

        if current_rsi > self.rsi_overbought:
            logger.info("Signal: SELL (RSI overbought: %.2f)", current_rsi)
            return SIGNAL_SELL

        # BUY: in uptrend with RSI in a good entry zone (not overbought)
        if in_uptrend and current_rsi < self.rsi_overbought:
            # Fresh crossover = strong buy
            if crossed_above:
                logger.info("Signal: BUY (fresh SMA crossover, RSI=%.2f)", current_rsi)
                return SIGNAL_BUY

            # Established uptrend with RSI in sweet spot (40-65)
            if 40 <= current_rsi <= 65:
                logger.info("Signal: BUY (uptrend + RSI sweet spot: %.2f)", current_rsi)
                return SIGNAL_BUY

            # Uptrend but RSI is getting high (65-70) — hold, don't chase
            logger.info("Signal: HOLD (uptrend but RSI elevated: %.2f)", current_rsi)
            return SIGNAL_HOLD

        # Downtrend or RSI too low — hold
        return SIGNAL_HOLD
