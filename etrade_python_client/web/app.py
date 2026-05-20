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
        "default_trade_qty": cfg.DEFAULT_TRADE_QTY,
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
    """Get all per-symbol trade limits."""
    return jsonify({
        "default_trade_qty": cfg.DEFAULT_TRADE_QTY,
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
        logger.info("Removed trade limits for %s (using default: %d)", symbol, cfg.DEFAULT_TRADE_QTY)
    else:
        cfg.SYMBOL_LIMITS[symbol] = {
            "max_buy": max_buy if max_buy is not None else cfg.DEFAULT_TRADE_QTY,
            "max_sell": max_sell if max_sell is not None else cfg.DEFAULT_TRADE_QTY,
        }
        logger.info("Set trade limits for %s: buy=%s, sell=%s", symbol,
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
        use_sandbox = body.get("sandbox", True)  # default to sandbox until prod keys work
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
