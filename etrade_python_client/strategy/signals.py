"""
Trading signal generation using technical indicators + candlestick patterns.

Combines SMA trend + RSI + candlestick patterns to produce BUY / SELL / HOLD signals.
This is a momentum strategy intended for paper-trading experiments only.

Strategy rules:
  BUY  when short SMA is above long SMA AND RSI is in the 40-65 range
       Candlestick confirmation (bullish engulfing, hammer) strengthens the signal
  SELL when short SMA crosses below long SMA OR RSI > overbought threshold
       Candlestick confirmation (bearish engulfing, shooting star) strengthens the signal
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

# Candlestick pattern constants
PATTERN_NONE = "NONE"
PATTERN_BULLISH_ENGULFING = "BULLISH_ENGULFING"
PATTERN_BEARISH_ENGULFING = "BEARISH_ENGULFING"
PATTERN_HAMMER = "HAMMER"
PATTERN_SHOOTING_STAR = "SHOOTING_STAR"
PATTERN_DOJI = "DOJI"
PATTERN_HEAD_AND_SHOULDERS = "HEAD_AND_SHOULDERS"
PATTERN_INVERSE_HEAD_AND_SHOULDERS = "INVERSE_HEAD_AND_SHOULDERS"


def detect_head_and_shoulders(prices: pd.Series) -> list[str]:
    """
    Detect Head and Shoulders (bearish reversal) and Inverse H&S (bullish reversal).
    Scans the last 20 daily bars for local peaks/troughs forming the pattern.
    """
    if len(prices) < 15:
        return []

    patterns = []
    data = prices.iloc[-20:].values if len(prices) >= 20 else prices.values
    n = len(data)

    # Find local peaks (highs) and troughs (lows) using a window of 2
    peaks = []
    troughs = []
    for i in range(2, n - 2):
        if data[i] > data[i-1] and data[i] > data[i-2] and data[i] > data[i+1] and data[i] > data[i+2]:
            peaks.append((i, data[i]))
        if data[i] < data[i-1] and data[i] < data[i-2] and data[i] < data[i+1] and data[i] < data[i+2]:
            troughs.append((i, data[i]))

    # Head and Shoulders: 3 peaks where middle is highest, shoulders roughly equal
    if len(peaks) >= 3:
        for i in range(len(peaks) - 2):
            left, head, right = peaks[i], peaks[i+1], peaks[i+2]
            if head[1] > left[1] and head[1] > right[1]:
                # Shoulders within 3% of each other
                shoulder_diff = abs(left[1] - right[1]) / max(left[1], right[1])
                # Head at least 1.5% above shoulders
                head_above = (head[1] - max(left[1], right[1])) / head[1]
                if shoulder_diff < 0.03 and head_above > 0.015:
                    # Confirm: current price is near or below neckline
                    neckline = min(left[1], right[1])
                    if data[-1] <= neckline * 1.01:
                        patterns.append(PATTERN_HEAD_AND_SHOULDERS)
                        break

    # Inverse Head and Shoulders: 3 troughs where middle is lowest
    if len(troughs) >= 3:
        for i in range(len(troughs) - 2):
            left, head, right = troughs[i], troughs[i+1], troughs[i+2]
            if head[1] < left[1] and head[1] < right[1]:
                shoulder_diff = abs(left[1] - right[1]) / max(left[1], right[1])
                head_below = (min(left[1], right[1]) - head[1]) / head[1]
                if shoulder_diff < 0.03 and head_below > 0.015:
                    neckline = max(left[1], right[1])
                    if data[-1] >= neckline * 0.99:
                        patterns.append(PATTERN_INVERSE_HEAD_AND_SHOULDERS)
                        break

    return patterns


def detect_candle_patterns(prices: pd.Series) -> list[str]:
    """
    Detect candlestick patterns from the last few prices.
    Requires at least 3 data points. Uses close prices to infer OHLC-like behavior.
    Returns list of detected patterns.
    """
    if len(prices) < 3:
        return []

    patterns = []
    # Use last 3 closes to approximate candle bodies
    p3, p2, p1 = float(prices.iloc[-3]), float(prices.iloc[-2]), float(prices.iloc[-1])

    body_prev = p2 - p3  # previous candle body (positive = bullish)
    body_curr = p1 - p2  # current candle body

    avg_price = (p1 + p2 + p3) / 3
    body_threshold = avg_price * 0.001  # doji threshold: 0.1% of price

    # Doji: very small body
    if abs(body_curr) < body_threshold:
        patterns.append(PATTERN_DOJI)

    # Bullish engulfing: previous bearish, current bullish and larger
    if body_prev < 0 and body_curr > 0 and abs(body_curr) > abs(body_prev):
        patterns.append(PATTERN_BULLISH_ENGULFING)

    # Bearish engulfing: previous bullish, current bearish and larger
    if body_prev > 0 and body_curr < 0 and abs(body_curr) > abs(body_prev):
        patterns.append(PATTERN_BEARISH_ENGULFING)

    # Hammer: downtrend then small body with recovery (p3 > p2, p1 > p2)
    if p3 > p2 and p1 > p2 and body_curr > 0:
        patterns.append(PATTERN_HAMMER)

    # Shooting star: uptrend then reversal (p3 < p2, p1 < p2)
    if p3 < p2 and p1 < p2 and body_curr < 0:
        patterns.append(PATTERN_SHOOTING_STAR)

    # Head and Shoulders (multi-bar pattern)
    patterns.extend(detect_head_and_shoulders(prices))

    return patterns


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

        # Candlestick patterns
        candle_patterns = detect_candle_patterns(prices)
        bullish_candle = any(p in candle_patterns for p in [PATTERN_BULLISH_ENGULFING, PATTERN_HAMMER, PATTERN_INVERSE_HEAD_AND_SHOULDERS])
        bearish_candle = any(p in candle_patterns for p in [PATTERN_BEARISH_ENGULFING, PATTERN_SHOOTING_STAR, PATTERN_HEAD_AND_SHOULDERS])

        logger.info(
            "Indicators — Short SMA: %.2f, Long SMA: %.2f, RSI: %.2f, Uptrend: %s, Candles: %s",
            current_short, current_long, current_rsi, in_uptrend,
            candle_patterns if candle_patterns else "none",
        )

        # SELL: downward crossover or overbought
        if crossed_below:
            logger.info("Signal: SELL (SMA crossover down)")
            return SIGNAL_SELL

        if current_rsi > self.rsi_overbought:
            logger.info("Signal: SELL (RSI overbought: %.2f)", current_rsi)
            return SIGNAL_SELL

        # Bearish candle in a weakening trend = SELL
        if bearish_candle and not in_uptrend:
            logger.info("Signal: SELL (bearish candle + downtrend)")
            return SIGNAL_SELL

        # BUY: in uptrend with RSI in a good entry zone (not overbought)
        if in_uptrend and current_rsi < self.rsi_overbought:
            # Fresh crossover = strong buy
            if crossed_above:
                logger.info("Signal: BUY (fresh SMA crossover, RSI=%.2f)", current_rsi)
                return SIGNAL_BUY

            # Bullish candle confirmation in uptrend = BUY (wider RSI range)
            if bullish_candle and current_rsi < self.rsi_overbought:
                logger.info("Signal: BUY (bullish candle + uptrend, RSI=%.2f)", current_rsi)
                return SIGNAL_BUY

            # Established uptrend with RSI in sweet spot (40-65)
            if 40 <= current_rsi <= 65:
                logger.info("Signal: BUY (uptrend + RSI sweet spot: %.2f)", current_rsi)
                return SIGNAL_BUY

            # Uptrend but RSI is getting high (65-70) — hold, don't chase
            logger.info("Signal: HOLD (uptrend but RSI elevated: %.2f)", current_rsi)
            return SIGNAL_HOLD

        # Bullish candle in neutral zone with RSI oversold = BUY opportunity
        if bullish_candle and current_rsi < self.rsi_oversold + 10:
            logger.info("Signal: BUY (bullish candle + RSI near oversold: %.2f)", current_rsi)
            return SIGNAL_BUY

        # Downtrend or RSI too low — hold
        return SIGNAL_HOLD
