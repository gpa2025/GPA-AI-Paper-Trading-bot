"""
Flask web dashboard for the AI Paper Trading Bot.

Endpoints:
  GET  /              — Dashboard UI
  GET  /api/state     — Full bot state as JSON
  POST /api/start     — Start the bot
  POST /api/stop      — Stop the bot
  POST /api/reset     — Reset portfolio (delete state file)
  GET  /api/screen    — Run screener and return results
"""

import os
import json
import logging
from flask import Flask, jsonify, request, send_from_directory

from web.bot_engine import BotEngine
from paper_trading.portfolio import PaperPortfolio
from db.database import TradingDatabase
import trading_config as cfg

logger = logging.getLogger("my_logger")

app = Flask(__name__, static_folder="static")
engine = BotEngine()


# ------------------------------------------------------------------ #
#  Dashboard UI
# ------------------------------------------------------------------ #

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# ------------------------------------------------------------------ #
#  API endpoints
# ------------------------------------------------------------------ #

@app.route("/api/state")
def api_state():
    return jsonify(engine.get_state())


@app.route("/api/prices", methods=["POST"])
def api_refresh_prices():
    """Fetch live prices for all held positions (works even when bot is stopped)."""
    engine.refresh_prices()
    return jsonify({"status": "ok", "prices": engine.current_prices})


