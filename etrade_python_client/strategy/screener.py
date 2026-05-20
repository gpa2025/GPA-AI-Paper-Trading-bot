"""
Stock screener — selects trending stocks from a diverse, ethically filtered pool.

The screener:
  1. Maintains a broad universe of stocks across multiple sectors
  2. Excludes weapons manufacturers and defense contractors
  3. Scores each stock by momentum (price relative to SMA + RSI trend)
  4. Returns the top N candidates ranked by trend strength

This replaces the static WATCHLIST with a dynamic, AI-driven selection.
"""

import logging
import pandas as pd
import yfinance as yf
from strategy.indicators import sma, rsi

logger = logging.getLogger("my_logger")

# ---------------------------------------------------------------------------
#  Diverse stock universe — broad sector coverage
# ---------------------------------------------------------------------------
STOCK_UNIVERSE = {
    # Technology
    "tech": [
        "AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMD", "CRM", "ADBE",
        "ORCL", "INTC", "CSCO", "QCOM", "TXN", "NOW", "SHOP",
        "PLTR", "SNOW", "NET", "UBER",
    ],
    # Healthcare & Biotech
    "healthcare": [
        "JNJ", "UNH", "PFE", "ABBV", "MRK", "TMO", "ABT", "LLY",
        "AMGN", "GILD", "ISRG", "DXCM", "VEEV", "ZTS", "MRNA",
    ],
    # Consumer / Retail
    "consumer": [
        "AMZN", "TSLA", "HD", "NKE", "SBUX", "MCD", "COST", "TGT",
        "LOW", "TJX", "LULU", "DPZ", "CMG", "ABNB", "BKNG",
    ],
    # Finance
    "finance": [
        "JPM", "BAC", "GS", "MS", "V", "MA", "AXP", "BLK",
        "SCHW", "C", "WFC", "SPGI", "ICE", "CME",
    ],
    # Energy (clean + traditional, no weapons)
    "energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "ENPH", "SEDG",
        "FSLR", "NEE", "AES", "BEP",
    ],
    # Industrials (non-defense)
    "industrials": [
        "CAT", "DE", "UNP", "UPS", "FDX", "HON", "EMR", "ETN",
        "WM", "RSG", "FAST", "URI",
    ],
    # Communication & Media
    "communication": [
        "DIS", "NFLX", "CMCSA", "T", "VZ", "TMUS", "SPOT", "ROKU",
    ],
    # Real Estate (REITs)
    "real_estate": [
        "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "DLR", "PSA",
    ],
    # Materials & Agriculture
    "materials": [
        "LIN", "APD", "ECL", "NEM", "FCX", "NTR", "MOS", "CF",
    ],
}

# ---------------------------------------------------------------------------
#  Weapons & defense exclusion list
#  These companies derive significant revenue from weapons manufacturing,
#  defense contracting, or military systems. They are never traded.
# ---------------------------------------------------------------------------
WEAPONS_DEFENSE_BLOCKLIST = {
    # Major defense contractors
    "LMT",   # Lockheed Martin
    "RTX",   # RTX (Raytheon)
    "NOC",   # Northrop Grumman
    "BA",    # Boeing (military division)
    "GD",    # General Dynamics
    "LHX",   # L3Harris Technologies
    "HII",   # Huntington Ingalls Industries
    "TDG",   # TransDigm Group
    "TXT",   # Textron (Bell helicopters, weapons systems)
    "HEI",   # HEICO (defense electronics)
    "KTOS",  # Kratos Defense
    "BWXT",  # BWX Technologies (nuclear weapons components)
    "AXON",  # Axon Enterprise (weapons/tasers)
    "SWBI",  # Smith & Wesson Brands
    "RGR",   # Sturm Ruger (firearms)
    "POWW",  # AMMO Inc (ammunition)
    "VSTO",  # Vista Outdoor (ammunition)
    "OLN",   # Olin Corporation (ammunition)
    "GE",    # GE Aerospace (military engines)
    "LDOS",  # Leidos (defense IT)
    "SAIC",  # Science Applications International (defense)
    "BAH",   # Booz Allen Hamilton (defense consulting)
    "CACI",  # CACI International (defense IT)
    "MRCY",  # Mercury Systems (defense electronics)
    "PLTR",  # Palantir (military surveillance — also in tech, removed here)
}


