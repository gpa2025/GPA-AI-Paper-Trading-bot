"""
Central configuration for the AI paper-trading bot.

Edit the values below to tune the bot's behavior.
Import this module from trading_bot.py or any other entry point.
"""

# ---------------------------------------------------------------------------
#  Watchlist mode
# ---------------------------------------------------------------------------
#  "static"  — trade only the symbols in WATCHLIST below
#  "screener" — auto-select trending stocks from a diverse universe
WATCHLIST_MODE = "screener"

# Static watchlist (used when WATCHLIST_MODE = "static")
WATCHLIST = ["AAPL", "MSFT", "GOOGL"]

# ---------------------------------------------------------------------------
#  Screener settings (used when WATCHLIST_MODE = "screener")
# ---------------------------------------------------------------------------
SCREENER_TOP_N = 8          # how many stocks to pick from the universe
SCREENER_SMA_PERIOD = 50    # SMA period for trend scoring
SCREENER_MIN_VOLUME = 500_000  # minimum avg daily volume
SCREENER_HISTORY = "3mo"    # look-back period for screening
SCREENER_RERUN_CYCLES = 30  # re-screen every N cycles to refresh picks
# Sectors to include (None = all). Options:
#   "tech", "healthcare", "consumer", "finance", "energy",
#   "industrials", "communication", "real_estate", "materials"
SCREENER_SECTORS = None     # None means all sectors

# ---------------------------------------------------------------------------
#  Excluded symbols — the AI will NEVER buy or sell these tickers.
#  Use this to protect positions you manage manually.
# ---------------------------------------------------------------------------
EXCLUDED_SYMBOLS = ["RIVN", "LCID"]  # <-- add your protected tickers here

# ---------------------------------------------------------------------------
#  Per-symbol trade limits (dollar amounts)
#  Controls the max dollar amount the AI can buy or sell in a single trade.
#  Symbols not listed here use TRADE_DOLLAR_AMOUNT.
#
#  Format:  "SYMBOL": {"max_buy": $, "max_sell": $}
# ---------------------------------------------------------------------------
SYMBOL_LIMITS = {
    "QS": {"max_buy": 500, "max_sell": 1000},
}

# ---------------------------------------------------------------------------
#  Order sizing (global caps)
# ---------------------------------------------------------------------------
MAX_POSITION_SIZE = 100     # max total shares to hold in any single symbol
MAX_PORTFOLIO_PCT = 0.15    # max % of portfolio value in one position
TRADE_DOLLAR_AMOUNT = 1000  # target dollar amount per trade
DEFAULT_TRADE_QTY = 10      # legacy fallback for web dashboard/test scripts

# ---------------------------------------------------------------------------
#  Cooldown — prevent re-trading the same symbol too quickly
# ---------------------------------------------------------------------------
COOLDOWN_HOURS = 24         # hours to wait after a trade before trading same symbol again

# ---------------------------------------------------------------------------
#  Timing
# ---------------------------------------------------------------------------
POLL_INTERVAL_SEC = 900     # seconds between market-data polls (15 minutes)

# ---------------------------------------------------------------------------
#  Paper account
# ---------------------------------------------------------------------------
STARTING_CASH = 0.00        # start with $0 cash — fund buys by selling
STATE_FILE = "paper_portfolio.json"
DB_FILE = "trading_bot.db"  # SQLite database for historical data

# ---------------------------------------------------------------------------
#  Rebalancing — sell existing positions to fund new buys
# ---------------------------------------------------------------------------
#  When ENABLE_REBALANCE is True and the bot gets a BUY signal but has
#  no cash, it will look for positions with a SELL or HOLD signal and
#  sell shares from the weakest one to raise funds.
#
#  REBALANCE_SELL_PRIORITY controls which positions get sold first:
#    "weakest_signal" — sell positions that have a SELL signal first,
#                       then HOLD, never sell positions with a BUY signal
#    "largest_loss"   — sell the position with the biggest unrealised loss
#    "largest_value"  — sell the position with the highest market value
# ---------------------------------------------------------------------------
ENABLE_REBALANCE = True
REBALANCE_SELL_PRIORITY = "weakest_signal"  # "weakest_signal", "largest_loss", "largest_value"

# ---------------------------------------------------------------------------
#  Seed portfolio — pre-load positions to mirror your real account.
#  These are added on first run (when no state file exists yet).
#  Set to {} to start with cash only.
#
#  Format:  "SYMBOL": {"qty": shares, "avg_cost": price_per_share}
# ---------------------------------------------------------------------------
SEED_POSITIONS = {
    # Add your real holdings here to mirror your brokerage account.
    # These are loaded on first run only (when no paper_portfolio.json exists).
    # To re-seed, delete paper_portfolio.json and restart.
    #
    # "AAPL":  {"qty": 50, "avg_cost": 170.00},
    # "MSFT":  {"qty": 20, "avg_cost": 380.00},
    # "GOOGL": {"qty": 10, "avg_cost": 140.00},
    # "RIVN":  {"qty": 100, "avg_cost": 15.00},   # protected by EXCLUDED_SYMBOLS
    # "LCID":  {"qty": 200, "avg_cost": 5.00},    # protected by EXCLUDED_SYMBOLS
}

# ---------------------------------------------------------------------------
#  Strategy: SMA Crossover + RSI
# ---------------------------------------------------------------------------
SHORT_SMA_WINDOW = 10       # fast moving average period
LONG_SMA_WINDOW = 30        # slow moving average period
RSI_PERIOD = 14             # RSI look-back period
RSI_OVERBOUGHT = 70.0       # sell when RSI exceeds this
RSI_OVERSOLD = 30.0         # (informational — buy logic uses crossover)

# Minimum data points before the strategy starts generating signals
MIN_DATA_POINTS = LONG_SMA_WINDOW + 1

# ---------------------------------------------------------------------------
#  Risk management
# ---------------------------------------------------------------------------
STOP_LOSS_PCT = 0.05        # sell if position drops 5% from avg cost
TAKE_PROFIT_PCT = 0.10      # sell if position gains 10% from avg cost
ENABLE_STOP_LOSS = True
ENABLE_TAKE_PROFIT = True