@app.route("/api/start", methods=["POST"])
def api_start():
    engine.start()
    return jsonify({"status": "started"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    engine.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Stop bot and delete portfolio state file."""
    engine.stop()
    if os.path.exists(cfg.STATE_FILE):
        os.remove(cfg.STATE_FILE)
    # Reload a fresh portfolio (will re-seed from config)
    engine.portfolio = PaperPortfolio(
        starting_cash=cfg.STARTING_CASH,
        state_file=cfg.STATE_FILE,
        seed_positions=cfg.SEED_POSITIONS,
    )
    engine.cycle = 0
    engine.current_prices = {}
    engine.signals = {}
    engine.activity_log = []
    return jsonify({"status": "reset"})


@app.route("/api/screen")
def api_screen():
    """Run the screener on demand and return results."""
    from strategy.screener import StockScreener
    screener = StockScreener(
        sma_period=cfg.SCREENER_SMA_PERIOD,
        rsi_period=cfg.RSI_PERIOD,
        min_volume=cfg.SCREENER_MIN_VOLUME,
        history_period=cfg.SCREENER_HISTORY,
        excluded_symbols=set(cfg.EXCLUDED_SYMBOLS),
        sectors=cfg.SCREENER_SECTORS,
    )
    results = screener.screen(top_n=cfg.SCREENER_TOP_N)
    return jsonify(results)


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify({
        "watchlist_mode": cfg.WATCHLIST_MODE,
        "watchlist": cfg.WATCHLIST,
        "excluded_symbols": list(cfg.EXCLUDED_SYMBOLS),
        "default_trade_qty": cfg.TRADE_DOLLAR_AMOUNT,
        "max_position_size": cfg.MAX_POSITION_SIZE,
        "poll_interval_sec": cfg.POLL_INTERVAL_SEC,
        "starting_cash": cfg.STARTING_CASH,
        "enable_rebalance": cfg.ENABLE_REBALANCE,
        "rebalance_sell_priority": cfg.REBALANCE_SELL_PRIORITY,
        "short_sma_window": cfg.SHORT_SMA_WINDOW,
        "long_sma_window": cfg.LONG_SMA_WINDOW,
        "rsi_period": cfg.RSI_PERIOD,
        "rsi_overbought": cfg.RSI_OVERBOUGHT,
        "stop_loss_pct": cfg.STOP_LOSS_PCT,
        "take_profit_pct": cfg.TAKE_PROFIT_PCT,
        "enable_stop_loss": cfg.ENABLE_STOP_LOSS,
        "enable_take_profit": cfg.ENABLE_TAKE_PROFIT,
        "screener_top_n": cfg.SCREENER_TOP_N,
    })


@app.route("/api/exclude", methods=["POST"])
def api_toggle_exclude():
    """Toggle a symbol's protected/tradeable status."""
    body = request.get_json()
    if not body or "symbol" not in body:
        return jsonify({"error": "Missing 'symbol'"}), 400

    symbol = body["symbol"].upper()
    exclude = body.get("excluded", True)

    if exclude and symbol not in cfg.EXCLUDED_SYMBOLS:
        if isinstance(cfg.EXCLUDED_SYMBOLS, list):
            cfg.EXCLUDED_SYMBOLS.append(symbol)
        else:
            cfg.EXCLUDED_SYMBOLS = list(cfg.EXCLUDED_SYMBOLS) + [symbol]
    elif not exclude and symbol in cfg.EXCLUDED_SYMBOLS:
        if isinstance(cfg.EXCLUDED_SYMBOLS, list):
            cfg.EXCLUDED_SYMBOLS.remove(symbol)
        else:
            cfg.EXCLUDED_SYMBOLS = [s for s in cfg.EXCLUDED_SYMBOLS if s != symbol]

    logger.info("Symbol %s is now %s", symbol, "PROTECTED" if exclude else "TRADEABLE")

    # Persist the change
    engine._save_bot_state()

    return jsonify({
        "symbol": symbol,
        "excluded": exclude,
        "excluded_symbols": list(cfg.EXCLUDED_SYMBOLS),
    })


@app.route("/api/limits", methods=["GET"])
def api_get_limits():
    """Get all per-symbol trade limits (dollar amounts)."""
    return jsonify({
        "default_trade_qty": cfg.TRADE_DOLLAR_AMOUNT,
        "symbol_limits": cfg.SYMBOL_LIMITS,
    })


@app.route("/api/limits", methods=["POST"])
def api_set_limit():
    """
    Set per-symbol trade limits.
    Body: {"symbol": "AAPL", "max_buy": 5, "max_sell": 5}
    Use max_buy=0 and max_sell=0 to remove the limit (use default).
    """
    body = request.get_json()
    if not body or "symbol" not in body:
        return jsonify({"error": "Missing 'symbol'"}), 400

    symbol = body["symbol"].upper()
    max_buy = body.get("max_buy")
    max_sell = body.get("max_sell")

    if max_buy == 0 and max_sell == 0:
        # Remove custom limit — fall back to default
        cfg.SYMBOL_LIMITS.pop(symbol, None)
        logger.info("Removed trade limits for %s (using default: $%d)", symbol, cfg.TRADE_DOLLAR_AMOUNT)
    else:
        cfg.SYMBOL_LIMITS[symbol] = {
            "max_buy": max_buy if max_buy is not None else cfg.TRADE_DOLLAR_AMOUNT,
            "max_sell": max_sell if max_sell is not None else cfg.TRADE_DOLLAR_AMOUNT,
        }
        logger.info("Set trade limits for %s: buy=$%s, sell=$%s", symbol,
                     cfg.SYMBOL_LIMITS[symbol]["max_buy"], cfg.SYMBOL_LIMITS[symbol]["max_sell"])

    # Persist the change
    engine._save_bot_state()

    return jsonify({
        "symbol": symbol,
        "limits": cfg.SYMBOL_LIMITS.get(symbol),
        "default": cfg.DEFAULT_TRADE_QTY,
        "all_limits": cfg.SYMBOL_LIMITS,
    })


# ------------------------------------------------------------------ #
#  Strategy Parameters
# ------------------------------------------------------------------ #

@app.route("/api/strategy", methods=["GET"])
def api_get_strategy():
    """Get current strategy parameters."""
    return jsonify({
        "short_sma_window": cfg.SHORT_SMA_WINDOW,
        "long_sma_window": cfg.LONG_SMA_WINDOW,
        "rsi_period": cfg.RSI_PERIOD,
        "rsi_overbought": cfg.RSI_OVERBOUGHT,
        "rsi_oversold": cfg.RSI_OVERSOLD,
        "trade_dollar_amount": cfg.TRADE_DOLLAR_AMOUNT,
        "cooldown_hours": cfg.COOLDOWN_HOURS,
    })


@app.route("/api/catalysts/<symbol>")
def api_catalysts(symbol):
    """Get upcoming catalysts and recent news for a symbol."""
    import yfinance as yf

    symbol = symbol.upper()
    try:
        ticker = yf.Ticker(symbol)
        result = {"symbol": symbol, "news": [], "earnings": None}

        # News
        news = getattr(ticker, "news", None)
        if news:
            for item in news[:8]:
                content = item.get("content", item)
                title = content.get("title", "")
                link = content.get("canonicalUrl", {}).get("url", "") if isinstance(content.get("canonicalUrl"), dict) else content.get("link", "")
                publisher = content.get("provider", {}).get("displayName", "") if isinstance(content.get("provider"), dict) else content.get("publisher", "")
                summary = content.get("summary", "")
                if title:
                    result["news"].append({
                        "title": title,
                        "link": link,
                        "publisher": publisher,
                        "summary": summary,
                    })

        # Earnings dates
        try:
            cal = ticker.calendar
            if cal is not None and not (hasattr(cal, "empty") and cal.empty):
                if isinstance(cal, dict):
                    earnings_date = cal.get("Earnings Date")
                    if earnings_date:
                        if isinstance(earnings_date, list):
                            result["earnings"] = [str(d) for d in earnings_date]
                        else:
                            result["earnings"] = [str(earnings_date)]
                elif hasattr(cal, "to_dict"):
                    result["earnings_info"] = {str(k): str(v) for k, v in cal.items() if v is not None}
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({"symbol": symbol, "error": str(e)}), 500


@app.route("/api/candle-patterns")
def api_candle_patterns():
    """Get candlestick patterns for all held positions."""
    import yfinance as yf
    from strategy.signals import detect_candle_patterns

    symbols = request.args.get("symbols", "")
    if not symbols:
        return jsonify({})

    result = {}
    for sym in symbols.split(","):
        sym = sym.strip().upper()
        if not sym:
            continue
        try:
            hist = yf.Ticker(sym).history(period="3mo", interval="1d")
            if hist.empty or len(hist) < 15:
                result[sym] = []
                continue
            patterns = detect_candle_patterns(hist["Close"])
            result[sym] = patterns
        except Exception:
            result[sym] = []

    return jsonify(result)


@app.route("/api/signal-explain/<symbol>")
def api_signal_explain(symbol):
    """Get plain-English explanation of the current signal for a symbol."""
    import yfinance as yf
    from strategy.indicators import sma, rsi
    from strategy.signals import detect_candle_patterns

    symbol = symbol.upper()
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo", interval="1d")
        if hist.empty or len(hist) < cfg.LONG_SMA_WINDOW + 1:
            return jsonify({"symbol": symbol, "signal": "HOLD", "explanation": "Not enough data to generate a signal."})

        close = hist["Close"]
        short_sma_vals = sma(close, cfg.SHORT_SMA_WINDOW)
        long_sma_vals = sma(close, cfg.LONG_SMA_WINDOW)
        rsi_vals = rsi(close, cfg.RSI_PERIOD)

        cur_short = float(short_sma_vals.iloc[-1])
        prev_short = float(short_sma_vals.iloc[-2])
        cur_long = float(long_sma_vals.iloc[-1])
        prev_long = float(long_sma_vals.iloc[-2])
        cur_rsi = float(rsi_vals.iloc[-1])
        cur_price = float(close.iloc[-1])

        in_uptrend = cur_short > cur_long
        crossed_above = prev_short <= prev_long and cur_short > cur_long
        crossed_below = prev_short >= prev_long and cur_short < cur_long

        patterns = detect_candle_patterns(close)

        # Build explanation
        reasons = []
        signal = "HOLD"

        # Trend
        if in_uptrend:
            reasons.append(f"📈 **Uptrend**: Short SMA ({cfg.SHORT_SMA_WINDOW}-day) at ${cur_short:.2f} is above Long SMA ({cfg.LONG_SMA_WINDOW}-day) at ${cur_long:.2f}")
        else:
            reasons.append(f"📉 **Downtrend**: Short SMA ({cfg.SHORT_SMA_WINDOW}-day) at ${cur_short:.2f} is below Long SMA ({cfg.LONG_SMA_WINDOW}-day) at ${cur_long:.2f}")

        if crossed_above:
            reasons.append("⚡ **Fresh crossover UP** — short SMA just crossed above long SMA (bullish)")
        elif crossed_below:
            reasons.append("⚡ **Fresh crossover DOWN** — short SMA just crossed below long SMA (bearish)")

        # RSI
        if cur_rsi > cfg.RSI_OVERBOUGHT:
            reasons.append(f"🔴 **RSI Overbought**: RSI at {cur_rsi:.1f} (above {cfg.RSI_OVERBOUGHT}) — stock is overextended")
        elif cur_rsi < cfg.RSI_OVERSOLD:
            reasons.append(f"🟢 **RSI Oversold**: RSI at {cur_rsi:.1f} (below {cfg.RSI_OVERSOLD}) — potential bounce")
        elif 40 <= cur_rsi <= 65:
            reasons.append(f"✅ **RSI Sweet Spot**: RSI at {cur_rsi:.1f} — healthy momentum, good entry zone")
        else:
            reasons.append(f"⚪ **RSI Neutral**: RSI at {cur_rsi:.1f}")

        # Candle patterns
        if patterns:
            pattern_names = {"BULLISH_ENGULFING": "Bullish Engulfing 🟢", "BEARISH_ENGULFING": "Bearish Engulfing 🔴",
                           "HAMMER": "Hammer 🟢", "SHOOTING_STAR": "Shooting Star 🔴", "DOJI": "Doji ⚪",
                           "HEAD_AND_SHOULDERS": "Head & Shoulders 🔴 (bearish reversal)",
                           "INVERSE_HEAD_AND_SHOULDERS": "Inverse Head & Shoulders 🟢 (bullish reversal)"}
            for p in patterns:
                reasons.append(f"🕯️ **Candle Pattern**: {pattern_names.get(p, p)}")

        # Determine signal
        if crossed_below or cur_rsi > cfg.RSI_OVERBOUGHT:
            signal = "SELL"
        elif any(p in patterns for p in ["BEARISH_ENGULFING", "SHOOTING_STAR", "HEAD_AND_SHOULDERS"]) and not in_uptrend:
            signal = "SELL"
        elif in_uptrend and cur_rsi < cfg.RSI_OVERBOUGHT:
            if crossed_above or (40 <= cur_rsi <= 65) or any(p in patterns for p in ["BULLISH_ENGULFING", "HAMMER", "INVERSE_HEAD_AND_SHOULDERS"]):
                signal = "BUY"
        elif any(p in patterns for p in ["BULLISH_ENGULFING", "HAMMER", "INVERSE_HEAD_AND_SHOULDERS"]) and cur_rsi < cfg.RSI_OVERSOLD + 10:
            signal = "BUY"

        # Summary
        if signal == "BUY":
            summary = f"The bot would BUY {symbol} here. The daily chart shows an uptrend with healthy momentum."
        elif signal == "SELL":
            summary = f"The bot would SELL {symbol} here. Momentum is fading or the stock is overextended."
        else:
            summary = f"The bot is HOLDING {symbol}. No clear entry or exit signal on the daily chart."

        return jsonify({
            "symbol": symbol,
            "signal": signal,
            "price": cur_price,
            "summary": summary,
            "reasons": reasons,
            "indicators": {
                "sma_short": round(cur_short, 2),
                "sma_long": round(cur_long, 2),
                "rsi": round(cur_rsi, 2),
                "in_uptrend": in_uptrend,
                "candle_patterns": patterns,
            }
        })
    except Exception as e:
        return jsonify({"symbol": symbol, "signal": "HOLD", "explanation": str(e)}), 500


@app.route("/api/strategy", methods=["POST"])
def api_set_strategy():
    """Update strategy parameters at runtime."""
    body = request.get_json()
    if not body:
        return jsonify({"error": "No data"}), 400

    if "short_sma_window" in body:
        cfg.SHORT_SMA_WINDOW = int(body["short_sma_window"])
    if "long_sma_window" in body:
        cfg.LONG_SMA_WINDOW = int(body["long_sma_window"])
    if "rsi_period" in body:
        cfg.RSI_PERIOD = int(body["rsi_period"])
    if "rsi_overbought" in body:
        cfg.RSI_OVERBOUGHT = float(body["rsi_overbought"])
    if "rsi_oversold" in body:
        cfg.RSI_OVERSOLD = float(body["rsi_oversold"])
    if "trade_dollar_amount" in body:
        cfg.TRADE_DOLLAR_AMOUNT = int(body["trade_dollar_amount"])
    if "cooldown_hours" in body:
        cfg.COOLDOWN_HOURS = int(body["cooldown_hours"])

    # Update MIN_DATA_POINTS
    cfg.MIN_DATA_POINTS = cfg.LONG_SMA_WINDOW + 1

    # Re-create signal generator in the engine if running
    if engine.signal_gen:
        engine.signal_gen.short_window = cfg.SHORT_SMA_WINDOW
        engine.signal_gen.long_window = cfg.LONG_SMA_WINDOW
        engine.signal_gen.rsi_period = cfg.RSI_PERIOD
        engine.signal_gen.rsi_overbought = cfg.RSI_OVERBOUGHT
        engine.signal_gen.rsi_oversold = cfg.RSI_OVERSOLD

    logger.info("Strategy updated: SMA(%d/%d) RSI(%d) OB=%.0f OS=%.0f",
                cfg.SHORT_SMA_WINDOW, cfg.LONG_SMA_WINDOW, cfg.RSI_PERIOD,
                cfg.RSI_OVERBOUGHT, cfg.RSI_OVERSOLD)

    return jsonify({"status": "ok", "strategy": {
        "short_sma_window": cfg.SHORT_SMA_WINDOW,
        "long_sma_window": cfg.LONG_SMA_WINDOW,
        "rsi_period": cfg.RSI_PERIOD,
        "rsi_overbought": cfg.RSI_OVERBOUGHT,
        "rsi_oversold": cfg.RSI_OVERSOLD,
    }})


# ------------------------------------------------------------------ #
#  Exclusion Blocklist (ethical filtering)
# ------------------------------------------------------------------ #

BLOCKLIST_CATEGORIES = {
    "Defense Contractors": ["LMT", "RTX", "NOC", "GD", "LHX", "HII", "TDG", "TXT", "HEI"],
    "Weapons & Firearms": ["SWBI", "RGR", "AXON", "KTOS"],
    "Ammunition": ["POWW", "VSTO", "OLN"],
    "Military Technology": ["BWXT", "MRCY", "GE", "BA"],
    "Defense IT & Surveillance": ["LDOS", "SAIC", "BAH", "CACI", "PLTR"],
}


@app.route("/api/blocklist", methods=["GET"])
def api_get_blocklist():
    """Get exclusion categories and their tickers."""
    from strategy.screener import WEAPONS_DEFENSE_BLOCKLIST
    return jsonify({
        "categories": BLOCKLIST_CATEGORIES,
        "active_tickers": sorted(WEAPONS_DEFENSE_BLOCKLIST),
    })


@app.route("/api/blocklist", methods=["POST"])
def api_set_blocklist():
    """Add or remove tickers from the blocklist."""
    from strategy import screener
    body = request.get_json()
    if not body:
        return jsonify({"error": "No data"}), 400

    add = body.get("add", [])
    remove = body.get("remove", [])

    for ticker in add:
        screener.WEAPONS_DEFENSE_BLOCKLIST.add(ticker.upper())
    for ticker in remove:
        screener.WEAPONS_DEFENSE_BLOCKLIST.discard(ticker.upper())

    logger.info("Blocklist updated: added=%s removed=%s total=%d",
                add, remove, len(screener.WEAPONS_DEFENSE_BLOCKLIST))

    return jsonify({
        "active_tickers": sorted(screener.WEAPONS_DEFENSE_BLOCKLIST),
    })


# ------------------------------------------------------------------ #
#  E*TRADE Authentication (for real-time data)
# ------------------------------------------------------------------ #

@app.route("/api/etrade/status")
def api_etrade_status():
    """Check if E*TRADE session is authenticated."""
    from market.etrade_session import etrade_session
    return jsonify({
        "authenticated": etrade_session.is_authenticated,
        "base_url": etrade_session.base_url,
    })


@app.route("/api/etrade/auth/start", methods=["POST"])
def api_etrade_auth_start():
    """Start E*TRADE OAuth flow. Returns the auth URL to open."""
    from market.etrade_session import etrade_session
    try:
        body = request.get_json() or {}
        use_sandbox = body.get("sandbox", False)  # use production keys by default
        auth_url = etrade_session.start_auth(use_sandbox=use_sandbox)
        return jsonify({"auth_url": auth_url, "sandbox": use_sandbox})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/etrade/auth/complete", methods=["POST"])
def api_etrade_auth_complete():
    """Complete E*TRADE OAuth with the verification code."""
    from market.etrade_session import etrade_session
    body = request.get_json()
    if not body or "code" not in body:
        return jsonify({"error": "Missing 'code'"}), 400

    ok = etrade_session.complete_auth(body["code"])
    if ok:
        return jsonify({"status": "authenticated", "source": "etrade"})
    return jsonify({"error": "Authentication failed — check the code"}), 401


# ------------------------------------------------------------------ #
#  E*TRADE Account Sync
# ------------------------------------------------------------------ #

@app.route("/api/sync/status")
def api_sync_status():
    """Check if synced positions file exists."""
    synced_file = "synced_positions.json"
    if os.path.exists(synced_file):
        with open(synced_file, "r") as f:
            data = json.load(f)
        return jsonify({"synced": True, "data": data})
    return jsonify({"synced": False})


@app.route("/api/sync/load", methods=["POST"])
def api_sync_load():
    """
    Load synced positions into the paper portfolio.
    Reads from synced_positions.json (created by the CLI sync tool)
    and replaces the current paper portfolio.
    """
    synced_file = "synced_positions.json"
    if not os.path.exists(synced_file):
        return jsonify({"error": "No synced data found. Run: python -m accounts.sync"}), 404

    try:
        with open(synced_file, "r") as f:
            data = json.load(f)

        seed = data.get("seed_positions", {})
        cash = data.get("cash", 0.0)

        if not seed:
            return jsonify({"error": "No positions in synced data"}), 400

        # Stop bot if running
        engine.stop()

        # Delete old state and create new portfolio with synced positions
        if os.path.exists(cfg.STATE_FILE):
            os.remove(cfg.STATE_FILE)

        engine.portfolio = PaperPortfolio(
            starting_cash=cash,
            state_file=cfg.STATE_FILE,
            seed_positions=seed,
        )
        engine.cycle = 0
        engine.current_prices = {}
        engine.signals = {}

        # Fetch live prices for the new positions
        engine.refresh_prices()

        logger.info(
            "Loaded %d synced positions with $%.2f cash",
            len(seed), cash,
        )

        return jsonify({
            "status": "loaded",
            "positions": len(seed),
            "cash": cash,
            "symbols": list(seed.keys()),
        })

    except Exception as exc:
        logger.error("Failed to load synced positions: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sync/import", methods=["POST"])
def api_sync_import():
    """
    Import positions directly from a JSON payload.
    Useful for pasting positions manually or from another source.

    Expected body:
    {
      "positions": {"AAPL": {"qty": 50, "avg_cost": 170.00}, ...},
      "cash": 1000.00
    }
    """
    try:
        body = request.get_json()
        if not body or "positions" not in body:
            return jsonify({"error": "Missing 'positions' in request body"}), 400

        seed = body["positions"]
        cash = body.get("cash", 0.0)

        engine.stop()

        if os.path.exists(cfg.STATE_FILE):
            os.remove(cfg.STATE_FILE)

        engine.portfolio = PaperPortfolio(
            starting_cash=cash,
            state_file=cfg.STATE_FILE,
            seed_positions=seed,
        )
        engine.cycle = 0
        engine.current_prices = {}
        engine.signals = {}
        engine.refresh_prices()

        return jsonify({
            "status": "imported",
            "positions": len(seed),
            "cash": cash,
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ------------------------------------------------------------------ #
#  Historical data API (from SQLite database)
# ------------------------------------------------------------------ #

@app.route("/api/history/trades")
def api_history_trades():
    """Get trade history from the database."""
    limit = request.args.get("limit", 100, type=int)
    symbol = request.args.get("symbol")
    action = request.args.get("action")
    since = request.args.get("since")
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_trades(limit=limit, symbol=symbol, action=action, since=since))


@app.route("/api/history/trades/summary")
def api_trade_summary():
    """Get aggregate trade statistics."""
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_trade_summary())


@app.route("/api/history/trades/by-symbol")
def api_trades_by_symbol():
    """Get per-symbol trade statistics."""
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_symbol_trade_stats())


@app.route("/api/history/snapshots")
def api_history_snapshots():
    """Get portfolio snapshots for charting."""
    limit = request.args.get("limit", 500, type=int)
    since = request.args.get("since")
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_snapshots(limit=limit, since=since))