# ---------------------------------------------------------------------------
#  Screener
# ---------------------------------------------------------------------------
class StockScreener:
    """Screens and ranks stocks by trend strength from a diverse universe."""

    def __init__(
        self,
        sma_period: int = 50,
        rsi_period: int = 14,
        min_volume: int = 500_000,
        history_period: str = "3mo",
        excluded_symbols: set[str] | None = None,
        sectors: list[str] | None = None,
    ):
        """
        :param sma_period: SMA period for trend scoring
        :param rsi_period: RSI period for momentum scoring
        :param min_volume: minimum average daily volume filter
        :param history_period: yfinance history period for screening
        :param excluded_symbols: additional symbols to exclude (user's exclusion list)
        :param sectors: list of sector keys to include (None = all sectors)
        """
        self.sma_period = sma_period
        self.rsi_period = rsi_period
        self.min_volume = min_volume
        self.history_period = history_period
        self.excluded = (excluded_symbols or set()) | WEAPONS_DEFENSE_BLOCKLIST
        self.sectors = sectors

    def get_universe(self) -> list[str]:
        """Return the full filtered stock universe."""
        symbols = []
        for sector, tickers in STOCK_UNIVERSE.items():
            if self.sectors and sector not in self.sectors:
                continue
            for ticker in tickers:
                if ticker not in self.excluded and ticker not in symbols:
                    symbols.append(ticker)
        return symbols

    def screen(self, top_n: int = 10) -> list[dict]:
        """
        Screen the universe and return the top N trending stocks.

        Each result is a dict with:
          symbol, sector, price, sma, rsi, trend_score,
          above_sma (bool), volume

        Trend score = (price / SMA - 1) * 100 + RSI_bonus
          - Stocks above their SMA with RSI between 40-70 score highest
            (strong uptrend, not yet overbought)
          - Stocks below SMA or with extreme RSI score lower

        Returns results sorted by trend_score descending.
        """
        universe = self.get_universe()
        logger.info("Screening %d stocks across %d sectors...",
                     len(universe), len(STOCK_UNIVERSE))

        results = []

        # Process in batches to avoid hammering the API
        batch_size = 20
        for i in range(0, len(universe), batch_size):
            batch = universe[i:i + batch_size]
            batch_str = " ".join(batch)

            try:
                data = yf.download(
                    batch_str,
                    period=self.history_period,
                    interval="1d",
                    progress=False,
                    threads=True,
                )
            except Exception as exc:
                logger.error("yfinance download failed for batch: %s", exc)
                continue

            for symbol in batch:
                try:
                    if len(batch) == 1:
                        closes = data["Close"]
                        volumes = data["Volume"]
                    else:
                        closes = data["Close"][symbol]
                        volumes = data["Volume"][symbol]

                    closes = closes.dropna()
                    volumes = volumes.dropna()

                    if len(closes) < self.sma_period:
                        continue

                    avg_vol = volumes.tail(20).mean()
                    if avg_vol < self.min_volume:
                        continue

                    sma_values = sma(closes, self.sma_period)
                    rsi_values = rsi(closes, self.rsi_period)

                    current_price = closes.iloc[-1]
                    current_sma = sma_values.iloc[-1]
                    current_rsi = rsi_values.iloc[-1]

                    if pd.isna(current_sma) or pd.isna(current_rsi):
                        continue

                    # Trend score calculation
                    sma_pct = (current_price / current_sma - 1) * 100

                    # RSI bonus: reward stocks in the 40-70 sweet spot
                    if 40 <= current_rsi <= 70:
                        rsi_bonus = 10
                    elif 30 <= current_rsi < 40:
                        rsi_bonus = 5   # oversold, potential bounce
                    elif current_rsi > 70:
                        rsi_bonus = -5  # overbought, risky
                    else:
                        rsi_bonus = -10  # deeply oversold, avoid

                    trend_score = sma_pct + rsi_bonus

                    # Find which sector this stock belongs to
                    stock_sector = "unknown"
                    for sec, tickers in STOCK_UNIVERSE.items():
                        if symbol in tickers:
                            stock_sector = sec
                            break

                    results.append({
                        "symbol": symbol,
                        "sector": stock_sector,
                        "price": round(float(current_price), 2),
                        "sma": round(float(current_sma), 2),
                        "rsi": round(float(current_rsi), 2),
                        "trend_score": round(trend_score, 2),
                        "above_sma": bool(current_price > current_sma),
                        "volume": int(avg_vol),
                    })

                except Exception as exc:
                    logger.debug("Skipping %s: %s", symbol, exc)
                    continue

        # Sort by trend score descending
        results.sort(key=lambda r: r["trend_score"], reverse=True)

        # Ensure sector diversity in top picks
        diverse_picks = self._diversify(results, top_n)

        logger.info(
            "Screener found %d candidates, selected top %d (diverse):",
            len(results), len(diverse_picks),
        )
        for pick in diverse_picks:
            logger.info(
                "  %s (%s) score=%.1f price=$%.2f rsi=%.1f",
                pick["symbol"], pick["sector"], pick["trend_score"],
                pick["price"], pick["rsi"],
            )

        return diverse_picks

    @staticmethod
    def _diversify(results: list[dict], top_n: int) -> list[dict]:
        """
        Select top_n stocks ensuring no single sector dominates.

        Algorithm:
          - Max 2 stocks per sector in the final picks
          - Fill remaining slots with next-best from any sector
        """
        max_per_sector = max(2, top_n // 4)
        sector_counts: dict[str, int] = {}
        picks = []

        # First pass: pick top stocks respecting sector caps
        for stock in results:
            if len(picks) >= top_n:
                break
            sector = stock["sector"]
            if sector_counts.get(sector, 0) >= max_per_sector:
                continue
            picks.append(stock)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        # Second pass: if we still need more, relax sector caps
        if len(picks) < top_n:
            picked_symbols = {p["symbol"] for p in picks}
            for stock in results:
                if len(picks) >= top_n:
                    break
                if stock["symbol"] not in picked_symbols:
                    picks.append(stock)

        return picks


def print_screening_results(results: list[dict]):
    """Pretty-print screening results to console."""
    if not results:
        print("  No stocks passed the screening criteria.")
        return

    print(f"\n  {'Rank':<5} {'Symbol':<7} {'Sector':<15} {'Price':>10} "
          f"{'SMA':>10} {'RSI':>7} {'Score':>8} {'Trend':<8}")
    print("  " + "-" * 75)
    for i, stock in enumerate(results, 1):
        trend = "▲ UP" if stock["above_sma"] else "▼ DOWN"
        print(f"  {i:<5} {stock['symbol']:<7} {stock['sector']:<15} "
              f"${stock['price']:>9,.2f} ${stock['sma']:>9,.2f} "
              f"{stock['rsi']:>6.1f} {stock['trend_score']:>+7.1f} {trend:<8}")
