"""
Market data provider — E*TRADE real-time quotes with Yahoo Finance fallback.

When an authenticated E*TRADE session is available, uses the E*TRADE
quote API for real-time prices (bid/ask/last from your broker).
Falls back to Yahoo Finance when E*TRADE is not authenticated or fails.
Historical data always comes from Yahoo Finance (E*TRADE's history API
is limited).
"""

import json
import logging
import pandas as pd
import yfinance as yf
from market.etrade_session import etrade_session

logger = logging.getLogger("my_logger")


class MarketData:
    """Fetches market data from E*TRADE (primary) or Yahoo Finance (fallback)."""

    def __init__(self):
        self._history: dict[str, list[float]] = {}

    @property
    def source(self) -> str:
        """Return which data source is active."""
        return "etrade" if etrade_session.is_authenticated else "yahoo"

    # ------------------------------------------------------------------ #
    #  E*TRADE quote API
    # ------------------------------------------------------------------ #

    def _etrade_quote(self, symbol: str) -> dict | None:
        """Fetch a quote from E*TRADE's real-time API."""
        if not etrade_session.is_authenticated:
            return None

        url = f"{etrade_session.base_url}/v1/market/quote/{symbol}.json"
        try:
            response = etrade_session.get(url)
            if response is None or response.status_code != 200:
                logger.debug("E*TRADE quote failed for %s (status=%s)",
                             symbol, getattr(response, "status_code", "N/A"))
                return None

            data = response.json()
            quote = data["QuoteResponse"]["QuoteData"][0]
            all_data = quote.get("All", {})

            return {
                "symbol": quote["Product"]["symbol"],
                "last_price": all_data.get("lastTrade"),
                "bid": all_data.get("bid"),
                "ask": all_data.get("ask"),
                "bid_size": all_data.get("bidSize"),
                "ask_size": all_data.get("askSize"),
                "volume": all_data.get("totalVolume"),
                "change": all_data.get("changeClose"),
                "change_pct": all_data.get("changeClosePercentage"),
                "high": all_data.get("high"),
                "low": all_data.get("low"),
                "open": all_data.get("open"),
                "previous_close": all_data.get("previousClose"),
                "source": "etrade",
            }
        except (KeyError, IndexError, TypeError) as exc:
            logger.debug("E*TRADE quote parse error for %s: %s", symbol, exc)
            return None
        except Exception as exc:
            logger.error("E*TRADE quote request error for %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------ #
    #  Yahoo Finance fallback
    # ------------------------------------------------------------------ #

    def _yahoo_quote(self, symbol: str) -> dict | None:
        """Fetch a quote from Yahoo Finance (fallback)."""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info

            last_price = info.get("lastPrice") or info.get("regularMarketPrice")
            prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")

            if last_price is None:
                return None

            change = (last_price - prev_close) if prev_close else None
            change_pct = (change / prev_close * 100) if prev_close and change else None

            return {
                "symbol": symbol,
                "last_price": last_price,
                "bid": info.get("bid"),
                "ask": info.get("ask"),
                "bid_size": None,
                "ask_size": None,
                "volume": info.get("lastVolume") or info.get("regularMarketVolume"),
                "change": change,
                "change_pct": change_pct,
                "high": info.get("dayHigh") or info.get("regularMarketDayHigh"),
                "low": info.get("dayLow") or info.get("regularMarketDayLow"),
                "open": None,
                "previous_close": prev_close,
                "source": "yahoo",
            }
        except Exception as exc:
            logger.error("Yahoo Finance quote failed for %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------ #
    #  Public API (tries E*TRADE first, falls back to Yahoo)
    # ------------------------------------------------------------------ #

    def get_quote(self, symbol: str) -> dict | None:
        """
        Fetch a real-time quote. Tries E*TRADE first, then Yahoo Finance.
        """
        # Try E*TRADE
        quote = self._etrade_quote(symbol)
        if quote and quote.get("last_price") is not None:
            return quote

        # Fallback to Yahoo
        quote = self._yahoo_quote(symbol)
        if quote:
            logger.debug("Using Yahoo fallback for %s", symbol)
        return quote

    def get_price(self, symbol: str) -> float | None:
        """Return just the last trade price."""
        quote = self.get_quote(symbol)
        return quote["last_price"] if quote else None

    def fetch_history(self, symbol: str, period: str = "1mo", interval: str = "1d") -> pd.Series:
        """
        Fetch historical closing prices (always from Yahoo Finance).
        E*TRADE's history API is limited, so Yahoo is used for this.
        """
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period, interval=interval)
            if hist.empty:
                logger.warning("No history returned for %s", symbol)
                return pd.Series(dtype=float)
            closes = hist["Close"]
            self._history[symbol] = closes.tolist()
            logger.info("Fetched %d historical bars for %s", len(closes), symbol)
            return closes
        except Exception as exc:
            logger.error("History fetch failed for %s: %s", symbol, exc)
            return pd.Series(dtype=float)

    def record_price(self, symbol: str, price: float):
        """Append a price to the in-memory history."""
        self._history.setdefault(symbol, []).append(price)

    def get_price_history(self, symbol: str) -> pd.Series:
        """Return the recorded price history as a pandas Series."""
        return pd.Series(self._history.get(symbol, []), dtype=float)

    def get_multiple_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch quotes for multiple symbols."""
        results = {}
        for sym in symbols:
            quote = self.get_quote(sym)
            if quote:
                results[sym] = quote
        return results