@app.route("/api/history/performance")
def api_performance():
    """Get overall portfolio performance (first vs latest snapshot)."""
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_portfolio_performance())


@app.route("/api/history/daily-pnl")
def api_daily_pnl():
    """Get daily P&L from snapshots."""
    days = request.args.get("days", 30, type=int)
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_daily_pnl(days=days))


@app.route("/api/history/prices/<symbol>")
def api_price_history(symbol):
    """Get price history for a symbol."""
    limit = request.args.get("limit", 500, type=int)
    since = request.args.get("since")
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_price_history(symbol.upper(), limit=limit, since=since))


@app.route("/api/chart/<symbol>")
def api_chart_data(symbol):
    """Get OHLC candle data with SMA and RSI overlays from Yahoo Finance."""
    import yfinance as yf
    import pandas as pd
    from strategy.indicators import sma, rsi

    period = request.args.get("period", "3mo")
    interval = request.args.get("interval", "1d")

    data = yf.download(symbol.upper(), period=period, interval=interval, progress=False)
    if data.empty:
        return jsonify({"error": "No data"}), 404

    # Flatten multi-level columns if present
    if hasattr(data.columns, 'levels'):
        data.columns = data.columns.get_level_values(0)

    close = data["Close"].squeeze()
    sma_short = sma(close, cfg.SHORT_SMA_WINDOW)
    sma_long = sma(close, cfg.LONG_SMA_WINDOW)
    rsi_values = rsi(close, cfg.RSI_PERIOD)

    candles = []
    for i, (idx, row) in enumerate(data.iterrows()):
        candles.append({
            "t": idx.strftime("%Y-%m-%d") if interval == "1d" else str(idx),
            "o": round(float(row["Open"]), 2),
            "h": round(float(row["High"]), 2),
            "l": round(float(row["Low"]), 2),
            "c": round(float(row["Close"]), 2),
            "sma_short": round(float(sma_short.iloc[i]), 2) if pd.notna(sma_short.iloc[i]) else None,
            "sma_long": round(float(sma_long.iloc[i]), 2) if pd.notna(sma_long.iloc[i]) else None,
            "rsi": round(float(rsi_values.iloc[i]), 2) if pd.notna(rsi_values.iloc[i]) else None,
        })

    return jsonify({
        "symbol": symbol.upper(),
        "interval": interval,
        "sma_short_window": cfg.SHORT_SMA_WINDOW,
        "sma_long_window": cfg.LONG_SMA_WINDOW,
        "rsi_period": cfg.RSI_PERIOD,
        "rsi_overbought": cfg.RSI_OVERBOUGHT,
        "candles": candles,
    })


@app.route("/api/history/prices")
def api_tracked_symbols():
    """Get all symbols with price history."""
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_tracked_symbols())


@app.route("/api/history/screens")
def api_screen_history():
    """Get historical screener runs."""
    limit = request.args.get("limit", 50, type=int)
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_screens(limit=limit))


@app.route("/api/history/activity")
def api_activity_history():
    """Get activity log from database."""
    limit = request.args.get("limit", 200, type=int)
    action = request.args.get("action")
    since = request.args.get("since")
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_activity(limit=limit, action=action, since=since))


@app.route("/api/history/syncs")
def api_sync_history():
    """Get E*TRADE sync history."""
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_syncs())


@app.route("/api/db/stats")
def api_db_stats():
    """Get database statistics."""
    db = TradingDatabase(cfg.DB_FILE)
    return jsonify(db.get_db_stats())


def create_app():
    """Factory for the Flask app."""
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("my_logger")
    if not log.handlers:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler("python_client.log", maxBytes=5*1024*1024, backupCount=3)
        fmt = logging.Formatter("%(asctime)-15s %(message)s", datefmt="%m/%d/%Y %I:%M:%S %p")
        fh.setFormatter(fmt)
        log.addHandler(fh)
        log.setLevel(logging.DEBUG)
    return app


if __name__ == "__main__":
    create_app().run(debug=False, host="127.0.0.1", port=5000)
